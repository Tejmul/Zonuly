"""Referral leads from PUBLIC LinkedIn profiles — no login, no session, no ban risk.

MOTIV §6 forbids logged-in LinkedIn automation. This does not log in: a web search
(`site:linkedin.com/in "Company" engineer`) returns public profiles whose result title
is "Name - Role - Company", so we read the name and role straight from the search result
— we never fetch the profile or touch a LinkedIn session. The email is then resolved from
the company's own pattern (contacts/patterns.py), exactly like the GitHub path.

This is the fix for companies with no public GitHub — the ~1,300 the free channels
otherwise leave without a lead. Free and unlimited except for the search engine's own
rate limit, so it is deliberately slow and gapped.
"""

from __future__ import annotations

import logging
import re
import time
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from sqlmodel import col, select

from jobhunter import CONFIG
from jobhunter.db import Company, Contact, get_session, init_db, utcnow

log = logging.getLogger(__name__)

UA = (CONFIG.get("sources") or {}).get(
    "user_agent",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36",
)
DDG_HTML = "https://html.duckduckgo.com/html/"
MIN_GAP_S = float((CONFIG.get("contacts") or {}).get("linkedin_min_gap_seconds", 12))
MAX_PER_COMPANY = int((CONFIG.get("contacts") or {}).get("linkedin_max_per_company", 8))
_last_call = 0.0
# once DuckDuckGo answers with a challenge page it keeps doing so for a while; waiting
# the full gap before every further attempt would turn a 53-company pass into half an
# hour of sleeping, so a throttle is remembered and the Exa path is taken directly
_throttled_until = 0.0
THROTTLE_BACKOFF_S = float((CONFIG.get("contacts") or {}).get("linkedin_throttle_backoff_seconds", 1800))

_RESULT = re.compile(r'result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S)
_SNIPPET = re.compile(r'result__snippet"[^>]*>(.*?)</a>', re.S)
_IN = re.compile(r"(?:[a-z]{2}\.)?linkedin\.com/in/[A-Za-z0-9\-%]+", re.I)
_CHALLENGE = re.compile(r"(anomaly|captcha|challenge|bots use DuckDuckGo)", re.I)
# "Firstname Lastname - Software Engineer - Company | LinkedIn"
_TITLE = re.compile(r"^(?P<name>[^-–|]{2,50}?)\s*[-–|]\s*(?P<role>[^-–|]{2,60}?)(?:\s*[-–|].*)?(?:\s*\|\s*LinkedIn)?\s*$")
# roles that can refer, in priority order
_ROLE_HINT = re.compile(
    r"\b(founder|co[- ]?founder|ceo|cto|chief|vp|head of|director|engineering manager|"
    r"staff|principal|senior|lead|software engineer|swe|sde|backend|frontend|full[- ]?stack|"
    r"platform|infrastructure|ml|ai|data|devops|sre|founding engineer|engineer|developer|recruiter)\b", re.I)
_NAME_OK = re.compile(r"^[A-Z][a-zA-Z.'’\-]+(?:\s+[A-Z][a-zA-Z.'’\-]+){1,3}$")


def _ddg(query: str, timeout: int = 25) -> tuple[str | None, str | None]:
    """One search. scrape.do first when it has budget (rotating IPs, no rate limit), else
    DuckDuckGo direct (free but throttled by IP). Same result HTML either way."""
    from urllib.parse import quote

    from jobhunter.research import web

    # scrape.do fetches the DDG results page through its proxy pool — no IP rate limit
    if web.scrapedo_budget()["daily_left"] > 0:
        html = web.fetch_html_scrapedo(f"{DDG_HTML}?q={quote(query)}", timeout=timeout)
        if html and not _CHALLENGE.search(html[:6000]) and "result__a" in html:
            return html, None

    global _last_call, _throttled_until
    if time.monotonic() < _throttled_until:
        return None, "search rate-limited (backing off); use exa"
    wait = MIN_GAP_S - (time.monotonic() - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.monotonic()
    try:
        r = httpx.get(DDG_HTML, params={"q": query}, headers={"User-Agent": UA},
                      timeout=timeout, follow_redirects=True)
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {str(e)[:100]}"
    if r.status_code != 200 or _CHALLENGE.search(r.text[:6000]):
        _throttled_until = time.monotonic() + THROTTLE_BACKOFF_S
        return None, f"search rate-limited (HTTP {r.status_code}); back off"
    return r.text, None


def _target(href: str) -> str:
    if "uddg=" in href:
        return unquote(parse_qs(urlparse(href).query).get("uddg", [""])[0])
    return href


def _people_from(html: str) -> list[dict]:
    """Extract {name, role, url} from a search page's LinkedIn profile results."""
    out: list[dict] = []
    seen: set[str] = set()
    for href, title in _RESULT.findall(html):
        url = _target(href)
        if not _IN.search(url):
            continue
        url = url.split("?")[0]
        slug = url.rstrip("/").split("/in/")[-1].lower()
        if slug in seen:
            continue
        text = re.sub(r"<[^>]+>", "", title)
        text = re.sub(r"\s+", " ", text).strip()
        m = _TITLE.match(text)
        if not m:
            continue
        name = m.group("name").strip(" .")
        role = m.group("role").strip()
        # a real person name (not "500+ connections" or a company handle)
        if not _NAME_OK.match(name) or not _ROLE_HINT.search(role):
            continue
        seen.add(slug)
        out.append({"name": name, "role": role, "url": url})
    return out


def _people_from_exa(name: str, limit: int, query: str | None = None) -> tuple[list[dict], str | None]:
    """The same question through the research layer's web search (Exa MCP — the
    keyless Agent Reach path — with the keyed API behind it), for the days DuckDuckGo
    answers with a challenge page. A result is a person only when it is a
    linkedin.com/in URL whose headline names a referring role and mentions the company."""
    from jobhunter.research import web

    stem = re.sub(r"[^a-z0-9]", "", name.lower())
    bare = re.sub(r"(ai|labs?|hq|inc|tech|technologies|app)$", "", stem) or stem
    out: list[dict] = []
    seen: set[str] = set()
    query = query or f'site:linkedin.com/in "{name}" (software engineer OR founding engineer OR CTO OR founder)'
    res = web.search(query, limit=max(limit, 10))
    for r in res.get("results") or []:
        url = (r.get("url") or "").split("?")[0]
        if not _IN.search(url):
            continue
        slug = url.rstrip("/").split("/in/")[-1].lower()
        if slug in seen:
            continue
        person = (r.get("title") or "").split("|")[0].split(" - ")[0].strip(" .")
        snippet = (r.get("snippet") or "").strip()
        headline = snippet.splitlines()[0].strip() if snippet else ""
        blob = re.sub(r"[^a-z0-9]", "", snippet.lower())
        if not _NAME_OK.match(person) or not _ROLE_HINT.search(headline) or bare not in blob:
            continue
        seen.add(slug)
        out.append({"name": person, "role": headline[:120], "url": url})
    return out[:limit], res.get("error")


def _search(name: str, query: str, max_people: int) -> tuple[list[dict], str, str | None]:
    """(people, via, error) — DuckDuckGo, then Exa when it is throttled."""
    html, err = _ddg(query)
    if not err:
        return _people_from(html)[:max_people], "ddg", None
    people, exa_err = _people_from_exa(name, max_people, query)
    if not people and exa_err:
        return [], "exa", f"{err}; exa: {exa_err}"
    return people, "exa", None


def _save(company_id: int, people: list[dict], via: str) -> int:
    """Store found profiles as contacts, resolving each address from the company's
    pattern when it is known — MX-checked, labelled guessed, never verified."""
    from jobhunter.contacts import patterns, roles as roleclass, verify

    with get_session() as session:
        c = session.get(Company, company_id)
        domain, pattern = c.domain, c.email_pattern
        existing = {(k.name or "").lower() for k in session.exec(
            select(Contact).where(Contact.company_id == company_id)).all()}
        added = 0
        for p in people:
            if p["name"].lower() in existing:
                continue
            existing.add(p["name"].lower())
            verdict = roleclass.classify(p["role"], name=p["name"])
            email = None
            confidence = "scraped"
            if domain and pattern:
                cands = patterns.candidates(p["name"], domain, pattern)
                if cands:
                    got, v = verify.best_candidate(cands)
                    if got:
                        email, confidence = got, (v.confidence if v else "pattern-guessed")
            session.add(Contact(
                company_id=company_id, name=p["name"], role=p["role"], email=email,
                linkedin=p["url"], source="linkedin-public",
                research_notes=f"found via {via}",
                confidence=confidence if email else "scraped",
                role_class=verdict.role_class, seniority=verdict.seniority,
                referral_rank=verdict.referral_rank, role_evidence=f"LinkedIn: {p['role']}",
                is_recruiter=verdict.role_class in ("recruiter", "tech_recruiter"),
                classified_at=utcnow(),
            ))
            added += 1
        c.contacts_found_at = utcnow()
        session.add(c)
        session.commit()
    return added


def find_for_company(company_id: int, *, max_people: int = MAX_PER_COMPANY) -> dict:
    """Public LinkedIn people at one company, via search, with emails from its pattern."""
    init_db()
    with get_session() as session:
        c = session.get(Company, company_id)
        if c is None:
            return {"error": f"no company {company_id}"}
        name = c.name
    query = f'site:linkedin.com/in "{name}" (software engineer OR founding engineer OR CTO OR founder)'
    people, via, err = _search(name, query, max_people)
    if err:
        return {"company": name, "error": err}
    if not people:
        return {"company": name, "found": 0, "via": via}
    added = _save(company_id, people, via)
    log.info("linkedin_public: %s -> %d people via %s, %d added", name, len(people), via, added)
    return {"company": name, "found": len(people), "added": added, "via": via}


def find_founders(company_id: int, *, max_people: int = 6) -> dict:
    """The founders and the people who run engineering — the ask of last resort when no
    engineer has an address, and the people whose address is most often published."""
    init_db()
    with get_session() as session:
        c = session.get(Company, company_id)
        if c is None:
            return {"error": f"no company {company_id}"}
        name = c.name
    query = (f'site:linkedin.com/in "{name}" (founder OR co-founder OR CEO OR CTO OR '
             f'"head of engineering" OR "VP engineering" OR "head of talent" OR "HR")')
    people, via, err = _search(name, query, max_people)
    if err:
        return {"company": name, "error": err}
    added = _save(company_id, people, via) if people else 0
    log.info("linkedin_public founders: %s -> %d found via %s, %d added", name, len(people), via, added)
    return {"company": name, "found": len(people), "added": added, "via": via}


_TOPUP = "[li-topup]"
TOPUP_DAYS = 14


def _topped_up_recently(notes: str | None, days: int = TOPUP_DAYS) -> bool:
    m = re.search(r"\[li-topup\] (\d{4}-\d{2}-\d{2})", notes or "")
    if not m:
        return False
    from datetime import datetime, timedelta

    return datetime.strptime(m.group(1), "%Y-%m-%d") >= utcnow() - timedelta(days=days)


def top_up(company_id: int, *, want: int = 5) -> dict:
    """More referrers for a company still short of `want` who can refer: the engineer /
    founder search, then founders + engineering leads + recruiters. Each search runs at
    most once per TOPUP_DAYS per company ("[li-topup] <date>" in its notes)."""
    from jobhunter.contacts import roles as roleclass

    init_db()
    with get_session() as session:
        c = session.get(Company, company_id)
        if c is None:
            return {"error": f"no company {company_id}"}
        if _topped_up_recently(c.notes):
            return {"company": c.name, "skipped": "topped up recently", "added": 0}
        have = roleclass.good_referrer_counts(session, [company_id]).get(company_id, 0)
    out = {"company_id": company_id, "had": have, "added": 0}
    if have >= want:
        return out
    for step in (find_for_company, find_founders):
        r = step(company_id)
        out["added"] += r.get("added", 0)
        if r.get("error"):
            out["error"] = r["error"]
            if not out["added"]:
                return out       # the search channel failed (throttled, offline): not marked, retried next pass
        with get_session() as session:
            if roleclass.good_referrer_counts(session, [company_id]).get(company_id, 0) >= want:
                break
    with get_session() as session:
        c = session.get(Company, company_id)
        kept = [ln for ln in (c.notes or "").splitlines() if not ln.startswith(_TOPUP)]
        c.notes = "\n".join(kept + [f"{_TOPUP} {utcnow():%Y-%m-%d}: +{out['added']}"]).strip()
        session.add(c)
        session.commit()
    return out


def find_pending(limit: int = 30, *, tiers: tuple[str, ...] = ("tier1", "tier2", "prospect", "unknown"),
                 list_key: str | None = None, want: int | None = None) -> dict:
    """Companies with roles and no leads yet — and every list row still short of `want`
    people who can refer (topped up) — in db.fill_order: list rows first. Slow: one
    search each, gapped."""
    from jobhunter.contacts import roles as roleclass
    from jobhunter.db import Job, fill_order
    from jobhunter.targeting import TIER_ORDER

    want = want or roleclass.GOOD_REFERRERS
    init_db()
    with get_session() as session:
        stmt = select(Company).where(col(Company.status).is_(None))
        if list_key:
            stmt = stmt.where(Company.list_key == list_key)
        rows = session.exec(stmt).all()
        with_jobs = set(session.exec(select(Job.company_id).distinct()).all())
        with_leads = set(session.exec(select(Contact.company_id).distinct()).all())
        good = roleclass.good_referrer_counts(session, [c.id for c in rows if c.list_key])
        short = {c.id for c in rows if c.list_key and good.get(c.id, 0) < want
                 and not _topped_up_recently(c.notes)}
        pending = [
            c for c in rows
            if c.id in short or (
                not c.list_key and (c.tier or "unknown") in tiers and c.id in with_jobs
                and c.id not in with_leads and "[li]" not in (c.notes or ""))
        ]
        pending.sort(key=lambda c: (*fill_order(c), TIER_ORDER.get(c.tier or "unknown", 9), c.name))
        ids = [c.id for c in pending[:limit]]
    out = {"searched": 0, "found_people": 0, "companies_with_people": 0, "topped_up": 0}
    for cid in ids:
        if cid in short:
            r = top_up(cid, want=want)
            out["searched"] += 1
            out["topped_up"] += 1
            out["found_people"] += r.get("added", 0)
            if r.get("added"):
                out["companies_with_people"] += 1
            if "rate" in (r.get("error") or ""):
                out["stopped"] = "search rate-limited — resumes next tick"
                break
            continue
        r = find_for_company(cid)
        out["searched"] += 1
        if r.get("error") and "rate" in r["error"]:
            out["stopped"] = "search rate-limited — resumes next tick"
            break
        out["found_people"] += r.get("added", 0)
        if r.get("added"):
            out["companies_with_people"] += 1
        with get_session() as session:   # mark searched so the next pass skips it
            c = session.get(Company, cid)
            if c and "[li]" not in (c.notes or ""):
                c.notes = (c.notes + "\n" if c.notes else "") + "[li] searched"
                session.add(c)
                session.commit()
    return out


__all__ = ["find_for_company", "find_founders", "find_pending"]
