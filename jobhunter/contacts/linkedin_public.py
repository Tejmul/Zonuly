"""Referral leads from PUBLIC LinkedIn profiles — no login, no session, no ban risk.

MOTIV §6 forbids logged-in LinkedIn automation. This does not log in: a web search
(`site:linkedin.com/in "Company" engineer`) returns public profiles whose result title
is "Name - Role - Company", so we read the name and role straight from the search result
— we never fetch the profile or touch a LinkedIn session. The email is then resolved from
the company's own pattern (contacts/patterns.py), exactly like the GitHub path.

This is the fix for companies with no public GitHub — the ~1,300 the free channels
otherwise leave without a lead. 2026-10-08: the search is the research layer's KEYLESS
web search (DuckDuckGo read through Jina, then the hosted Exa MCP — paced there, free),
and a profile is kept only when its headline names the company now (likely-current).
"""

from __future__ import annotations

import logging
import re

from sqlmodel import col, select

from jobhunter import CONFIG
from jobhunter.db import Company, Contact, get_session, init_db, utcnow

log = logging.getLogger(__name__)

MAX_PER_COMPANY = int((CONFIG.get("contacts") or {}).get("linkedin_max_per_company", 8))
_IN = re.compile(r"(?:[a-z]{2}\.)?linkedin\.com/in/[A-Za-z0-9\-%]+", re.I)
# roles that can refer, in priority order
_ROLE_HINT = re.compile(
    r"\b(founder|co[- ]?founder|ceo|cto|chief|vp|head of|director|engineering manager|"
    r"staff|principal|senior|lead|software engineer|swe|sde|backend|frontend|full[- ]?stack|"
    r"platform|infrastructure|ml|ai|data|devops|sre|founding engineer|engineer|developer|recruiter)\b", re.I)
_NAME_OK = re.compile(r"^[A-Z][a-zA-Z.'’\-]+(?:\s+[A-Z][a-zA-Z.'’\-]+){1,3}$")


_EXPERIENCE = re.compile(r"Experience:\s*([^·|\n]{2,80})", re.I)
# the hosted Exa MCP's profile card: "### AI Engineer - [Sarvam](https://…/company/…) (Current)"
_CURRENT = re.compile(r"###\s*([^\n]+?)\s+-\s+\[([^\]]{2,80})\]\([^)]*\)\s*\(Current\)", re.I)
# 2026-10-16, the card Exa returns now: "### [Rupeek](…/company/rupeek)" then, under it,
# "#### Director of Engineering (Current)" — the position under the company heading
_CO_BLOCK = re.compile(r"^###\s*\[([^\]]{2,80})\]\([^)]*\)\s*$(.*?)(?=^###\s|\Z)", re.M | re.S)
_CUR_ROLE = re.compile(r"^####\s*(.+?)\s*\(Current\)\s*$", re.M)


def _current_card(snippet: str, company: str, domain: str | None) -> tuple[str, str] | None:
    """(role, evidence) when the card lists a current position under THIS company's heading."""
    from jobhunter import guardrails

    for m in _CO_BLOCK.finditer(snippet or ""):
        if not guardrails.names_company(m.group(1), company, domain):
            continue
        r = _CUR_ROLE.search(m.group(2))
        if r:
            role = r.group(1).strip()
            return role, f"{role} at {m.group(1).strip()} (current)"
    return None


def _headline_line(snippet: str) -> str:
    """The card's headline: the first line after the '# Name' heading."""
    lines = [x.strip() for x in (snippet or "").splitlines() if x.strip()]
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
    return lines[0] if lines else ""


_LI_SUFFIX = re.compile(r"\s*[-–|·]\s*LinkedIn(?:\s+[A-Z][a-z]+)?\s*$")


#: guardrails._EX ends in \b, so "Ex- Google, Rupeek" and "Ex - Myntra" slip past it
#: (no word boundary after "ex-"); this one catches them (2026-10-16)
_FORMER = re.compile(r"\b(?:ex\s*[-–—:]|ex\s+(?=[A-Z@])|former(?:ly)?\b|previously\b|prev\.|alum(?:ni|nus)?\b|past\b)", re.I)


def _former(text: str) -> bool:
    from jobhunter import guardrails

    return bool(guardrails._EX.search(text) or _FORMER.search(text))


def _segment_names(segments: list[str], company: str, domain: str | None) -> str | None:
    """The first headline segment that names the company and not as a former employer."""
    from jobhunter import guardrails

    for seg in segments:
        if guardrails.names_company(seg, company, domain) and not _former(seg):
            return seg
    return None


def people_from_results(results: list[dict], company: str, domain: str | None = None) -> list[dict]:
    """{name, role, url, headline} from web-search hits on linkedin.com/in — a person only
    when the result's own headline ("Name - Role - Company | LinkedIn", the profile's
    "(Current)" position, or its "Experience:" line) names THIS company, and not as a
    former employer. Pure, so the tests call it."""
    out: list[dict] = []
    seen: set[str] = set()
    for r in results or []:
        url = (r.get("url") or "").split("?")[0]
        if not _IN.search(url):
            continue
        slug = url.rstrip("/").split("/in/")[-1].lower()
        if slug in seen:
            continue
        title = _LI_SUFFIX.sub("", (r.get("title") or "").strip())
        title = re.sub(r"\s*\.\.\.$", "", title)
        parts = [x.strip(" .") for x in re.split(r"\s+[-–|]\s+|\s*\|\s*", title) if x.strip(" .")]
        if not parts:
            continue
        person = parts[0]
        snippet = (r.get("snippet") or "").strip()
        first_line = snippet.splitlines()[0].strip() if snippet else ""
        cur = _CURRENT.search(snippet)
        exp = _EXPERIENCE.search(snippet)
        hit = None
        role = ""
        from jobhunter import guardrails

        if cur and guardrails.names_company(cur.group(2), company, domain):
            role, hit = cur.group(1).strip(), f"{cur.group(1).strip()} at {cur.group(2).strip()} (current)"
        if not hit:
            card = _current_card(snippet, company, domain)
            if card:
                role, hit = card
        if first_line.startswith("# "):
            first_line = _headline_line(snippet)
        if not hit:
            segs = parts[1:] + [x.strip() for x in re.split(r"\s*\|\s*", first_line) if x.strip()]
            seg = _segment_names(segs, company, domain)
            if seg:
                hit = seg
                role = next((x for x in segs if _ROLE_HINT.search(x)), seg)
        if not hit and exp and _segment_names([exp.group(1)], company, domain):
            hit = f"Experience: {exp.group(1).strip()}"
            role = next((x for x in parts[1:] if _ROLE_HINT.search(x)), first_line or hit)
        if not _NAME_OK.match(person) or not hit:
            continue
        seen.add(slug)
        out.append({"name": person, "role": role[:120], "url": url, "headline": hit[:300]})
    return out


def _search(name: str, query: str, max_people: int, domain: str | None = None) -> tuple[list[dict], str, str | None]:
    """(people, via, error) through the research layer's KEYLESS web search — DuckDuckGo
    read through Jina first, then the hosted Exa MCP — never a metered backend."""
    from jobhunter.research import web

    if web.search_down():
        return [], "keyless", "search rate-limited: every keyless backend is cooling down"
    res = web.search(query, limit=max(max_people, 10), only=web.KEYLESS)
    people = people_from_results(res.get("results") or [], name, domain)[:max_people]
    if not people and res.get("error") and not res.get("results"):
        return [], res.get("backend") or "keyless", f"search failed: {res['error'][:160]}"
    return people, res.get("backend") or "keyless", None


#: the queries, best referrers first (2026-10-08): engineers, engineering leads, founders, recruiters
ROLE_QUERIES = {
    "engineers": "engineer",
    "leads": '"engineering manager" OR "senior engineer" OR "staff engineer" OR "tech lead"',
    "founders": "founder OR co-founder OR CTO",
    "recruiters": 'recruiter OR "talent acquisition"',
}


#: 2026-10-14, the dedicated referral job (referrals.run_cycle): several query SHAPES per
#: company — the bare company + city first ('"Acme" Bengaluru site:linkedin.com/in'), then
#: the role words a referrer's headline carries, best referrers first
REFERRAL_QUERIES = {
    "city": "",
    "engineers": "engineer",
    "sde": 'SDE OR "software development engineer"',
    "ml": '"ML engineer" OR "machine learning" OR "AI engineer"',
    "founders": "founder OR co-founder OR CTO",
    "managers": '"engineering manager" OR "tech lead" OR "head of engineering"',
    "recruiters": 'recruiter OR "talent acquisition"',
}


def _query(name: str, words: str, where: str) -> str:
    return " ".join(x for x in (f'"{name}"', words, where.strip(), "site:linkedin.com/in") if x)


def search_role(company_id: int, kind: str, *, max_people: int = MAX_PER_COMPANY,
                queries: dict[str, str] | None = None, city: str | None = None) -> dict:
    """One keyless search for one kind of referrer at one company — with the city for
    a Bengaluru / Hyderabad company, since "Acme engineer" alone finds every Acme.
    `queries` picks the vocabulary (ROLE_QUERIES by default, REFERRAL_QUERIES for the
    referral job); `city` overrides the company's own city text."""
    queries = queries or ROLE_QUERIES
    init_db()
    with get_session() as session:
        c = session.get(Company, company_id)
        if c is None:
            return {"error": f"no company {company_id}"}
        name, domain, region = c.name, c.domain, c.hq_region
        city = city if city is not None else c.city
    where = f" {city}" if city and (region == "india" or city in ("Bengaluru", "Bangalore", "Hyderabad")) else ""
    words = queries[kind]
    if not where and not words:
        words = "engineer"          # a bare company name with no city is every namesake
    query = _query(name, words, where)
    people, via, err = _search(name, query, max_people, domain)
    if where and words and not people and not err:
        # the city narrowed it to nothing: the headline check still keeps it to this company
        people, via, err = _search(name, _query(name, words, ""), max_people, domain)
    if err:
        return {"company": name, "kind": kind, "error": err}
    added = _save(company_id, people, via) if people else 0
    return {"company": name, "kind": kind, "found": len(people), "added": added, "via": via}


def _save(company_id: int, people: list[dict], via: str) -> int:
    """Store found profiles as contacts, resolving each address from the company's
    pattern when it is known — MX-checked, labelled guessed, never verified."""
    from jobhunter import guardrails
    from jobhunter.contacts import patterns, roles as roleclass, verify

    with get_session() as session:
        c = session.get(Company, company_id)
        # only a pattern the company's own addresses confirm (contacts/addresses.py)
        domain, pattern = c.domain, patterns.confirmed(c)
        existing = {(k.name or "").lower() for k in session.exec(
            select(Contact).where(Contact.company_id == company_id)).all()}
        added = 0
        for p in people:
            if p["name"].lower() in existing:
                continue
            existing.add(p["name"].lower())
            email = None
            confidence = None
            if domain and pattern:
                cands = patterns.candidates(p["name"], domain, pattern)
                if cands:
                    got, v = verify.best_candidate(cands)
                    if got:
                        email, confidence = got, (v.confidence if v else "pattern-guessed")
            g = guardrails.vet_contact(session, company=c, name=p["name"], email_addr=email,
                                       source="pattern" if email else "linkedin-public", channel=f"linkedin:{via}",
                                       url=p["url"], name_source="linkedin",
                                       linkedin_headline=p.get("headline") or p["role"], smtp=confidence)
            if g is None or not g.get("name"):
                continue      # a LinkedIn lead is a person or nothing
            verdict = roleclass.classify(p["role"], name=g["name"])
            session.add(Contact(
                company_id=company_id, name=g["name"], role=p["role"], email=g.get("email"),
                linkedin=p["url"], source="linkedin-public",
                research_notes=f"found via {via}",
                confidence=g["confidence"] if g.get("email") else "scraped",
                role_class=verdict.role_class, seniority=verdict.seniority,
                referral_rank=verdict.referral_rank, role_evidence=f"LinkedIn: {p['role']}",
                is_recruiter=verdict.role_class in ("recruiter", "tech_recruiter"),
                classified_at=utcnow(),
                name_source="linkedin", email_kind=g.get("email_kind"), affiliation=g["affiliation"],
                affiliation_source=g["affiliation_source"], affiliation_evidence=g["affiliation_evidence"],
                affiliation_checked_at=g["affiliation_checked_at"],
            ))
            added += 1
        c.contacts_found_at = utcnow()
        session.add(c)
        session.commit()
    return added


#: 2026-10-15: the keyless engines (DDG, Startpage, Bing, Mojeek, Brave) all return zero
#: linkedin.com/in results from this machine, so the referral job found 4 people in 100
#: companies. One Exa `category: people` search returns ~10–15 profile cards for a company
#: (Razorpay: 15 cards, 13 current). It spends the existing daily Exa cap
#: (research.exa_daily_cap) and leaves EXA_PEOPLE_RESERVE of it for everything else.
_LC = CONFIG.get("contacts") or {}
EXA_PEOPLE_RESERVE = int(_LC.get("exa_people_reserve", 15))
EXA_PEOPLE_MAX = int(_LC.get("exa_people_max_per_company", 12))
EXA_PEOPLE_MODE = str(_LC.get("exa_people_mode", "linkedin-domain"))


def exa_people_available() -> bool:
    from jobhunter.research import web

    return web.exa_budget()["left"] > EXA_PEOPLE_RESERVE


def search_people_exa(company_id: int, *, city: str | None = None, max_people: int = EXA_PEOPLE_MAX) -> dict:
    """One metered Exa people search for one company. Every card still has to name THIS
    company as a current position (people_from_results), exactly like a keyless hit."""
    from jobhunter.research import web

    init_db()
    with get_session() as session:
        c = session.get(Company, company_id)
        if c is None:
            return {"error": f"no company {company_id}"}
        name, domain = c.name, c.domain
    if not exa_people_available():
        return {"company": name, "kind": "exa-people", "error": "exa people: daily allowance used"}
    where = f" in {city}, India" if city else ""
    who = f"{name} ({domain})" if domain else name
    q = f"engineers, engineering leaders and founders currently working at {who}{where}"
    # 2026-10-16: `category: "people"` answers 400 INVALID_FLAGS on this key ("needs a sierra
    # main lane"); the same people library comes back with includeDomains linkedin.com,
    # profile cards and all (Rupeek: 15 profiles, 5 current, $0.012)
    if EXA_PEOPLE_MODE == "category":
        hits = web._exa_api_search(q, 15, 60, category="people")
    else:
        hits = web._exa_api_search(q, 15, 60, include_domains=["linkedin.com"])
    if not hits:
        err = (web._errors[-1] if web._errors else "no results")[:160]
        return {"company": name, "kind": "exa-people", "found": 0, "added": 0, "error": err}
    rows = [{"url": h.url, "title": h.title, "snippet": h.snippet} for h in hits]
    people = people_from_results(rows, name, domain)[:max_people]
    added = _save(company_id, people, "exa-people") if people else 0
    return {"company": name, "kind": "exa-people", "found": len(people), "added": added, "via": "exa-people"}


def find_for_company(company_id: int, *, max_people: int = MAX_PER_COMPANY) -> dict:
    """Public LinkedIn people at one company, via search, with emails from its pattern."""
    init_db()
    with get_session() as session:
        c = session.get(Company, company_id)
        if c is None:
            return {"error": f"no company {company_id}"}
        name, c_domain = c.name, c.domain
    query = f'site:linkedin.com/in "{name}" (software engineer OR founding engineer OR CTO OR founder)'
    people, via, err = _search(name, query, max_people, c_domain)
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
        name, c_domain = c.name, c.domain
    query = (f'site:linkedin.com/in "{name}" (founder OR co-founder OR CEO OR CTO OR '
             f'"head of engineering" OR "VP engineering" OR "head of talent" OR "HR")')
    people, via, err = _search(name, query, max_people, c_domain)
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


__all__ = ["find_for_company", "find_founders", "find_pending", "search_role", "people_from_results",
           "ROLE_QUERIES", "REFERRAL_QUERIES"]
