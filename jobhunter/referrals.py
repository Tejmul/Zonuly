"""Referrals — "the main thing" (Tejmul, 2026-10-08).

A company is worth showing when there is a person confirmed there NOW whom we can write
to. This module is the work that gets us there, run by the tick before anything else:

    people      for the ★ list, then companies complete except for a referrer, then the
                rest by priority (db.people_order): GitHub (org public members, commits
                inside 12 months, profiles — contacts.discover_for_company), the company's
                team / about pages through Jina, then public LinkedIn through the keyless
                web search (engineers → engineering leads → founders → recruiters, the
                city for Bengaluru / Hyderabad companies). Target: TARGET ready referrers,
                at least one with an address. Each company is marked "[refer] <date>".
    addresses   the company's address format learned from its own confirmed addresses
                (contacts/addresses.py) and applied to ready referrers without one.
    reverify    referrers whose affiliation was checked more than REVERIFY_DAYS ago are
                re-confirmed — GitHub membership / commit recency / profile, a LinkedIn
                headline, the page that published the address. Failing → unconfirmed.
    revalidate  ~30 companies a tick, never-checked then oldest validated_at first: the
                website still answers, the description still passes, the careers page still
                lists roles. A failed part drops the company out of complete.
    measure     the numbers the report and the audit read.

Every write goes through guardrails. Free channels only, none through the user's own
accounts: GitHub through GITHUB_TOKEN (a separate account's; 60 calls an hour without one,
budgeted per company), Jina Reader, the keyless web search (web.KEYLESS), yt-dlp's public
YouTube metadata (contacts/public_mentions.py).

Layering: db, guardrails, contacts, research, hiring_verify — the pipeline layer.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
import time
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from sqlmodel import col, select

from jobhunter import CONFIG, ROOT, guardrails
from jobhunter.db import (DB_PATH, Company, Contact, ExtractionReject, ExtractionWrite, Job, get_session,
                          get_setting, init_db, people_order, set_setting, utcnow)

log = logging.getLogger(__name__)

_R = (CONFIG.get("referrals") or {})
TARGET = int(_R.get("target_ready", guardrails.REFERRERS_WANTED))
REVERIFY_DAYS = int(_R.get("reverify_days", 90))
REVALIDATE_PER_TICK = int(_R.get("revalidate_per_tick", 30))
#: how long a company rests after a people pass, by queue band (list / only-referrer / rest)
REST_DAYS = tuple(_R.get("rest_days") or (3, 7, 21))
#: re-run GitHub + site discovery only when the last run is older than this
DISCOVER_FRESH_DAYS = int(_R.get("discover_fresh_days", 7))
#: GitHub mining for a company needs about this many calls of the hourly budget
GITHUB_MIN_REMAINING = int(((CONFIG.get("tick") or {}).get("github_min_remaining", 20)))
#: the public-mention channels (contacts/public_mentions.py)
MENTIONS = bool(_R.get("public_mentions", True))
MARK = "[refer]"
STAR = "india-star"
LOG_KEY = "referrals:runs"
# ---- the dedicated referral job (2026-10-14: "it ONLY scrapes referrals, for India —
# Bengaluru as priority"): launchd com.zonuly.referrals every 15 min, its own lock
#: seconds of work per cycle, and companies per cycle
CYCLE_S = int(_R.get("cycle_seconds", 1200))
CYCLE_LIMIT = int(_R.get("cycle_companies", 40))
#: once every keyless search backend is cooling, this many more companies get the
#: non-search channels (team pages, GitHub, the pattern), then the cycle ends
DOWN_TAIL = int(_R.get("search_down_tail", 6))
#: a company someone else touched this recently (contacts_found_at) is left alone
TOUCHED_MINUTES = int(_R.get("skip_touched_minutes", 60))
CLAIM_PREFIX = "refer:claim:"
INDIA_CITIES = ("Bengaluru", "Hyderabad")
BUCKETS = ("Bengaluru", "Hyderabad", "India", "Remote")


# ------------------------------------------------------------------ bookkeeping

def backup(tag: str) -> Path:
    """The live database copied with SQLite's backup API (WAL-safe while the tick writes)."""
    dest = ROOT / "data" / "backups" / f"jobhunter-{datetime.now():%Y-%m-%d-%H%M}-{tag}.db"
    dest.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(DB_PATH, timeout=60)
    dst = sqlite3.connect(dest)
    with dst:
        src.backup(dst)
    dst.close()
    src.close()
    return dest


def _log_run(kind: str, out: dict) -> None:
    """Keep the last 300 runs so the hourly audit can sum what happened in its hour."""
    try:
        with get_session() as s:
            raw = get_setting(s, LOG_KEY, "")
            runs = json.loads(raw) if raw else []
            runs = (runs + [{"at": utcnow().isoformat(timespec="seconds"), "kind": kind,
                             **{k: v for k, v in out.items() if isinstance(v, (int, float, str, dict))}}])[-300:]
            set_setting(s, LOG_KEY, json.dumps(runs, default=str)[:400_000])
    except Exception as e:  # noqa: BLE001 — bookkeeping must never fail the work
        log.debug("referrals: could not log run: %s", e)


def runs_since(since: datetime) -> list[dict]:
    with get_session() as s:
        raw = get_setting(s, LOG_KEY, "")
    try:
        runs = json.loads(raw) if raw else []
    except json.JSONDecodeError:
        return []
    return [r for r in runs if r.get("at", "") >= since.isoformat(timespec="seconds")]


def _marked(notes: str | None) -> datetime | None:
    m = re.search(r"\[refer\] (\d{4}-\d{2}-\d{2})", notes or "")
    return datetime.strptime(m.group(1), "%Y-%m-%d") if m else None


def _mark(session, c: Company, summary: str) -> None:
    kept = [ln for ln in (c.notes or "").splitlines() if not ln.startswith(MARK)]
    c.notes = "\n".join(kept + [f"{MARK} {utcnow():%Y-%m-%d}: {summary}"[:200]]).strip()
    session.add(c)


# ------------------------------------------------------------------ measure

def measure(session=None) -> dict:
    """The referral numbers: distribution of ready referrers over active companies, with
    an address, the ★ list, complete companies, ready referrers by source and role."""
    own = session is None
    session = session or get_session()
    try:
        active = session.exec(select(Company).where(col(Company.status).is_(None))).all()
        ids = {c.id for c in active}
        ready = [p for p in session.exec(select(Contact).where(col(Contact.affiliation).in_(guardrails.READY))).all()
                 if p.company_id in ids and guardrails.is_ready(p)]
        per: dict[int, list[int]] = {}
        for p in ready:
            e = per.setdefault(p.company_id, [0, 0])
            e[0] += 1
            e[1] += bool(p.email)
        dist = Counter()
        for c in active:
            n = per.get(c.id, [0, 0])[0]
            dist["0" if n == 0 else "1-2" if n <= 2 else "3-4" if n <= 4 else "5+"] += 1
        star = [c for c in active if c.list_key == STAR]
        star_all = session.exec(select(Company).where(Company.list_key == STAR)).all()
        with_email = sum(1 for c in active if per.get(c.id, [0, 0])[1] >= 1)
        return {
            "active": len(active),
            "distribution": {k: dist.get(k, 0) for k in ("0", "1-2", "3-4", "5+")},
            "companies_ready_with_email": with_email,
            "companies_target_met": sum(1 for c in active if per.get(c.id, [0, 0])[0] >= TARGET
                                        and per.get(c.id, [0, 0])[1] >= 1),
            "star": {"on_list": len(star_all), "active": len(star),
                     "ready_3plus": sum(1 for c in star if per.get(c.id, [0, 0])[0] >= 3),
                     "ready_with_email": sum(1 for c in star if per.get(c.id, [0, 0])[1] >= 1),
                     "complete": sum(1 for c in star if c.complete)},
            "complete": sum(1 for c in active if c.complete),
            "ready_referrers": len(ready),
            "ready_with_email": sum(1 for p in ready if p.email),
            "ready_pattern_guessed": sum(1 for p in ready if p.email and p.confidence == "pattern-guessed"),
            "ready_by_source": dict(Counter(p.source for p in ready).most_common()),
            "ready_by_role": dict(Counter(p.role_class or "unclassified" for p in ready).most_common()),
            "patterns": dict(Counter(_pattern_status(c) for c in active if c.domain).most_common()),
        }
    finally:
        if own:
            session.close()


def _pattern_status(c: Company) -> str:
    try:
        ev = json.loads(c.email_pattern_evidence or "{}")
    except (TypeError, ValueError):
        ev = {}
    if isinstance(ev, dict) and ev.get("status"):
        return ev["status"]
    return "legacy" if c.email_pattern else "unlearned"


# ------------------------------------------------------------------ addresses (the pattern step)

def pattern_step(*, dry_run: bool = True, company_ids: list[int] | None = None, sample: int = 12) -> dict:
    """Learn every company's address format from its own addresses; retract what an
    unconfirmed pattern made; address the ready referrers by the confirmed one."""
    from jobhunter.contacts import addresses

    init_db()
    out = {"dry_run": dry_run, "companies": 0, "status": Counter(), "pattern_written": 0, "pattern_agreed": 0,
           "pattern_cleared": 0, "addresses_added": 0, "addresses_retracted": 0, "skipped": Counter(),
           "companies_gaining_address": 0, "samples": []}
    with get_session() as session:
        ids = company_ids or [c.id for c in session.exec(select(Company).where(
            col(Company.status).is_(None), col(Company.domain).is_not(None))).all()]
    before = _with_email_ids()
    for cid in ids:
        with get_session() as session:
            c = session.get(Company, cid)
            if c is None or not c.domain:
                continue
            plan = addresses.plan_company(session, c)
            out["companies"] += 1
            out["status"][plan.verdict["status"]] += 1
            if plan.pattern_change in ("written", "agreed", "cleared"):
                out[f"pattern_{plan.pattern_change}"] += 1
            for k, v in plan.skipped.items():
                out["skipped"][k] += v
            if dry_run:
                out["addresses_added"] += len(plan.add)
                out["addresses_retracted"] += len(plan.retract)
                if plan.add and cid not in before:
                    out["companies_gaining_address"] += 1
                if (plan.add or plan.retract) and len(out["samples"]) < sample:
                    out["samples"].append({"company": c.name, "pattern": plan.verdict.get("pattern"),
                                           "why": plan.verdict.get("reason"),
                                           "add": [a for _, a in plan.add[:3]],
                                           "retract": [(a, w[:60]) for _, a, w in plan.retract[:2]]})
                continue
            res = addresses.apply_plan(session, c, plan)
            session.commit()
            out["addresses_added"] += res["added"]
            out["addresses_retracted"] += res["retracted"]
    if not dry_run:
        out["companies_gaining_address"] = len(_with_email_ids() - before)
    out["status"], out["skipped"] = dict(out["status"]), dict(out["skipped"])
    if not dry_run:
        _log_run("pattern", {k: v for k, v in out.items() if k not in ("samples",)})
    return out


def _with_email_ids() -> set[int]:
    with get_session() as s:
        return {cid for cid, (_, e) in guardrails.ready_counts(s).items() if e >= 1}


# ------------------------------------------------------------------ people

def _band(c: Company) -> int:
    return people_order(c)[0]


def people_queue(limit: int | None = None, *, list_key: str | None = None, top: int | None = None,
                 force: bool = False) -> list[int]:
    """Companies short of TARGET ready referrers or of one with an address, in
    db.people_order, each resting REST_DAYS after its last pass (unless `force`).
    `top` keeps only the `top` highest-priority non-list companies."""
    init_db()
    now = utcnow()
    with get_session() as session:
        stmt = select(Company).where(col(Company.status).is_(None))
        if list_key:
            stmt = stmt.where(Company.list_key == list_key)
        rows = session.exec(stmt).all()
        counts = guardrails.ready_counts(session)
    pending = []
    for c in rows:
        r, e = counts.get(c.id, (0, 0))
        if r >= TARGET and e >= 1:
            continue
        if not c.list_key and (c.overrated or c.tier == "reject"):
            continue
        rest = REST_DAYS[min(_band(c), 2)]
        last = _marked(c.notes)
        if not force and last and now - last < timedelta(days=rest):
            continue
        pending.append(c)
    pending.sort(key=lambda c: (*people_order(c), -(c.priority_score or 0), c.id))
    if top is not None:
        listed = [c for c in pending if c.list_key]
        rest = sorted((c for c in pending if not c.list_key), key=lambda c: (-(c.priority_score or 0), c.id))[:top]
        pending = listed + rest
    ids = [c.id for c in pending]
    return ids[:limit] if limit else ids


def _counts(cid: int) -> tuple[int, int]:
    with get_session() as s:
        rows = s.exec(select(Contact).where(Contact.company_id == cid,
                                            col(Contact.affiliation).in_(guardrails.READY))).all()
        ready = [p for p in rows if guardrails.is_ready(p)]
        return len(ready), sum(1 for p in ready if p.email)


def _short(cid: int) -> bool:
    r, e = _counts(cid)
    return r < TARGET or e < 1


def team_pages(cid: int) -> int:
    """Named addresses on their own team / about pages, read through Jina."""
    from jobhunter.contacts import roles, site_scraper

    with get_session() as session:
        c = session.get(Company, cid)
        if c is None or not c.website:
            return 0
        website, domain = c.website, c.domain
        have = {(p.email or "").lower() for p in session.exec(select(Contact).where(Contact.company_id == cid)).all()}
    found = site_scraper.team_from_jina(website, domain)
    added = 0
    with get_session() as session:
        c = session.get(Company, cid)
        for sc, url in found:
            if sc.email in have:
                continue
            g = guardrails.vet_contact(session, company=c, name=sc.name, email_addr=sc.email, source="site",
                                       channel="jina", url=url, name_source="page", team_page=url, published_at=url)
            if g is None or not g.get("email") or not g.get("name"):
                continue
            v = roles.classify(None, name=g["name"], email=g["email"])
            session.add(Contact(
                company_id=cid, name=g["name"], email=g["email"], source="site", confidence=g["confidence"],
                role_class=v.role_class, seniority=v.seniority, referral_rank=v.referral_rank,
                role_evidence=v.evidence, classified_at=utcnow(), research_notes=f"address published at {url}",
                name_source="page", email_kind=g.get("email_kind"), affiliation=g["affiliation"],
                affiliation_source=g["affiliation_source"], affiliation_evidence=g["affiliation_evidence"],
                affiliation_checked_at=g["affiliation_checked_at"]))
            have.add(sc.email)
            added += 1
        session.commit()
    return added


def people_for_company(cid: int, *, linkedin: bool = True) -> dict:
    """The free waterfall for one company, stopping once it has TARGET ready referrers and
    one with an address: GitHub + their site → team pages via Jina → addresses on the open
    web (only while nobody confirmed there has one) → LinkedIn (keyless search, best
    referrers first, until TARGET) → the confirmed pattern."""
    from jobhunter.contacts import addresses, discover_for_company, github_miner, linkedin_public, web_emails
    from jobhunter.research import web

    with get_session() as session:
        c = session.get(Company, cid)
        if c is None:
            return {"error": f"no company {cid}"}
        name, listed = c.name, bool(c.list_key)
        has_site = bool(c.website or c.domain or c.github_org)
        fresh = c.contacts_found_at and utcnow() - c.contacts_found_at < timedelta(
            days=REST_DAYS[0] if listed else DISCOVER_FRESH_DAYS)
    r0, e0 = _counts(cid)
    out: dict = {"company": name, "before": [r0, e0], "github_site": 0, "team_pages": 0, "web": 0, "linkedin": 0,
                 "mentions": 0, "by_pattern": 0, "channels": []}
    if has_site and not fresh:
        # GitHub at 60 calls an hour without a separate-account token: mine only while the
        # hourly budget has room for a company (~20 calls); the site scrape runs either way
        left = github_miner.calls_left().get("remaining")
        gh_ok = left is None or left >= GITHUB_MIN_REMAINING
        if not gh_ok:
            out["github_skipped"] = f"GitHub budget: {left} calls left this hour"
        try:
            s = asyncio.run(discover_for_company(cid, github=gh_ok))
            out["github_site"] = s.get("added", 0)
            out["by_pattern"] += s.get("by_pattern", 0)
            out["channels"].append("github+site")
        except Exception as e:  # noqa: BLE001 — one channel failing must not end the company
            out["github_error"] = str(e)[:160]
    if _short(cid) and has_site:
        try:
            out["team_pages"] = team_pages(cid)
            out["channels"].append("jina-team")
        except Exception as e:  # noqa: BLE001
            out["team_error"] = str(e)[:160]
    if has_site and _counts(cid)[1] == 0 and not web.search_down():
        # nobody confirmed there has an address yet: any "@their-domain" address the open
        # web shows (a founder on a conference page, an engineer on a blog) — keyless search
        try:
            w = web_emails.find(cid)
            out["web"] = w.get("people", 0) + w.get("attached", 0)
            out["by_pattern"] += w.get("by_pattern", 0) or 0
            out["channels"].append("web-emails")
        except Exception as e:  # noqa: BLE001
            out["web_error"] = str(e)[:160]
    if linkedin and _short(cid):
        for kind in linkedin_public.ROLE_QUERIES:
            if _counts(cid)[0] >= TARGET:
                break
            if web.search_down():
                out["search_down"] = True
                break
            res = linkedin_public.search_role(cid, kind)
            out["channels"].append(f"linkedin:{kind}")
            if res.get("error"):
                out["linkedin_error"] = res["error"][:160]
                if "rate-limited" in res["error"] or "cooling" in res["error"]:
                    out["search_down"] = True
                    break
                continue
            out["linkedin"] += res.get("added", 0)
    # 2026-10-14: people named in public — podcasts / YouTube (yt-dlp, works while the web
    # search cools down), talks and meetups (keyless search), bylines on their own blog
    if MENTIONS and _short(cid):
        from jobhunter.contacts import public_mentions

        try:
            chans = ("youtube", "blog") if web.search_down() else ("youtube", "talks", "blog")
            m = public_mentions.find(cid, channels=chans if has_site else tuple(c for c in chans if c != "blog"))
            out["mentions"] = m.get("added", 0)
            out["channels"].append("mentions")
        except Exception as e:  # noqa: BLE001
            out["mentions_error"] = str(e)[:160]
    res = addresses.fill(cid)
    out["by_pattern"] += res.get("added", 0)
    out["pattern_status"] = res.get("status")
    out["after"] = list(_counts(cid))
    # rest the company only when every channel had its turn (or the target is met): a pass
    # that skipped LinkedIn because the keyless search was cooling down is not a pass
    met = out["after"][0] >= TARGET and out["after"][1] >= 1
    out["marked"] = met or (linkedin and not out.get("search_down"))
    if out["marked"]:
        with get_session() as session:
            c = session.get(Company, cid)
            _mark(session, c, f"{out['before']}→{out['after']} via {','.join(out['channels'])}")
            session.commit()
    return out


def people_step(limit: int = 100, *, budget_s: int = 1500, ids: list[int] | None = None,
                linkedin: bool = True, list_key: str | None = None, top: int | None = None,
                force: bool = False, owner: str | None = None) -> dict:
    """The people step the tick runs first. Stops at `budget_s` seconds, and stops the
    LinkedIn searches (not the step) once every keyless search backend is cooling down.
    With `owner` (the tick passes "tick") a company another worker has claimed — the
    referral job's queue window — is skipped, and each company is claimed while worked."""
    from jobhunter.research import web

    t0 = time.time()
    queue = ids if ids is not None else people_queue(limit, list_key=list_key, top=top, force=force)
    if owner:
        held = claimed_by_others(owner)
        skipped = [cid for cid in queue if cid in held]
        queue = [cid for cid in queue if cid not in held]
    out = {"companies": 0, "queued": len(queue), "added_ready": 0, "added_with_email": 0, "by_pattern": 0,
           "reached_target": 0, "gained_email": 0, "linkedin_added": 0, "github_site_added": 0,
           "team_added": 0, "errors": 0, "stopped": None}
    out["linkedin_skipped"] = 0
    if owner:
        out["yielded_to_claims"] = len(skipped)
    for cid in queue[:limit] if limit else queue:
        if time.time() - t0 > budget_s:
            out["stopped"] = f"time budget ({budget_s}s) reached"
            break
        if owner and not claim(cid, owner, ttl_s=900):
            out["yielded_to_claims"] = out.get("yielded_to_claims", 0) + 1
            continue
        # the keyless backends cool down for a few minutes after repeated failures; while
        # they do, LinkedIn is skipped (and the company is not rested), then tried again
        use_li = linkedin and not web.search_down()
        if linkedin and not use_li:
            out["linkedin_skipped"] += 1
            out["linkedin_down"] = "every keyless search backend was cooling down for part of the run"
        try:
            r = people_for_company(cid, linkedin=use_li)
        except Exception as e:  # noqa: BLE001 — one company must not end the step
            log.warning("referrals: company %s failed: %s", cid, e)
            out["errors"] += 1
            continue
        finally:
            if owner:
                release(cid, owner)
        out["companies"] += 1
        out.setdefault("touched", []).append(cid)
        if r.get("search_down"):
            out["linkedin_down"] = r.get("linkedin_error") or "keyless search cooling down"
        b, a = r.get("before") or [0, 0], r.get("after") or [0, 0]
        out["added_ready"] += max(0, a[0] - b[0])
        out["added_with_email"] += max(0, a[1] - b[1])
        out["by_pattern"] += r.get("by_pattern", 0)
        out["linkedin_added"] += r.get("linkedin", 0)
        out["github_site_added"] += r.get("github_site", 0)
        out["team_added"] += r.get("team_pages", 0)
        out["mentions_added"] = out.get("mentions_added", 0) + r.get("mentions", 0)
        if r.get("github_skipped"):
            out["github_skipped"] = out.get("github_skipped", 0) + 1
        out["reached_target"] += (a[0] >= TARGET and a[1] >= 1) and not (b[0] >= TARGET and b[1] >= 1)
        out["gained_email"] += a[1] >= 1 and b[1] == 0
    out["seconds"] = round(time.time() - t0)
    _log_run("people", out)
    log.info("referrals people: %s", out)
    return out


# ------------------------------------------------------------------ claims (who is working a company)

def _claim_key(cid: int) -> str:
    return f"{CLAIM_PREFIX}{cid}"


def claims(now: datetime | None = None) -> dict[int, str]:
    """company_id -> owner, for every claim not yet expired. One `setting` row per company
    (refer:claim:<id> = '<owner>|<until iso>'), so two writers never overwrite each other's
    claims in one JSON blob."""
    from jobhunter.db import Setting

    now = now or utcnow()
    out: dict[int, str] = {}
    with get_session() as s:
        rows = s.exec(select(Setting).where(col(Setting.key).startswith(CLAIM_PREFIX))).all()
    for row in rows:
        owner, _, until = (row.value or "").partition("|")
        try:
            if datetime.fromisoformat(until) > now:
                out[int(row.key[len(CLAIM_PREFIX):])] = owner
        except ValueError:
            continue
    return out


def claimed_by_others(owner: str) -> set[int]:
    return {cid for cid, who in claims().items() if who != owner}


def claim(cid: int, owner: str, *, ttl_s: int = 900) -> bool:
    """Claim a company for `ttl_s` seconds; False when someone else holds it."""
    from jobhunter.db import Setting

    now = utcnow()
    for attempt in range(5):
        try:
            with get_session() as s:
                row = s.get(Setting, _claim_key(cid))
                if row is not None:
                    who, _, until = (row.value or "").partition("|")
                    try:
                        live = datetime.fromisoformat(until) > now
                    except ValueError:
                        live = False
                    if live and who != owner:
                        return False
                value = f"{owner}|{(now + timedelta(seconds=ttl_s)).isoformat(timespec='seconds')}"
                set_setting(s, _claim_key(cid), value)
            return True
        except Exception as e:  # noqa: BLE001 — "database is locked" under the WAL writers: retry
            if "locked" not in str(e) or attempt == 4:
                log.debug("claim %s failed: %s", cid, e)
                return True       # a claim is a courtesy, never a reason to stop the work
            time.sleep(1 + attempt)
    return True


def release(cid: int, owner: str) -> None:
    from jobhunter.db import Setting

    for attempt in range(5):
        try:
            with get_session() as s:
                row = s.get(Setting, _claim_key(cid))
                if row is not None and (row.value or "").partition("|")[0] == owner:
                    s.delete(row)
                    s.commit()
            return
        except Exception as e:  # noqa: BLE001
            if "locked" not in str(e) or attempt == 4:
                return
            time.sleep(1 + attempt)


def prune_claims() -> int:
    """Expired claim rows deleted (a killed cycle leaves its window behind)."""
    from jobhunter.db import Setting

    now, n = utcnow(), 0
    with get_session() as s:
        for row in s.exec(select(Setting).where(col(Setting.key).startswith(CLAIM_PREFIX))).all():
            try:
                live = datetime.fromisoformat((row.value or "").partition("|")[2]) > now
            except ValueError:
                live = False
            if not live:
                s.delete(row)
                n += 1
        s.commit()
    return n


# ------------------------------------------------------------------ the referral job (run_cycle)

def bucket(c: Company) -> int | None:
    """0 Bengaluru, 1 Hyderabad, 2 the rest of India, 3 a remote target; None = not ours.
    A company in both cities counts as Bengaluru (the priority)."""
    if c.status is not None:
        return None
    city = (c.city or "").lower()
    if "bengaluru" in city or "bangalore" in city:
        return 0
    if "hyderabad" in city:
        return 1
    if c.list_key == STAR or (c.hq_region or "") == "india" or c.india_found_at is not None:
        return 2
    from jobhunter.fill import REMOTE_REGIONS

    if (c.hq_region or "") in REMOTE_REGIONS and c.tier != "reject" and not c.overrated:
        return 3
    return None


def cycle_key(c: Company) -> tuple:
    """Bengaluru → Hyderabad → India → remote; inside each, the ★ list first (list order),
    then companies with evidence of pay at or above the India floor (before pay unknown),
    then priority, then the best role's selection score."""
    b = bucket(c)
    return (9 if b is None else b, 0 if c.list_key == STAR else 1, (c.list_rank or 9999) if c.list_key else 0,
            pay_first(c), -(c.priority_score or 0), -(c.best_selection_score or 0), c.id or 0)


def pay_first(c: Company) -> int:
    """0 when the pay floor found evidence the company clears it (india.apply_pay_floor), else 1."""
    return 0 if guardrails.meta_of(c, "pay_floor").get("value") == "ok" else 1


def cycle_queue(limit: int | None = None, *, exclude: set[int] | frozenset = frozenset(), owner: str = "referrals",
                force: bool = False) -> list[int]:
    """The referral job's queue: companies in a bucket, short of TARGET ready referrers or
    of one with an address, not resting after a pass ([refer] <date>, REST_DAYS), not
    touched by another worker in the last TOUCHED_MINUTES (contacts_found_at), not claimed
    by someone else, and not in `exclude`."""
    init_db()
    now = utcnow()
    with get_session() as session:
        rows = session.exec(select(Company).where(col(Company.status).is_(None))).all()
        counts = guardrails.ready_counts(session)
    held = claimed_by_others(owner)
    pending = []
    for c in rows:
        if c.id in exclude or c.id in held or bucket(c) is None:
            continue
        r, e = counts.get(c.id, (0, 0))
        if r >= TARGET and e >= 1:
            continue                                     # at target: nothing to do
        if not c.list_key and (c.overrated or c.tier == "reject"):
            continue
        if not force:
            last = _marked(c.notes)
            if last and now - last < timedelta(days=REST_DAYS[min(_band(c), 2)]):
                continue
            if c.contacts_found_at and now - c.contacts_found_at < timedelta(minutes=TOUCHED_MINUTES):
                continue
        pending.append(c)
    pending.sort(key=cycle_key)
    ids = [c.id for c in pending]
    return ids[:limit] if limit else ids


def lead_channel(source: str | None, affiliation_source: str | None) -> str:
    """Where a referrer came from, in the People page's words: linkedin | github | team |
    talks | web. The evidence decides first (a LinkedIn headline is LinkedIn whatever
    address was later attached), then the row's source."""
    a, s = affiliation_source or "", source or ""
    if a == "linkedin-headline" or s == "linkedin-public":
        return "linkedin"
    if a == "public-mention" or s == "public-page":
        return "talks"
    if a in ("org-member", "recent-commit", "profile-company") or s == "github":
        return "github"
    if a == "team-page" or s == "site":
        return "team"
    return "web"


def _ready_rows(cid: int) -> dict[int, tuple[str, bool]]:
    """contact id -> (channel, has an address), for the company's ready referrers."""
    with get_session() as s:
        rows = s.exec(select(Contact).where(Contact.company_id == cid,
                                            col(Contact.affiliation).in_(guardrails.READY))).all()
        return {p.id: (lead_channel(p.source, p.affiliation_source), bool(p.email))
                for p in rows if guardrails.is_ready(p)}


def refer_company(cid: int, *, search_ok: bool = True) -> dict:
    """One company, referral work only, in the job's order: public LinkedIn (several query
    shapes, the city first) → their team page → GitHub (inside the hourly budget) → public
    mentions → addresses (the open web while nobody has one, then the learned format).
    Stops searching once TARGET ready referrers are found. `search_ok=False` (every keyless
    backend cooling) runs only the channels that need no web search."""
    from jobhunter.contacts import addresses, discover_for_company, github_miner, linkedin_public, web_emails
    from jobhunter.research import web

    with get_session() as session:
        c = session.get(Company, cid)
        if c is None:
            return {"error": f"no company {cid}"}
        name, listed, b = c.name, bool(c.list_key), bucket(c)
        has_site = bool(c.website or c.domain or c.github_org)
        fresh = c.contacts_found_at and utcnow() - c.contacts_found_at < timedelta(
            days=REST_DAYS[0] if listed else DISCOVER_FRESH_DAYS)
    city = BUCKETS[b] if b in (0, 1) else None
    before = _ready_rows(cid)
    out: dict = {"company": name, "bucket": BUCKETS[b] if b is not None else None,
                 "before": [len(before), sum(1 for _, e in before.values() if e)], "channels": [], "queries": 0}

    def ready_now() -> int:
        return _counts(cid)[0]

    # 1. public LinkedIn, search results only — the headline must name the company now
    if search_ok:
        for kind in linkedin_public.REFERRAL_QUERIES:
            if ready_now() >= TARGET:
                break
            if web.search_down():
                out["search_down"] = True
                break
            res = linkedin_public.search_role(cid, kind, queries=linkedin_public.REFERRAL_QUERIES,
                                              city=city if city else None)
            out["queries"] += 1
            out["channels"].append(f"linkedin:{kind}")
            if res.get("error"):
                out["linkedin_error"] = res["error"][:160]
                if "rate-limited" in res["error"] or "cooling" in res["error"]:
                    out["search_down"] = True
                    break
    else:
        out["search_down"] = True
    # 2. their own team / about page, through Jina
    if has_site and ready_now() < TARGET:
        try:
            team_pages(cid)
            out["channels"].append("team")
        except Exception as e:  # noqa: BLE001 — one channel failing must not end the company
            out["team_error"] = str(e)[:160]
    # 3. GitHub (org members, recent committers, profiles) inside the hourly budget
    if has_site and not fresh and ready_now() < TARGET:
        left = github_miner.calls_left().get("remaining")
        gh_ok = left is None or left >= GITHUB_MIN_REMAINING
        if not gh_ok:
            out["github_skipped"] = f"GitHub budget: {left} calls left this hour"
        try:
            asyncio.run(discover_for_company(cid, github=gh_ok))
            out["channels"].append("github+site" if gh_ok else "site")
        except Exception as e:  # noqa: BLE001
            out["github_error"] = str(e)[:160]
    # 4. named in public: podcasts / YouTube (no web search), talks, their blog's bylines
    if MENTIONS and ready_now() < TARGET:
        from jobhunter.contacts import public_mentions

        chans = ("youtube", "talks", "blog") if search_ok and not out.get("search_down") else ("youtube", "blog")
        try:
            public_mentions.find(cid, channels=chans if has_site else tuple(x for x in chans if x != "blog"))
            out["channels"].append("mentions")
        except Exception as e:  # noqa: BLE001
            out["mentions_error"] = str(e)[:160]
    # 5. addresses: the open web while nobody ready has one, then the learned format
    #    (≥2 agreeing examples; a pattern-guessed address still spends the 5/day guessed cap)
    if has_site and _counts(cid)[1] == 0 and search_ok and not web.search_down():
        try:
            web_emails.find(cid)
            out["channels"].append("web-emails")
        except Exception as e:  # noqa: BLE001
            out["web_error"] = str(e)[:160]
    try:
        res = addresses.fill(cid)
        out["pattern_status"] = res.get("status")
    except Exception as e:  # noqa: BLE001
        out["pattern_error"] = str(e)[:160]
    after = _ready_rows(cid)
    out["after"] = [len(after), sum(1 for _, e in after.values() if e)]
    new = {k: v for k, v in after.items() if k not in before}
    out["added_by_channel"] = dict(Counter(ch for ch, _ in new.values()))
    out["emails_found"] = sum(1 for k, (_, e) in after.items() if e and not (before.get(k) or (None, False))[1])
    met = out["after"][0] >= TARGET and out["after"][1] >= 1
    out["reached_target"] = met and not (out["before"][0] >= TARGET and out["before"][1] >= 1)
    # rested only when every channel had its turn: a pass without the search is not a pass
    out["marked"] = met or not out.get("search_down")
    if out["marked"]:
        with get_session() as session:
            c = session.get(Company, cid)
            _mark(session, c, f"job {out['before']}→{out['after']} via {','.join(out['channels'])}")
            session.commit()
    return out


def run_cycle(*, budget_s: int | None = None, limit: int | None = None, exclude: set[int] | None = None,
              ids: list[int] | None = None, owner: str = "referrals") -> dict:
    """One cycle of the referral job — referral work and nothing else. Claims its queue
    window (so the tick's people step searches other companies), works it in order inside
    `budget_s`, and ends early once the keyless search has been cooling for DOWN_TAIL
    companies rather than spinning."""
    from jobhunter.research import web

    budget_s = budget_s or CYCLE_S
    limit = limit or CYCLE_LIMIT
    t0 = time.time()
    try:
        prune_claims()
    except Exception as e:  # noqa: BLE001
        log.debug("prune claims: %s", e)
    queue = ids if ids is not None else cycle_queue(limit, exclude=exclude or set(), owner=owner)
    window = [cid for cid in queue[:limit] if claim(cid, owner, ttl_s=budget_s + 300)]
    out: dict = {"queued": len(queue), "window": len(window), "companies": 0, "added_ready": 0,
                 "added_by_channel": Counter(), "emails_found": 0, "reached_target": 0, "reached_ids": [],
                 "buckets": Counter(), "linkedin_queries": 0, "errors": 0, "stopped": None, "search_down_companies": 0}
    try:
        for i, cid in enumerate(window):
            if time.time() - t0 > budget_s:
                out["stopped"] = f"time budget ({budget_s}s) reached"
                break
            down = web.search_down()
            if down:
                out["search_down_companies"] += 1
                if out["search_down_companies"] > DOWN_TAIL:
                    out["stopped"] = "every keyless search backend is cooling down — ended early"
                    break
            try:
                r = refer_company(cid, search_ok=not down)
            except Exception as e:  # noqa: BLE001 — one company must not end the cycle
                log.warning("referral job: company %s failed: %s", cid, e)
                out["errors"] += 1
                continue
            finally:
                release(cid, owner)
            out["companies"] += 1
            out["buckets"][r.get("bucket") or "other"] += 1
            out["linkedin_queries"] += r.get("queries", 0)
            for ch, n in (r.get("added_by_channel") or {}).items():
                out["added_by_channel"][ch] += n
                out["added_ready"] += n
            out["emails_found"] += r.get("emails_found", 0)
            if r.get("reached_target"):
                out["reached_target"] += 1
                out["reached_ids"].append(cid)
            if r.get("search_down") and not down:
                out["search_down_companies"] += 1
    finally:
        for cid in window:
            release(cid, owner)
    out["added_by_channel"] = dict(out["added_by_channel"])
    out["linkedin_added"] = out["added_by_channel"].get("linkedin", 0)
    out["buckets"] = dict(out["buckets"])
    out["seconds"] = round(time.time() - t0)
    _log_run("cycle", out)
    log.info("referral job cycle: %s", out)
    return out


def job_active(*, within_minutes: int = 30) -> bool:
    """Is the referral job working? Its agent is loaded in launchd, or a cycle was logged
    in the last `within_minutes` (a cycle run by hand counts too). The tick's people step
    yields to it — a smaller share, and never a company the job has claimed."""
    import subprocess

    try:
        r = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=10)
        if any(ln.split("\t")[-1] == "com.zonuly.referrals" for ln in r.stdout.splitlines()):
            return True
    except Exception:  # noqa: BLE001 — not macOS: fall back to the run log
        pass
    return any(r.get("kind") == "cycle" for r in runs_since(utcnow() - timedelta(minutes=within_minutes)))


# ------------------------------------------------------------------ re-verification

def _due_referrers(session, limit: int) -> list[Contact]:
    cutoff = utcnow() - timedelta(days=REVERIFY_DAYS)
    rows = session.exec(select(Contact).where(
        col(Contact.affiliation).in_(guardrails.READY),
        (col(Contact.affiliation_checked_at).is_(None)) | (col(Contact.affiliation_checked_at) < cutoff))
        .order_by(col(Contact.affiliation_checked_at)).limit(limit * 3)).all()
    return [p for p in rows if guardrails.is_ready(p)][:limit]


def _reverify_linkedin(session, p: Contact, company: Company) -> str:
    """confirmed | downgraded | inconclusive — their public headline, read again."""
    from jobhunter.contacts import linkedin_public
    from jobhunter.research import web

    slug = (p.linkedin or "").rstrip("/").split("/in/")[-1].lower()
    res = web.search(f'"{p.name}" "{company.name}" site:linkedin.com/in', limit=10, only=web.KEYLESS, fresh=True)
    hits = [r for r in res.get("results") or [] if slug and slug in (r.get("url") or "").lower()]
    if not hits:
        tries = (p.research_notes or "").count("[reverify] inconclusive")
        if tries >= 1:
            guardrails.downgrade_affiliation(session, p, reason="public headline not found in two re-checks",
                                             source=p.linkedin)
            return "downgraded"
        p.research_notes = f"{p.research_notes or ''}\n[reverify] inconclusive {utcnow():%Y-%m-%d}".strip()
        session.add(p)
        return "inconclusive"
    people = linkedin_public.people_from_results(hits, company.name, company.domain)
    if people:
        aff = guardrails.affiliation(company_name=company.name, domain=company.domain,
                                     linkedin_headline=people[0]["headline"])
        if aff.label in guardrails.READY:
            p.affiliation_evidence = f"re-checked {utcnow():%Y-%m-%d}: {aff.evidence}"[:300]
            p.affiliation_checked_at = utcnow()
            session.add(p)
            return "confirmed"
    guardrails.downgrade_affiliation(session, p, reason="public headline no longer names the company",
                                     source=p.linkedin)
    return "downgraded"


def _reverify_page(session, p: Contact) -> str:
    """The page that published their address (or named them) still does."""
    from jobhunter.research import web

    m = re.search(r"(?:address published at|named on their own site:)\s*(\S+)",
                  f"{p.research_notes or ''} {p.affiliation_evidence or ''}")
    if not m or not p.email:
        return "inconclusive"
    page = web.read(m.group(1), fresh=True, timeout=40)
    text = (page.get("text") or "").lower()
    if not text:
        return "inconclusive"
    if p.email.lower() in text:
        p.affiliation_checked_at = utcnow()
        session.add(p)
        return "confirmed"
    guardrails.downgrade_affiliation(session, p, reason=f"{m.group(1)[:80]} no longer shows their address",
                                     source=m.group(1))
    return "downgraded"


def reverify_referrers(limit: int = 150, *, github_reserve: int = 1500) -> dict:
    """Ready referrers whose evidence is older than REVERIFY_DAYS, re-confirmed."""
    import httpx

    from jobhunter.contacts import affiliation as gh_aff

    init_db()
    out = {"due": 0, "confirmed": 0, "downgraded": 0, "inconclusive": 0, "by_source": Counter()}
    with get_session() as session:
        due = _due_referrers(session, limit)
        out["due"] = len(due)
        gh_by_company: dict[int, set[int]] = {}
        others = []
        for p in due:
            if (p.github or p.handle) and p.affiliation_source in ("org-member", "recent-commit", "company-address",
                                                                    "profile-company"):
                gh_by_company.setdefault(p.company_id, set()).add(p.id)
            else:
                others.append(p.id)
    # GitHub: the org's membership, commits in the last 12 months and the profile, read again
    if gh_by_company:
        budget = gh_aff.Budget(github_reserve)
        with httpx.Client(follow_redirects=True) as client:
            for cid, pids in gh_by_company.items():
                if not budget.ok():
                    out["github_stopped"] = budget.stopped or "budget reserve reached"
                    break
                try:
                    gh_aff.check_company(cid, client, budget, fresh_ids=pids)
                except Exception as e:  # noqa: BLE001
                    log.warning("reverify: github company %s failed: %s", cid, e)
                    continue
                with get_session() as session:
                    for pid in pids:
                        p = session.get(Contact, pid)
                        ok = p is not None and guardrails.is_ready(p)
                        if p is not None and not ok:
                            guardrails.reject(session, company_id=cid, contact_id=pid, field="contact.affiliation",
                                              value="ready", reason=f"re-check: {p.affiliation_evidence or ''}"[:240],
                                              channel="reverify")
                        out["confirmed" if ok else "downgraded"] += 1
                        out["by_source"]["github"] += 1
                    session.commit()
    for pid in others:
        with get_session() as session:
            p = session.get(Contact, pid)
            c = session.get(Company, p.company_id) if p else None
            if p is None or c is None:
                continue
            try:
                if p.linkedin and p.affiliation_source == "linkedin-headline":
                    verdict = _reverify_linkedin(session, p, c)
                    out["by_source"]["linkedin"] += 1
                else:
                    verdict = _reverify_page(session, p)
                    out["by_source"]["page"] += 1
            except Exception as e:  # noqa: BLE001
                log.warning("reverify: contact %s failed: %s", pid, e)
                verdict = "inconclusive"
            out[verdict] += 1
            session.commit()
    out["by_source"] = dict(out["by_source"])
    _log_run("reverify", out)
    return out


# ------------------------------------------------------------------ rolling company re-check

def _site_answers(url: str) -> tuple[bool, str]:
    import httpx

    from jobhunter.research import web

    if not url.startswith("http"):
        url = f"https://{url}"
    try:
        r = httpx.get(url, follow_redirects=True, timeout=15,
                      headers={"User-Agent": "Mozilla/5.0 (Macintosh) ZoNuLy/recheck"})
        # a bot wall (401/403/429) is a live site that does not want robots
        if r.status_code < 400 or r.status_code in (401, 403, 405, 429):
            return True, f"HTTP {r.status_code}"
        why = f"HTTP {r.status_code}"
    except Exception as e:  # noqa: BLE001
        why = type(e).__name__
    page = web.read(url, prefer="jina", timeout=40, fresh=True)
    if (page.get("text") or "").strip() and not guardrails.bot_wall(page.get("text")):
        return True, f"direct {why}; Jina read it"
    return False, f"does not answer ({why}; Jina: {page.get('error') or 'empty'})"


def revalidate_companies(limit: int = REVALIDATE_PER_TICK, *, hiring_recheck_days: int = 14,
                         budget_s: int | None = None) -> dict:
    """The rolling re-check: never-checked first, then oldest validated_at. Stops after
    `budget_s` seconds (the tick's companies share) — the rest are next tick's."""
    t0 = time.time()
    from jobhunter import hiring_verify

    init_db()
    with get_session() as session:
        rows = session.exec(select(Company).where(col(Company.status).is_(None))).all()
        rows.sort(key=lambda c: (c.validated_at is not None, c.validated_at or datetime.min,
                                 *people_order(c)))
        ids = [c.id for c in rows[:limit]]
        never = sum(1 for c in rows if c.validated_at is None)
    out = {"checked": 0, "never_checked_before": never, "passed": 0, "failed": Counter(), "newly_failed": 0,
           "recovered": 0, "hiring_reverified": 0}
    for cid in ids:
        if budget_s and time.time() - t0 > budget_s:
            out["stopped"] = f"time budget ({budget_s}s)"
            break
        with get_session() as session:
            c = session.get(Company, cid)
            website, desc_ok = c.website, bool(c.description) and bool(
                guardrails.description(c.description, name=c.name, domain=c.domain, strict=False))
            has_desc = bool(c.description)
            recheck_hiring = (c.hiring_status == "verified" and (
                c.hiring_checked_at is None or utcnow() - c.hiring_checked_at > timedelta(days=hiring_recheck_days)))
        failed: dict[str, str] = {}
        if website:
            ok, why = _site_answers(website)
            if not ok:
                failed["website"] = why
        if has_desc and not desc_ok:
            failed["description"] = "the description on record no longer passes the validators"
        if recheck_hiring:
            try:
                hiring_verify.verify_company(cid)
                out["hiring_reverified"] += 1
            except Exception as e:  # noqa: BLE001
                log.warning("revalidate: hiring for %s failed: %s", cid, e)
        with get_session() as session:
            c = session.get(Company, cid)
            roles = session.exec(select(Job.id).where(Job.company_id == cid).limit(1)).first()
            if recheck_hiring and c.hiring_status != "verified" and not roles:
                failed["hiring"] = f"careers page no longer lists roles ({c.hiring_status})"
            res = guardrails.set_recheck(session, c, failed)
            session.commit()
        out["checked"] += 1
        out["passed"] += not failed
        for k in failed:
            out["failed"][k] += 1
        out["newly_failed"] += len(res["newly_failed"])
        out["recovered"] += len(res["recovered"])
    out["failed"] = dict(out["failed"])
    _log_run("revalidate", out)
    return out


# ------------------------------------------------------------------ the audit block

def audit_block(session, since: datetime) -> dict:
    """Referrals in the last hour, for audit.py and the /health page."""
    ready_new = [p for p in session.exec(select(Contact).where(col(Contact.created_at) >= since)).all()
                 if guardrails.is_ready(p)]
    writes = session.exec(select(ExtractionWrite).where(col(ExtractionWrite.at) >= since,
                                                        ExtractionWrite.field == "contact.email")).all()
    by_source = Counter(p.source for p in ready_new if p.email)
    by_pattern = sum(1 for w in writes if w.channel == "pattern")
    downgrades = session.exec(select(ExtractionReject).where(
        col(ExtractionReject.at) >= since, ExtractionReject.field == "contact.affiliation")).all()
    became = session.exec(select(Company.id).where(col(Company.complete_at) >= since,
                                                   Company.complete == True)).all()  # noqa: E712
    runs = runs_since(since)
    cyc = [r for r in runs if r.get("kind") == "cycle"]
    # 2026-10-14: by channel (LinkedIn separately), and the companies whose third ready
    # referrer arrived inside the hour
    by_channel = Counter(lead_channel(p.source, p.affiliation_source) for p in ready_new)
    touched = {p.company_id for p in ready_new}
    reached = 0
    if touched:
        rows = session.exec(select(Contact).where(col(Contact.company_id).in_(touched),
                                                  col(Contact.affiliation).in_(guardrails.READY))).all()
        per: dict[int, list] = {}
        for p in rows:
            if guardrails.is_ready(p):
                per.setdefault(p.company_id, []).append(p.created_at or since)
        reached = sum(1 for ts in per.values() if len(ts) >= TARGET and sorted(ts)[TARGET - 1] >= since)
    rv = [r for r in runs if r.get("kind") == "reverify"]
    rc = [r for r in runs if r.get("kind") == "revalidate"]
    ppl = [r for r in runs if r.get("kind") == "people"]
    # companies that moved from 0 ready-with-email to ≥1: against the last audit's set
    now_ids = {cid for cid, (_, e) in guardrails.ready_counts(session).items() if e >= 1}
    try:
        prev_ids = set(json.loads(get_setting(session, "referrals:with_email_ids", "") or "[]"))
    except json.JSONDecodeError:
        prev_ids = set()
    set_setting(session, "referrals:with_email_ids", json.dumps(sorted(now_ids)))
    return {
        "ready_added": len(ready_new),
        "ready_added_with_email": sum(1 for p in ready_new if p.email),
        "addresses_found": {"by_pattern": by_pattern, "by_source": dict(by_source.most_common())},
        "ready_by_channel": dict(by_channel.most_common()),
        "linkedin_added": by_channel.get("linkedin", 0),
        "emails_found": len(writes),
        "companies_reached_target": reached,
        "job": {"cycles": len(cyc), "companies": sum(r.get("companies", 0) for r in cyc),
                "added_ready": sum(r.get("added_ready", 0) for r in cyc),
                "emails_found": sum(r.get("emails_found", 0) for r in cyc),
                "reached_target": sum(r.get("reached_target", 0) for r in cyc),
                "stopped": next((r["stopped"] for r in reversed(cyc) if r.get("stopped")), None)},
        "companies_gained_email": len(now_ids - prev_ids) if prev_ids else None,
        "companies_lost_email": len(prev_ids - now_ids) if prev_ids else None,
        "companies_with_email": len(now_ids),
        "companies_became_complete": len(became),
        "people_runs": {"companies": sum(r.get("companies", 0) for r in ppl),
                        "linkedin_down": next((r["linkedin_down"] for r in reversed(ppl) if r.get("linkedin_down")), None)},
        "reverify": {"confirmed": sum(r.get("confirmed", 0) for r in rv),
                     "downgraded": sum(r.get("downgraded", 0) for r in rv),
                     "inconclusive": sum(r.get("inconclusive", 0) for r in rv),
                     "affiliation_rejects": len(downgrades)},
        "revalidate": {"checked": sum(r.get("checked", 0) for r in rc), "passed": sum(r.get("passed", 0) for r in rc),
                       "newly_failed": sum(r.get("newly_failed", 0) for r in rc),
                       "recovered": sum(r.get("recovered", 0) for r in rc)},
    }


__all__ = ["measure", "pattern_step", "people_queue", "people_for_company", "people_step", "reverify_referrers",
           "revalidate_companies", "audit_block", "backup", "TARGET", "run_cycle", "cycle_queue", "refer_company",
           "lead_channel", "bucket", "claim", "release", "claims", "job_active"]
