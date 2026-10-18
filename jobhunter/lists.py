"""Hand-picked company lists — "★ India" and whatever comes next.

A list is a YAML file in data/lists/<key>.yaml that says, in the user's words, which
companies to chase and why. The database mirrors it: every company on a list carries
`list_key`, `list_rank`, `city` and `list_meta` (the list's own row as JSON), and the
dashboard shows the list on its own page, in the list's order, with nothing else on it.

The list is the decision. Grading still runs on these companies — tier, pay power,
hiring proof — but it orders them; it never drops one. Everything the list says about a
company (stage, funding, CTC range) is shown as *the list's* claim, never as a scored
fact, and nothing here overwrites a field the pipeline already read from a source.

    python scripts/run.py lists show india-star
    python scripts/run.py lists sync india-star      # create/attach rows, verify websites
    python scripts/run.py lists people india-star    # roles + referrers at every company
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import timedelta
from pathlib import Path

import httpx
import yaml
from sqlmodel import col, select

from jobhunter import ROOT
from jobhunter.db import Company, Contact, Job, get_session, init_db, utcnow

log = logging.getLogger(__name__)

LISTS_DIR = ROOT / "data" / "lists"


# ------------------------------------------------------------------ the files

def available() -> list[dict]:
    """Every list on disk, with how many of its companies are attached in the database."""
    init_db()
    out = []
    for path in sorted(LISTS_DIR.glob("*.yaml")):
        spec = _read(path)
        with get_session() as session:
            attached = session.exec(
                select(Company.id).where(Company.list_key == spec["key"])
            ).all()
        out.append({
            "key": spec["key"], "name": spec.get("name") or spec["key"], "title": spec.get("title"),
            "description": (spec.get("description") or "").strip(), "source": spec.get("source"),
            "companies": len(spec["companies"]), "attached": len(attached),
        })
    return out


def _read(path: Path) -> dict:
    spec = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not spec.get("key") or not isinstance(spec.get("companies"), list):
        raise ValueError(f"{path.name}: a list needs `key:` and `companies:`")
    return spec


def load(key: str) -> dict:
    path = LISTS_DIR / f"{key}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"no list '{key}' — expected {path}")
    return _read(path)


# ------------------------------------------------------------------ website check
#
# A guessed website is only written once the page has been fetched and names the company.
# "plazza.in" could be anything; a domain that says "Plazza" on its own front page is
# theirs. A fail leaves the field null — the row then says "no website" rather than
# pointing every scraper at a stranger.

def _stem(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


async def _check_site(http: httpx.AsyncClient, entry: dict) -> tuple[int, str | None, str]:
    url = entry.get("website")
    if not url:
        return entry["rank"], None, entry.get("note") or "no website in the list"
    if entry.get("website_verified"):
        return entry["rank"], url, "verified by hand (site blocks bots)"
    try:
        r = await http.get(url)
    except Exception as e:  # noqa: BLE001 — one dead site must not stop the sync
        return entry["rank"], None, f"unreachable: {type(e).__name__}"
    if r.status_code >= 400:
        return entry["rank"], None, f"HTTP {r.status_code}"
    text = re.sub(r"<[^>]+>", " ", r.text[:400_000]).lower()
    stems = [_stem(entry["name"])] + [_stem(a) for a in entry.get("aliases") or []]
    stems = [s for s in stems if len(s) >= 3]
    page = re.sub(r"[^a-z0-9]", "", text)
    if any(s in page for s in stems):
        return entry["rank"], str(r.url).rstrip("/"), "named on their own page"
    return entry["rank"], None, "page does not name the company"


async def _check_sites(entries: list[dict]) -> dict[int, tuple[str | None, str]]:
    from jobhunter.scrapers.base import gather_limited, make_client

    async with make_client() as http:
        results = await gather_limited([_check_site(http, e) for e in entries], limit=8)
    out: dict[int, tuple[str | None, str]] = {}
    for item in results:
        if item:
            rank, url, note = item
            out[rank] = (url, note)
    return out


# ------------------------------------------------------------------ sync

def _find(session, entry: dict) -> Company | None:
    """The list's name, then its aliases, then the harvest's normalized-name match."""
    from jobhunter.harvest import _find_company

    for name in [entry["name"], *(entry.get("aliases") or [])]:
        row = session.exec(select(Company).where(Company.name == name)).first()
        if row:
            return row
    return _find_company(session, entry["name"])


def sync(key: str, *, verify_sites: bool = True) -> dict:
    """Attach every company on the list to a database row — creating what is missing,
    filling only the blanks on what exists — and detach rows the list no longer names."""
    from jobhunter.pipeline import _domain_of

    init_db()
    spec = load(key)
    entries = spec["companies"]
    sites = asyncio.run(_check_sites(entries)) if verify_sites else {}

    stats = {"list": key, "companies": len(entries), "created": 0, "attached": 0, "website_ok": 0,
             "website_missing": [], "detached": 0}
    with get_session() as session:
        seen: set[int] = set()
        for entry in entries:
            row = _find(session, entry)
            if row is None:
                row = Company(name=entry["name"])
                stats["created"] += 1
            else:
                stats["attached"] += 1

            site, note = sites.get(entry["rank"], (entry.get("website"), "not checked"))
            if site:
                stats["website_ok"] += 1
            else:
                stats["website_missing"].append(f"{entry['name']}: {note}")

            # the list owns the name: a row attached through an alias ("India's Ringg",
            # as a scraper spelled it) takes the name the list gives it
            if row.name != entry["name"] and row.name in (entry.get("aliases") or []):
                row.name = entry["name"]
            row.list_key = key
            row.list_rank = int(entry["rank"])
            row.city = entry.get("city") or row.city
            meta = {k: v for k, v in entry.items() if k not in ("aliases",)}
            meta["website_check"] = note
            row.list_meta = json.dumps(meta, ensure_ascii=False)

            # fill the blanks only — a field the pipeline read from a source stays
            row.website = row.website or site
            row.domain = row.domain or _domain_of(row.website)
            row.careers_url = row.careers_url or entry.get("careers")
            row.hq_region = row.hq_region or "india"
            row.description = row.description or entry.get("what")
            from jobhunter.admission import normalize_stage

            stage = normalize_stage(entry.get("stage"))   # "Series A / YC" -> "Series A"
            if stage and not row.funding_stage:
                row.funding_stage = stage
            if entry.get("funding_usd_m") and not row.funding_amount_usd_m:
                row.funding_amount_usd_m = float(entry["funding_usd_m"])
            if not row.funding_evidence and entry.get("funding"):
                # the list's name, not its source file: this line is shown to people
                row.funding_evidence = f"{spec.get('name') or key} list (hand-picked): {entry.get('stage')} · {entry['funding']}"
            tag = f"[list:{key}] #{entry['rank']}"
            if tag not in (row.notes or ""):
                row.notes = f"{row.notes}\n{tag}".strip() if row.notes else tag
            session.add(row)
            session.flush()
            seen.add(row.id)

        # a company the list dropped is no longer on it — the row itself stays
        for row in session.exec(select(Company).where(Company.list_key == key)).all():
            if row.id not in seen:
                row.list_key, row.list_rank, row.list_meta = None, None, None
                session.add(row)
                stats["detached"] += 1
        session.commit()

    log.info("lists sync: %s", {k: v for k, v in stats.items() if k != "website_missing"})
    return stats


# ------------------------------------------------------------------ the rows

_BANDS = {"pays": 0, "deep_pockets": 1, "funded": 2, "thin": 3, "unknown": 4}


def rows(key: str) -> dict:
    """The list as the dashboard shows it: the header, then every company in list order
    with what the database knows — roles, people, who to ask first, hiring proof."""
    from jobhunter.contacts import roles as roleclass
    from jobhunter.network import _reachable

    init_db()
    spec = load(key)
    with get_session() as session:
        companies = session.exec(
            select(Company).where(Company.list_key == key).order_by(col(Company.list_rank))
        ).all()
        # 2026-10-19 (Tejmul): a list with `include_cities` also shows every company in
        # those cities that clears the guardrails — active (or under the pay bar, shown
        # collapsed as before), not a reject, not overrated, not do-not-contact. The
        # list's own rows keep their rank and come first.
        cities = [x.lower() for x in spec.get("include_cities") or []]
        if cities:
            more = [c for c in session.exec(select(Company).where(
                        col(Company.list_key).is_(None), col(Company.merged_into).is_(None),
                        col(Company.status).is_(None) | (Company.status == "below_pay_bar"))).all()
                    if any(x in (c.city or "").lower() for x in cities)
                    and c.tier != "reject" and not c.overrated and not c.do_not_contact]
            more.sort(key=lambda c: (0 if c.complete else 1, _BANDS.get(c.pay_power_band or "", 9),
                                     -(c.priority_score or 0), c.id))
            companies = list(companies) + more
        ids = [c.id for c in companies]
        jobs = session.exec(select(Job).where(col(Job.company_id).in_(ids))).all() if ids else []
        people = session.exec(select(Contact).where(col(Contact.company_id).in_(ids))).all() if ids else []

    jobs_by: dict[int, list[Job]] = {}
    for j in jobs:
        jobs_by.setdefault(j.company_id, []).append(j)
    people_by: dict[int, list[Contact]] = {}
    for p in people:
        people_by.setdefault(p.company_id, []).append(p)

    from jobhunter import india

    out: list[dict] = []
    below: list[dict] = []     # under the India ₹20L floor (india.py): shown collapsed, not chased
    for c in companies:
        meta = json.loads(c.list_meta) if c.list_meta else {}
        cjobs = jobs_by.get(c.id, [])
        cpeople = sorted(people_by.get(c.id, []), key=lambda p: (p.referral_rank or 9))
        reachable = _reachable(c, cpeople)
        best = reachable[0] if reachable else None
        fresher = [j for j in cjobs if not j.is_senior]
        (below if c.status == "below_pay_bar" else out).append({
            "id": c.id, "rank": c.list_rank, "name": c.name, "city": c.city,
            "listed": c.list_key == key,
            "what": c.description or meta.get("what"),
            "website": c.website, "careers_url": c.careers_url,
            "website_note": None if c.website else meta.get("website_check"),
            # the list's own claims, labelled as such
            "stage": meta.get("stage"), "funding": meta.get("funding"), "size": meta.get("size"),
            "ctc": meta.get("ctc"), "ctc_kind": meta.get("ctc_kind"), "category": meta.get("category"),
            # what the pipeline has found since
            "tier": c.tier, "hiring_status": c.hiring_status, "hiring_evidence": c.hiring_evidence,
            "ats": c.ats, "email_pattern": c.email_pattern,
            "roles": {
                "total": len(cjobs), "fresher": len(fresher),
                "titles": [j.title for j in sorted(cjobs, key=lambda j: (j.is_senior, -(j.match_score or 0)))[:5]],
                "best_score": max((j.match_score or 0 for j in cjobs), default=0) or None,
            },
            "people": {
                "total": len(cpeople), "reachable": len(reachable),
                "verified": sum(1 for p in cpeople if p.confidence == "verified" and p.email),
                # people the evidence places there now (guardrails.affiliation)
                "current": sum(1 for p in cpeople if p.affiliation in ("verified-current", "likely-current")),
                "by_source": _count(p.source for p in cpeople),
                "best": {
                    "id": best.id, "name": best.name, "email": best.email, "confidence": best.confidence,
                    "affiliation": best.affiliation,
                    "label": roleclass.LABELS.get(best.role_class or "", best.role_class),
                } if best else None,
            },
            "searched_at": c.contacts_found_at.isoformat() if c.contacts_found_at else None,
            "ready": bool(reachable),
            "pay_bar": india.pay_label(c),
            "below_bar_reason": c.status_reason if c.status == "below_pay_bar" else None,
            # the complete bar (guardrails.complete_bar): the page lists complete rows first
            # and the rest under "Still being filled" — no hand-picked row disappears
            "complete": bool(c.complete),
            "missing": json.loads(c.complete_missing) if c.complete_missing else [],
        })

    return {
        "key": spec["key"], "name": spec.get("name"), "title": spec.get("title"),
        "description": (spec.get("description") or "").strip(), "source": spec.get("source"),
        "companies": out,
        "below_bar": below,
        "pay_floor_lpa": india.MIN_LPA,
        "stats": {
            "companies": len(out),
            "listed": sum(1 for r in out if r["listed"]),
            "with_website": sum(1 for r in out if r["website"]),
            "with_roles": sum(1 for r in out if r["roles"]["total"]),
            "with_people": sum(1 for r in out if r["people"]["total"]),
            "ready": sum(1 for r in out if r["ready"]),
            "people": sum(r["people"]["total"] for r in out),
            "reachable": sum(r["people"]["reachable"] for r in out),
            "hiring_proven": sum(1 for r in out if r["hiring_status"] == "verified"),
            "by_city": _count(r["city"] for r in out if r["city"]),
            "below_bar": len(below),
            "complete": sum(1 for r in out if r["complete"]),
            "being_filled": sum(1 for r in out if not r["complete"]),
        },
    }


def _count(values) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return out


# ------------------------------------------------------------------ people

def find_people(key: str, *, linkedin: bool = True, refresh_days: int = 0,
                limit: int | None = None, linkedin_only: bool = False,
                emails_only: bool = False, x_handles: bool = False) -> dict:
    """Roles and referrers at every company on the list, in list order.

    Per company, cheapest channel first, each committing on its own so a kill between
    companies loses nothing: their public job board (which also proves they hire) →
    the roles on it → the free people waterfall (GitHub commits, their site, the email
    pattern, Hunter) → public LinkedIn profiles via search, no login. `refresh_days`
    skips companies searched more recently than that; 0 searches every one.
    `linkedin_only` runs just the last step — the pass to make when search was
    throttled on the first run. `emails_only` is the address pass: for a company with
    nobody reachable it searches the open web for any "@domain" address, looks up the
    founders and HR on LinkedIn, learns the pattern, and fills every named person from
    it; for a company that already has a referrer it only does the fill.
    """
    from jobhunter import harvest
    from jobhunter.contacts import discover_for_company, linkedin_public

    init_db()
    cutoff = utcnow() - timedelta(days=refresh_days) if refresh_days else None
    with get_session() as session:
        companies = session.exec(
            select(Company).where(Company.list_key == key).order_by(col(Company.list_rank))
        ).all()
        # a row below the India pay bar is hidden, not chased (india.py)
        todo = [c.id for c in companies if c.status != "below_pay_bar"
                and not (cutoff and c.contacts_found_at and c.contacts_found_at >= cutoff)]
    if limit:
        todo = todo[:limit]

    out = {"list": key, "companies": len(todo), "boards_found": 0, "roles_added": 0,
           "people_added": 0, "verified": 0, "with_people": 0, "linkedin_added": 0,
           "addresses": 0, "by_pattern": 0, "lines": []}
    li_down = not linkedin
    for cid in todo:
        with get_session() as session:
            c = session.get(Company, cid)
            name, had_ats = c.name, bool(c.ats)
        line = {"id": cid, "name": name}
        if x_handles:
            from jobhunter.contacts import x_handles as xh

            line.update(xh.find_for_company(cid))
            out["lines"].append(line)
            log.info("lists people: %s", line)
            continue
        if emails_only:
            out["lines"].append(_emails_for(cid, name, line, out))
            log.info("lists people: %s", line)
            continue
        if linkedin_only:
            r = linkedin_public.find_for_company(cid)
            line["linkedin"] = r.get("added", 0)
            line["via"] = r.get("via")
            if r.get("error"):
                line["linkedin_error"] = r["error"]
            out["linkedin_added"] += r.get("added", 0)
            if r.get("added"):
                out["with_people"] += 1
            out["lines"].append(line)
            log.info("lists people: %s", line)
            continue
        try:
            if not had_ats:
                p = harvest.probe_ats(company_ids=[cid])
                if p["boards_found"]:
                    out["boards_found"] += 1
                    line["board"] = True
            r = harvest.scrape_roles(company_ids=[cid], only_without_jobs=False)
            line["roles"] = r.get("added", 0)
            out["roles_added"] += r.get("added", 0)
        except Exception as e:  # noqa: BLE001 — roles are a bonus; the people hunt still runs
            log.warning("lists people: roles for %s failed: %s", name, e)
            line["roles_error"] = str(e)[:120]

        try:
            s = asyncio.run(discover_for_company(cid))
            line["people"] = s.get("added", 0)
            line["verified"] = s.get("verified", 0)
            out["people_added"] += s.get("added", 0)
            out["verified"] += s.get("verified", 0)
        except Exception as e:  # noqa: BLE001
            log.warning("lists people: %s failed: %s", name, e)
            line["people_error"] = str(e)[:120]

        if not li_down:
            r = linkedin_public.find_for_company(cid)
            if r.get("error"):
                line["linkedin_error"] = r["error"]
                if "rate" in r["error"]:
                    li_down = True          # every further search would only wait and fail
                    out["linkedin_stopped"] = r["error"]
            else:
                line["linkedin"] = r.get("added", 0)
                out["linkedin_added"] += r.get("added", 0)

        if (line.get("people") or 0) + (line.get("linkedin") or 0):
            out["with_people"] += 1
        out["lines"].append(line)
        log.info("lists people: %s", line)

    return out


def _emails_for(cid: int, name: str, line: dict, out: dict) -> dict:
    """One company's address pass — see find_people(emails_only=True)."""
    from jobhunter.contacts import fill_by_pattern, linkedin_public, web_emails
    from jobhunter.network import _reachable

    with get_session() as session:
        c = session.get(Company, cid)
        people = session.exec(select(Contact).where(Contact.company_id == cid)).all()
        reachable = len(_reachable(c, people))
        line["reachable_before"] = reachable
    if reachable == 0:
        try:
            w = web_emails.find(cid)
            line["addresses"] = w.get("addresses", 0)
            line["pattern"] = w.get("pattern")
            out["addresses"] += w.get("addresses", 0)
        except Exception as e:  # noqa: BLE001 — one company must not end the pass
            line["web_error"] = str(e)[:120]
        r = linkedin_public.find_founders(cid)
        line["founders"] = r.get("added", 0)
        if r.get("error"):
            line["founders_error"] = r["error"]
    n = fill_by_pattern(cid)
    line["by_pattern"] = n
    out["by_pattern"] += n
    with get_session() as session:
        c = session.get(Company, cid)
        people = session.exec(select(Contact).where(Contact.company_id == cid)).all()
        line["reachable_after"] = len(_reachable(c, people))
    if line["reachable_after"] > line["reachable_before"]:
        out["with_people"] += 1
    return line


# ------------------------------------------------------------------ audit

def audit_orgs(key: str, *, fix: bool = True) -> dict:
    """Re-check every GitHub org on the list against the strict match, and drop what fails.

    A wrong org is worse than none: its committers arrive as "verified" engineers at a
    company they have never heard of, and the review gate cannot tell. On a failure the
    org is cleared and its GitHub-sourced contacts removed; the next people pass then
    starts clean. Nothing that was written to (an Email row) is touched.
    """
    from jobhunter.contacts import github_miner
    from jobhunter.db import Email
    from jobhunter.scrapers.base import make_client

    init_db()
    with get_session() as session:
        companies = [(c.id, c.name, c.website, c.github_org) for c in session.exec(
            select(Company).where(Company.list_key == key, col(Company.github_org).is_not(None))
            .order_by(col(Company.list_rank))).all()]

    async def run() -> list:
        async with make_client() as http:
            return [(cid, name, org, *await github_miner.org_is_theirs(http, name, site, org))
                    for cid, name, site, org in companies]

    out = {"list": key, "checked": len(companies), "ok": 0, "wrong": [], "contacts_removed": 0}
    for cid, name, org, ok, why in asyncio.run(run()):
        if ok:
            out["ok"] += 1
            continue
        out["wrong"].append(f"{name}: {org} — {why}")
        if not fix:
            continue
        with get_session() as session:
            written = set(session.exec(select(Email.contact_id).where(Email.company_id == cid)).all())
            for p in session.exec(select(Contact).where(Contact.company_id == cid, Contact.source == "github")).all():
                if p.id in written:
                    continue
                session.delete(p)
                out["contacts_removed"] += 1
            c = session.get(Company, cid)
            c.github_org = None
            c.contacts_found_at = None
            session.add(c)
            session.commit()
        log.warning("lists audit: %s — cleared org %s (%s)", name, org, why)
    return out
