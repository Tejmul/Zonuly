"""Company-registry hygiene: the one-off cleanup of what the old admission let in, and the
hourly dedup that keeps it clean.

What it does, in order, to every row that is not on a hand-picked list (`list_key` rows
are never touched — the ★ list is the decision):

  1. NAME       re-clean headline names ("Swiss startup Jaipur Robotics" -> "Jaipur
                Robotics"); a name that is not a company at all is quarantined
  2. WEBSITE    clear a website on a board/form/shortener host, and an apply-link host
                shared by rows it does not belong to (exa.ai on 68 rows)
  3. FUNDING    the stage into the one vocabulary; card totals read; a revenue line
                misread as a round dropped; bot-wall descriptions cleared
  4. MERGE      rows with the same name key, or the same real domain, become one: jobs,
                contacts, emails and events move to the survivor; the loser is kept,
                marked `merged` with `merged_into`
  5. QUARANTINE bare rows nothing can rescue — no site, no roles, no people, no evidence
                to search with (or the website search already failed three times)
  6. REGRADE    overrated + priority + city (targeting.refresh_priority)

Nothing is deleted. Quarantine and merge are a status, reversible by hand. `plan()` reads
only; `apply()` writes in small commits so the hourly tick's writers are never blocked
for long.

Layering: pipeline layer (admission, targeting, backoff, db).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import func, text
from sqlmodel import select

from jobhunter import admission, backoff
from jobhunter.db import Company, Contact, Email, Job, get_session, init_db

log = logging.getLogger(__name__)

# where a row's name came from a headline or link text, judging by its source tags
_HEADLINE_SOURCES = ("funding-rss", "funding-news", "scrapedo-dir", "x")
_TRUSTED_TAGS = ("[yc]", "[exa]")
_FILL = ("website", "domain", "github_org", "ats", "ats_slug", "description", "story", "story_evidence",
         "hq_region", "funding_stage", "funding_amount_usd_m", "funding_total_usd_m", "funding_announced",
         "funding_evidence", "funding_investors", "valuation_usd_m", "valuation_evidence", "team_size",
         "careers_url", "hiring_status", "hiring_evidence", "hiring_claim", "hiring_claim_url",
         "hiring_claim_by", "hiring_claim_source", "hiring_claim_at", "email_pattern", "city")


@dataclass
class Plan:
    renames: list[tuple[int, str, str]] = field(default_factory=list)          # id, old, new
    quarantine: list[tuple[int, str, str]] = field(default_factory=list)       # id, name, why
    merges: list[tuple[int, int, str]] = field(default_factory=list)           # loser, survivor, why
    websites_cleared: list[tuple[int, str, str]] = field(default_factory=list) # id, host, why
    stages: list[tuple[int, str | None, str | None]] = field(default_factory=list)
    funding: list[tuple[int, str, object, object]] = field(default_factory=list)  # id, field, old, new
    descriptions_cleared: list[tuple[int, str]] = field(default_factory=list)
    name_keys: int = 0
    overrated: list[tuple[int, str, str]] = field(default_factory=list)
    moved: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    untouched_list_rows: int = 0
    total: int = 0

    def summary(self, samples: int = 6) -> dict:
        def s(rows):
            return [" | ".join(str(x) for x in r) for r in rows[:samples]]

        why_q: dict[str, int] = defaultdict(int)
        for _, _, why in self.quarantine:
            why_q[why.split(":")[0].split(" — ")[0]] += 1
        why_o: dict[str, int] = defaultdict(int)
        for _, _, why in self.overrated:
            why_o[why.split(" (")[0].split(" — ")[0].split(":")[0]] += 1
        return {
            "companies": self.total,
            "list_rows_untouched": self.untouched_list_rows,
            "name_keys_to_write": self.name_keys,
            "renames": {"count": len(self.renames), "samples": s(self.renames)},
            "quarantine": {"count": len(self.quarantine), "by_reason": dict(why_q), "samples": s(self.quarantine)},
            "merges": {"count": len(self.merges), "samples": s(self.merges), "rows_moved": dict(self.moved)},
            "websites_cleared": {"count": len(self.websites_cleared), "samples": s(self.websites_cleared)},
            "stages_normalised": {"count": len(self.stages), "samples": s(self.stages),
                                  "after": _count(new for _, _, new in self.stages)},
            "funding_repaired": {"count": len(self.funding), "samples": s(self.funding)},
            "bot_wall_descriptions": {"count": len(self.descriptions_cleared), "samples": s(self.descriptions_cleared)},
            "overrated": {"count": len(self.overrated), "by_reason": dict(why_o), "samples": s(self.overrated)},
        }


def _count(values) -> dict:
    out: dict = defaultdict(int)
    for v in values:
        out[v or "null"] += 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def _source_of(c: Company) -> tuple[str, bool]:
    """(source, headline?) for a row, from its claim source and its notes' tags."""
    notes = c.notes or ""
    src = c.hiring_claim_source or ""
    headline = src in _HEADLINE_SOURCES or any(f"[{t}]" in notes for t in _HEADLINE_SOURCES)
    if src in ("yc", "ats") or any(t in notes for t in _TRUSTED_TAGS) or c.ats:
        return ("ats" if (src == "ats" or c.ats) else "yc"), False
    return src or "unknown", headline


def _weight(c: Company, jobs: dict, people: dict) -> tuple:
    """Survivor order: most roles + people, has a site, most fields filled, oldest."""
    filled = sum(1 for f in _FILL if getattr(c, f, None))
    return (-(jobs.get(c.id, 0) + people.get(c.id, 0)), 0 if c.website else 1, -filled, c.id)


def plan(session) -> Plan:
    """Everything apply() would do, computed from a read. Writes nothing."""
    from jobhunter.enrich import bot_wall

    p = Plan()
    rows = session.exec(select(Company)).all()
    p.total = len(rows)
    jobs = dict(session.exec(select(Job.company_id, func.count()).group_by(Job.company_id)).all())
    people = dict(session.exec(select(Contact.company_id, func.count()).group_by(Contact.company_id)).all())
    emails = dict(session.exec(select(Email.company_id, func.count()).group_by(Email.company_id)).all())

    active = [c for c in rows if c.status is None]
    listed = [c for c in active if c.list_key]
    p.untouched_list_rows = len(listed)
    list_keys = {admission.name_key(c.name): c for c in listed}
    work = [c for c in active if not c.list_key]

    # 1. names
    final_name: dict[int, str] = {}
    quarantined: set[int] = set()
    for c in work:
        src, headline = _source_of(c)
        name, how = admission.clean_name(c.name, headline=headline, source=src)
        if name is None:
            if jobs.get(c.id) or people.get(c.id) or emails.get(c.id):
                # a junk name with real data behind it is renamed by hand, not hidden
                final_name[c.id] = c.name
                continue
            p.quarantine.append((c.id, c.name, f"junk name: {how}"))
            quarantined.add(c.id)
            continue
        final_name[c.id] = name
        if name != c.name:
            p.renames.append((c.id, c.name, name))
        if c.name_key != admission.name_key(name):
            p.name_keys += 1

    # 2. websites: bogus hosts, and shared hosts that do not carry the row's name
    by_host: dict[str, list[Company]] = defaultdict(list)
    site: dict[int, str | None] = {}
    for c in work:
        if c.id in quarantined:
            continue
        host = admission.host_of(c.website) or c.domain
        site[c.id] = host
        if not host:
            continue
        if admission.bogus_host(host, c.name):
            p.websites_cleared.append((c.id, host, "board/form/shortener/press host"))
            site[c.id] = None
            continue
        by_host[admission.registrable(host) or host].append(c)
    for host, group in by_host.items():
        if len(group) < 2:
            continue
        for c in group:
            if not admission.host_fits_name(final_name[c.id], host):
                p.websites_cleared.append((c.id, host, f"shared by {len(group)} rows, not theirs"))
                site[c.id] = None

    # 3. funding + descriptions
    for c in work:
        if c.id in quarantined:
            continue
        f = admission.funding_facts(stage=c.funding_stage, amount_usd_m=c.funding_amount_usd_m,
                                    total_usd_m=c.funding_total_usd_m, announced=c.funding_announced,
                                    valuation_usd_m=c.valuation_usd_m, evidence=c.funding_evidence)
        if f["stage"] != c.funding_stage and (c.funding_stage or f["stage"]):
            p.stages.append((c.id, c.funding_stage, f["stage"]))
        if (f["amount_usd_m"] or None) != (c.funding_amount_usd_m or None):
            p.funding.append((c.id, "funding_amount_usd_m", c.funding_amount_usd_m, f["amount_usd_m"]))
        if f["total_usd_m"] and f["total_usd_m"] != c.funding_total_usd_m:
            p.funding.append((c.id, "funding_total_usd_m", c.funding_total_usd_m, f["total_usd_m"]))
        if f["announced"] and f["announced"] != c.funding_announced:
            p.funding.append((c.id, "funding_announced", c.funding_announced, f["announced"]))
        if c.description and bot_wall(c.description):
            p.descriptions_cleared.append((c.id, c.description[:60]))

    # 4. merges: same name key, or the same real domain carrying the name
    groups: dict[str, list[Company]] = defaultdict(list)
    for c in work:
        if c.id not in quarantined:
            groups[admission.name_key(final_name[c.id])].append(c)
    parent: dict[int, int] = {}
    for key, group in groups.items():
        if not key:
            continue
        if key in list_keys:
            for c in group:
                if jobs.get(c.id) or people.get(c.id) or emails.get(c.id):
                    continue   # has data: left for a person to fold into the list row
                p.quarantine.append((c.id, c.name, f"duplicate of list row #{list_keys[key].id} {list_keys[key].name}"))
                quarantined.add(c.id)
            continue
        if len(group) < 2:
            continue
        group.sort(key=lambda c: _weight(c, jobs, people))
        head = group[0]
        head_site = admission.registrable(site.get(head.id)) if site.get(head.id) else None
        for loser in group[1:]:
            loser_site = admission.registrable(site.get(loser.id)) if site.get(loser.id) else None
            if head_site and loser_site and head_site != loser_site:
                continue   # same name, different real sites: two companies (there are two Hives)
            head_site = head_site or loser_site
            parent[loser.id] = head.id
            p.merges.append((loser.id, head.id, f"same name key '{key}': {loser.name} -> {head.name}"))
    for host, group in by_host.items():
        fits = [c for c in group if site.get(c.id) and c.id not in quarantined and c.id not in parent
                and admission.host_fits_name(final_name[c.id], host)]
        if len(fits) < 2:
            continue
        fits.sort(key=lambda c: _weight(c, jobs, people))
        for loser in fits[1:]:
            if loser.id in parent or fits[0].id in parent:
                continue
            parent[loser.id] = fits[0].id
            p.merges.append((loser.id, fits[0].id, f"same domain {host}: {loser.name} -> {fits[0].name}"))
    for loser, survivor in parent.items():
        p.moved["jobs"] += jobs.get(loser, 0)
        p.moved["contacts"] += people.get(loser, 0)
        p.moved["emails"] += emails.get(loser, 0)

    # 5. bare and unrescuable
    for c in work:
        if c.id in quarantined or c.id in parent:
            continue
        has_site = site.get(c.id) or c.github_org
        has_data = jobs.get(c.id) or people.get(c.id) or emails.get(c.id) or any(
            jobs.get(l, 0) + people.get(l, 0) for l, s in parent.items() if s == c.id)
        if has_site or has_data:
            continue
        evidence = c.description or c.funding_evidence or c.hiring_claim_url or c.story
        failed = backoff.tries(c, "website") >= 3
        if not evidence or failed:
            why = "website search failed 3 times" if failed else "no site, roles, people or anything to search with"
            p.quarantine.append((c.id, c.name, f"bare and unrescuable: {why}"))
            quarantined.add(c.id)

    # 6. overrated, as the regrade will see it (with the repaired funding)
    for c in active:
        if c.id in quarantined or c.id in parent:
            continue
        f = admission.funding_facts(stage=c.funding_stage, amount_usd_m=c.funding_amount_usd_m,
                                    total_usd_m=c.funding_total_usd_m, announced=c.funding_announced,
                                    valuation_usd_m=c.valuation_usd_m, evidence=c.funding_evidence)
        o = admission.overrated_verdict(
            final_name.get(c.id, c.name), stage=f["stage"], amount_usd_m=f["amount_usd_m"],
            total_usd_m=f["total_usd_m"], valuation_usd_m=f["valuation_usd_m"], evidence=c.funding_evidence,
            hiring_verified=c.hiring_status == "verified", open_roles=jobs.get(c.id, 0), pay_band=c.pay_power_band)
        if o.overrated:
            p.overrated.append((c.id, c.name, o.reason + (" [list row: kept on its list]" if c.list_key else "")))
    p._site = site                 # type: ignore[attr-defined]
    p._final = final_name          # type: ignore[attr-defined]
    p._parent = parent             # type: ignore[attr-defined]
    return p


def _merge(session, loser: Company, survivor: Company, why: str) -> None:
    """Move everything that points at the loser onto the survivor, fill the survivor's
    blanks from the loser, and mark the loser merged. Never deletes the loser."""
    for table, column in (("job", "company_id"), ("contact", "company_id"), ("email", "company_id"),
                          ("event", "company_id")):
        session.exec(text(f'UPDATE "{table}" SET {column} = :s WHERE {column} = :l').bindparams(s=survivor.id, l=loser.id))
    session.exec(text('UPDATE "job" SET company_name = :n WHERE company_id = :s').bindparams(n=survivor.name, s=survivor.id))
    for f in _FILL:
        if getattr(survivor, f, None) in (None, "") and getattr(loser, f, None) not in (None, ""):
            setattr(survivor, f, getattr(loser, f))
    if loser.contacts_found_at and not survivor.contacts_found_at:
        survivor.contacts_found_at = loser.contacts_found_at
    loser.status, loser.merged_into = "merged", survivor.id
    loser.status_reason = why[:300]
    loser.tier, loser.tier_reason, loser.priority_score = "reject", f"merged into #{survivor.id} {survivor.name}", 0
    loser.name_key = admission.name_key(survivor.name)
    session.add(survivor)
    session.add(loser)


def apply(p: Plan, *, batch: int = 100) -> dict:
    """Carry out a plan. Commits every `batch` changes so a concurrent tick waits seconds,
    not minutes. Then regrades overrated + priority + city for every row."""
    from jobhunter import targeting

    done = defaultdict(int)
    final = p._final    # type: ignore[attr-defined]
    quarantine = {cid: why for cid, _, why in p.quarantine}
    clear_sites = {cid for cid, _, _ in p.websites_cleared}
    stages = {cid: new for cid, _, new in p.stages}
    fund = defaultdict(dict)
    for cid, f, _, new in p.funding:
        fund[cid][f] = new
    bot = {cid for cid, _ in p.descriptions_cleared}

    with get_session() as session:
        n = 0

        def tick() -> None:
            nonlocal n
            n += 1
            if n % batch == 0:
                session.commit()

        # merges first (renames could otherwise collide with a loser's exact name)
        for loser_id, survivor_id, why in p.merges:
            loser, survivor = session.get(Company, loser_id), session.get(Company, survivor_id)
            if loser is None or survivor is None or loser.list_key or survivor.list_key:
                continue
            _merge(session, loser, survivor, why)
            if final.get(survivor.id) == loser.name:
                # the survivor is about to take this exact name; the loser steps aside
                loser.name = f"{loser.name} [merged into #{survivor.id}]"
                session.add(loser)
            done["merged"] += 1
            tick()
        session.commit()
        for c in session.exec(select(Company).where(Company.list_key.is_(None))).all():  # type: ignore[union-attr]
            if c.status == "merged":
                continue
            if c.id in quarantine:
                c.status, c.status_reason = "quarantined", quarantine[c.id][:300]
                c.tier, c.tier_reason, c.priority_score = "reject", f"quarantined: {quarantine[c.id]}"[:300], 0
                done["quarantined"] += 1
            else:
                new = final.get(c.id)
                if new and new != c.name:
                    clash = session.exec(select(Company).where(Company.name == new, Company.id != c.id)).first()
                    if clash is None:
                        c.notes = f"{c.notes}\n[hygiene] renamed from '{c.name}'".strip() if c.notes else f"[hygiene] renamed from '{c.name}'"
                        session.exec(text('UPDATE "job" SET company_name = :n WHERE company_id = :i').bindparams(n=new, i=c.id))
                        c.name = new
                        done["renamed"] += 1
                if c.id in clear_sites:
                    c.website = c.domain = None
                    done["websites_cleared"] += 1
                if c.id in stages:
                    c.funding_stage = stages[c.id]
                    done["stages"] += 1
                for f, v in fund.get(c.id, {}).items():
                    setattr(c, f, v)
                    done["funding"] += 1
                if c.id in bot:
                    c.description = None
                    done["descriptions_cleared"] += 1
            key = admission.name_key(c.name)
            if c.name_key != key:
                c.name_key = key
                done["name_keys"] += 1
            session.add(c)
            tick()
        session.commit()
    done["regrade"] = targeting.refresh_priority()
    log.info("hygiene applied: %s", dict(done))
    return dict(done)


def run(*, dry_run: bool = True) -> dict:
    """The cleanup. dry_run (the default) reads and reports; it writes nothing."""
    init_db()
    with get_session() as session:
        p = plan(session)
        session.rollback()
    out = {"dry_run": dry_run, "plan": p.summary()}
    if not dry_run:
        out["applied"] = apply(p)
    return out


def backfill_keys() -> int:
    """Write name_key where it is missing — the cheap part, safe to run every hour."""
    init_db()
    n = 0
    with get_session() as session:
        for c in session.exec(select(Company).where(Company.name_key.is_(None))).all():  # type: ignore[union-attr]
            c.name_key = admission.name_key(c.name)
            session.add(c)
            n += 1
            if n % 200 == 0:
                session.commit()
        session.commit()
    return n


__all__ = ["plan", "apply", "run", "backfill_keys", "Plan"]


def mark_empty(*, dry_run: bool = False) -> dict:
    """Flag every active, not-overrated company we hold nothing about as `scrape_first`,
    and clear the flag on the ones that now have a site and either people or roles.
    Never hides or removes a row — it only moves it to the front of the fill-in queues."""
    init_db()
    with get_session() as session:
        with_jobs = set(session.exec(select(Job.company_id).distinct()).all())
        with_people = set(session.exec(select(Contact.company_id).distinct()).all())
        rows = session.exec(select(Company).where(Company.status.is_(None))).all()  # type: ignore[union-attr]
        flagged = cleared = 0
        for c in rows:
            has_site = bool(c.website or c.domain)
            empty = (not c.overrated and not has_site and not (c.description or "").strip()
                     and c.id not in with_jobs and c.id not in with_people)
            filled = has_site and (c.id in with_people or c.id in with_jobs)
            if empty and not c.scrape_first:
                c.scrape_first = True
                flagged += 1
            elif c.scrape_first and (filled or c.overrated):
                c.scrape_first = False
                cleared += 1
            else:
                continue
            if not dry_run:
                session.add(c)
        if not dry_run:
            session.commit()
        total = sum(1 for c in rows if c.scrape_first)
    out = {"flagged": flagged, "cleared": cleared, "scrape_first_now": total, "dry_run": dry_run}
    log.info("mark_empty: %s", out)
    return out
