"""company-people — the company's LinkedIn "People" tab, rebuilt without our LinkedIn account.

LinkedIn's People tab is behind a login. Our account is never used (Tejmul, 2026-10-18:
"don't even keep my account in the first place"). So the list is assembled from every
place a company's people are named in public, then checked one by one:

    source           what it gives                                       login?
    linkedin-company the company page's employee cards                   no (guest page)
    search           site:linkedin.com/in "Company" <role> [city] —       no
                     segmented by role, the way Apify's SERP actors do
    exa              one metered Exa people search (opt-in)               no
    jobs             the company's open LinkedIn jobs → "hiring team"     no (guest endpoint)
    team             the company's own team / about pages                no
    yc               active founders on its YC page                      no
    github           org members + recent committers                     no (GitHub token)
    known            people we already hold for the company              —
    apify            Apify's company-employees actor: the People tab      not OURS — Apify's
                     itself, 25 a page (opt-in, APIFY_TOKEN, $ capped)    vendor accounts

Then `verify`: every candidate with a LinkedIn URL is opened as a public profile (paced,
breaker-guarded); its JSON-LD says whether the company is a CURRENT position (no end
year). The record's `status` is the strongest evidence held:

    verified-current   profile lists the company with no end date, or the company's own
                       team page names them, or Apify's People-tab row lists it as current
    likely-current     a search headline names the company as current, an active YC
                       founder, a job's hiring team, a recent committer / org member
    unverified         named somewhere, nothing yet says they are there now
    former             the profile shows the company with an end date — dropped unless
                       include_former

Nothing is invented: every record lists its `evidence` (source, quote, url).
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Iterable

from jobhunter.actors import linkedin as L
from jobhunter.actors.core import Blocked, Run

log = logging.getLogger(__name__)

ALL_SOURCES = ("known", "linkedin-company", "search", "jobs", "team", "yc", "github")
OPT_IN_SOURCES = ("exa", "apify")
STATUS_ORDER = {"verified-current": 0, "likely-current": 1, "unverified": 2, "former": 3}

#: role segments for the search source — Apify's "auto query segmentation" idea: one
#: query per kind of person, because a bare company name returns the same top 10
SEGMENTS = {
    "all": "",
    "engineers": 'engineer OR developer OR SDE',
    "ml": '"machine learning" OR "AI engineer" OR "data scientist"',
    "leads": '"engineering manager" OR "tech lead" OR "head of engineering" OR staff OR principal',
    "founders": "founder OR co-founder OR CTO OR CEO",
    "recruiters": 'recruiter OR "talent acquisition" OR "people partner"',
}


#: 2026-10-19 (Tejmul): an India company's people are searched in its own city only — a
#: Bengaluru company's Bengaluru staff, a Hyderabad company's Hyderabad staff. The first name
#: is what Apify gets; the rest are spellings a LinkedIn location may carry instead.
INDIA_CITIES = {
    "bengaluru": ["Bengaluru", "Bangalore"],
    "hyderabad": ["Hyderabad", "Secunderabad"],
}


_ALIASES = {n for ns in INDIA_CITIES.values() for n in ns[1:]}


def city_locations(city: str | None) -> list[str] | None:
    """The location filter an India company's city implies, else None (no filter)."""
    names = [n for key, ns in INDIA_CITIES.items() if key in (city or "").lower() for n in ns]
    return names or None


def _norm_name(n: str | None) -> str:
    return re.sub(r"[^a-z]", "", (n or "").lower())


class Book:
    """The merged candidate list: one entry per person, keyed by LinkedIn slug, else name."""

    def __init__(self):
        self.by_key: dict[str, dict] = {}
        self.slug_of_name: dict[str, str] = {}

    def add(self, *, name: str | None, source: str, title: str | None = None, linkedin_url: str | None = None,
            url: str | None = None, quote: str | None = None, strength: str = "unverified", **extra) -> None:
        slug = L.profile_slug(linkedin_url or "") if linkedin_url else None
        nk = _norm_name(name)
        if not slug and not nk:
            return
        key = f"in:{slug}" if slug else (f"in:{self.slug_of_name[nk]}" if nk in self.slug_of_name else f"name:{nk}")
        if slug and nk and f"name:{nk}" in self.by_key:          # a name-only entry meets its profile
            old = self.by_key.pop(f"name:{nk}")
            self.by_key[key] = old
        rec = self.by_key.setdefault(key, {"name": None, "title": None, "linkedin_url": None, "evidence": [],
                                           "sources": [], "status": "unverified"})
        if slug:
            rec["linkedin_url"] = L.profile_url(slug)
            if nk:
                self.slug_of_name[nk] = slug
        rec["name"] = rec["name"] or name
        rec["title"] = rec["title"] or title
        for k, v in extra.items():
            if v is not None and rec.get(k) is None:
                rec[k] = v
        if source not in rec["sources"]:
            rec["sources"].append(source)
        rec["evidence"].append({"source": source, "quote": (quote or title or "")[:240], "url": url or linkedin_url})
        if STATUS_ORDER[strength] < STATUS_ORDER[rec["status"]]:
            rec["status"] = strength


# ------------------------------------------------------------------ sources

def _src_known(book: Book, known: Iterable[dict], **_) -> int:
    n = 0
    for k in known:
        book.add(name=k.get("name"), title=k.get("role"), linkedin_url=k.get("linkedin"), source="known",
                 quote=f"already held ({k.get('confidence') or 'scraped'})", github=k.get("github"))
        n += 1
    return n


def _src_linkedin_company(book: Book, *, run: Run, company: str, slug: str | None, **_) -> int:
    if not slug:
        return 0
    co = L.fetch_company(run.fetcher, slug)
    if not co:
        return 0
    run.stats["company"] = {k: co.get(k) for k in ("name", "employees_on_linkedin", "industry", "headquarters", "website")}
    for e in co["employee_profiles"]:
        book.add(name=e.get("card"), linkedin_url=e["url"], source="linkedin-company",
                 url=co["source_url"], quote=f"listed on the {co.get('name') or company} LinkedIn page")
    return len(co["employee_profiles"])


def _src_search(book: Book, *, company: str, domain: str | None, city: str | None, segments: Iterable[str],
                slug: str | None = None, **_) -> int:
    from jobhunter.contacts import linkedin_public as lp
    from jobhunter.research import web

    n = 0
    for seg in segments:
        if web.search_down():
            break
        for where in ([f" {city}", ""] if city else [""]):
            res = web.search(lp._query(company, SEGMENTS.get(seg, seg), where), limit=10, only=web.KEYLESS)
            hits = res.get("results") or []
            for p in lp.people_from_results(hits, company, domain):   # headline names the company now
                # a headline names the company by NAME only — "Braintrust" (braintrust.dev)
                # brought back Braintrust Network, a French "braintrust" and a board director
                # (2026-10-18), so it stays unverified until the profile links the position
                # to the company's own page
                book.add(name=p["name"], title=p["role"], linkedin_url=p["url"], source="search",
                         quote=p.get("headline") or p["role"], strength="unverified")
                n += 1
            if hits:
                break        # the city query answered; no need for the wider one
    return n


def _src_exa(book: Book, *, company: str, domain: str | None, city: str | None, **_) -> int:
    from jobhunter.contacts import linkedin_public as lp
    from jobhunter.research import web

    if not lp.exa_people_available():
        raise RuntimeError("exa daily allowance used")
    who = f"{company} ({domain})" if domain else company
    where = f" in {city}" if city else ""
    hits = web._exa_api_search(f"engineers, engineering leaders and founders currently working at {who}{where}",
                               15, 60, include_domains=["linkedin.com"]) or []
    rows = [{"url": h.url, "title": h.title, "snippet": h.snippet} for h in hits]
    n = 0
    for p in lp.people_from_results(rows, company, domain):
        book.add(name=p["name"], title=p["role"], linkedin_url=p["url"], source="exa",
                 quote=p.get("headline") or p["role"], strength="likely-current")
        n += 1
    return n


def _src_jobs(book: Book, *, run: Run, company: str, domain: str | None, max_jobs: int = 6, **_) -> int:
    """The company's open jobs on LinkedIn; a job that shows its hiring team names a person
    who is there now and is hiring — the warmest cold contact there is."""
    from jobhunter import guardrails

    f = run.fetcher
    page = f.get(f"{L.BASE}/jobs-guest/jobs/api/seeMoreJobPostings/search",
                 params={"keywords": f'"{company}"', "start": 0})
    cards = [c for c in (L._parse_cards(page.text) if page else [])
             if c.get("company") and guardrails.names_company(c["company"], company, domain)]
    run.stats["jobs_at_company"] = len(cards)
    n = 0
    for c in cards[:max_jobs]:
        d = L.job_detail(f, c["job_id"]) or {}
        p = d.get("job_poster")
        if p:
            book.add(name=p["name"], title=p.get("title"), linkedin_url=p.get("linkedin_url"), source="jobs",
                     url=c["url"], quote=f"hiring team on \"{c['title']}\"", strength="likely-current",
                     hiring_for=c["title"])
            n += 1
    return n


def _src_team(book: Book, *, company: str, domain: str | None, website: str | None, **_) -> int:
    from jobhunter.contacts import team_people

    if not website:
        return 0
    out = team_people.read_team(website, company, domain)
    for p in out.get("people") or []:
        book.add(name=p["name"], title=p.get("title"), source="team", url=p.get("url"),
                 quote=f"named on {company}'s own site as {p.get('title')}", strength="verified-current")
    return len(out.get("people") or [])


def _src_yc(book: Book, *, company: str, domain: str | None, website: str | None, **_) -> int:
    from jobhunter.contacts import team_people

    slug = team_people.yc_slug(website, domain)
    if not slug:
        return 0
    out = team_people.yc_founders(slug, company)
    for p in out.get("people") or []:
        book.add(name=p["name"], title=p.get("title"), linkedin_url=p.get("linkedin"), source="yc",
                 url=out.get("url"), quote=f"active founder on YC page ({p.get('title')})", strength="likely-current")
    return len(out.get("people") or [])


def _src_github(book: Book, *, company: str, website: str | None, github_org: str | None, **_) -> int:
    from jobhunter.contacts import github_miner
    from jobhunter.scrapers.base import make_client

    async def go():
        async with make_client() as http:
            return await github_miner.mine(http, company, website, org=github_org, limit=20)

    org, people = asyncio.run(go())
    for p in people:
        if not p.name:
            continue
        why = ("public member of the GitHub org " + org) if p.member else (
            f"committed to {org}/{p.commit_repo}" + (f" on {p.last_commit_at:%Y-%m-%d}" if p.last_commit_at else ""))
        book.add(name=p.name, title=(p.bio or "")[:120] or None, source="github", url=f"https://github.com/{p.login}",
                 quote=why, strength="likely-current", github=f"https://github.com/{p.login}", email=p.email)
    return len(people)


def _src_apify(book: Book, *, company: str, slug: str | None, apify_max: int, apify_mode: str,
               titles: list[str] | None, locations: list[str] | None, run: Run, **_) -> int:
    from jobhunter.actors import apify_source

    if not slug:
        raise RuntimeError("apify needs the company's LinkedIn slug (--linkedin)")
    out = apify_source.company_employees(f"{L.BASE}/company/{slug}/", max_items=apify_max, mode=apify_mode,
                                         job_titles=titles,
                                         locations=[x for x in locations if x not in _ALIASES] if locations else None)
    if out.get("error"):
        raise RuntimeError(out["error"])
    run.stats["apify_cost_usd"] = out.get("cost_usd")
    for p in out["people"]:
        cur = next((c for c in p.get("current") or []
                    if c.get("company_url") and L.company_slug(c["company_url"]) == slug), None)
        book.add(name=p["name"], title=(cur or {}).get("title") or p.get("title"), linkedin_url=p.get("linkedin_url"),
                 source="apify", quote=p.get("headline"), url=p.get("linkedin_url"),
                 strength="verified-current" if cur else "likely-current",
                 location=p.get("location"), headline=p.get("headline"), email=p.get("email"))
    return len(out["people"])


SOURCES = {
    "known": _src_known, "linkedin-company": _src_linkedin_company, "search": _src_search, "exa": _src_exa,
    "jobs": _src_jobs, "team": _src_team, "yc": _src_yc, "github": _src_github, "apify": _src_apify,
}


# ------------------------------------------------------------------ verify + shape

def _verify(run: Run, book: Book, *, company: str, slug: str | None, domain: str | None, max_verify: int) -> None:
    todo = [r for r in book.by_key.values() if r["linkedin_url"] and r["status"] != "verified-current"]
    todo.sort(key=lambda r: STATUS_ORDER[r["status"]])
    run.stats.update(verify_opened=0, verify_current=0, verify_former=0, verify_unreadable=0)
    for rec in todo[:max_verify]:
        try:
            prof = L.fetch_profile(run.fetcher, rec["linkedin_url"])
        except Blocked as e:
            run.stats["verify_blocked"] = str(e)
            return
        run.stats["verify_opened"] += 1
        if not prof:
            run.stats["verify_unreadable"] += 1
            continue
        rec.update(name=prof["name"] or rec["name"], headline=prof.get("headline") or rec.get("headline"),
                   location=prof.get("location") or rec.get("location"), education=prof.get("education"),
                   past=prof.get("past"))
        job = L._works_at(prof, company, slug, domain)
        if job:
            run.stats["verify_current"] += 1
            rec["status"] = "verified-current"
            rec["since"] = job.get("start")
            rec["evidence"].append({"source": "profile", "url": rec["linkedin_url"],
                                    "quote": f"profile lists {job['company']} with no end date"
                                             + (f" (since {job['start']})" if job.get("start") else "")})
        elif any(L.company_slug(p.get("company_url") or "") == slug or
                 (p.get("company") and company.lower() in p["company"].lower()) for p in prof.get("past") or []):
            run.stats["verify_former"] += 1
            rec["status"] = "former"
            rec["evidence"].append({"source": "profile", "url": rec["linkedin_url"],
                                    "quote": f"profile lists {company} with an end date"})


def _shape(rec: dict, company: str) -> dict:
    from jobhunter.contacts import roles

    text = rec.get("title") or rec.get("headline")
    v = roles.classify(text, name=rec.get("name"))
    first, _, last = (rec.get("name") or "").partition(" ")
    return {
        "name": rec.get("name"), "first_name": first or None, "last_name": last or None,
        "title": rec.get("title"), "headline": rec.get("headline"), "company": company,
        "status": rec["status"], "since": rec.get("since"),
        "role_class": v.role_class, "seniority": v.seniority, "referral_rank": v.referral_rank,
        "can_refer": v.can_refer,
        "linkedin_url": rec.get("linkedin_url"), "public_identifier": L.profile_slug(rec.get("linkedin_url") or ""),
        "github": rec.get("github"), "email": rec.get("email"), "location": rec.get("location"),
        "education": rec.get("education"), "past": rec.get("past"), "hiring_for": rec.get("hiring_for"),
        "sources": rec["sources"], "evidence": rec["evidence"],
        "source_url": rec.get("linkedin_url") or (rec["evidence"][0]["url"] if rec["evidence"] else None),
    }


def _matches(p: dict, titles: list[str] | None, exclude_titles: list[str] | None, locations: list[str] | None) -> bool:
    blob = " ".join(filter(None, [p.get("title"), p.get("headline")])).lower()
    # Apify filters by title on its side; its short mode returns no title to re-check
    apify_filtered = not blob and "apify" in (p.get("sources") or [])
    if titles and not apify_filtered and not any(t.lower() in blob for t in titles):
        return False
    if exclude_titles and any(t.lower() in blob for t in exclude_titles):
        return False
    # `location` is only ever where the profile says the person is based (LinkedIn JSON-LD
    # addressLocality, Apify's profile location) — never a school, never a search snippet.
    # Under a location filter, unknown is not a match (2026-10-19: working city or nothing).
    if locations and not any(l.lower() in (p.get("location") or "").lower() for l in locations):
        return False
    return True


def company_people(run: Run, *, company: str, domain: str | None = None, website: str | None = None,
                   linkedin: str | None = None, city: str | None = None, github_org: str | None = None,
                   sources: list[str] | None = None, segments: list[str] | None = None,
                   titles: list[str] | None = None, exclude_titles: list[str] | None = None,
                   locations: list[str] | None = None, max_items: int = 100, verify: bool = True,
                   max_verify: int = 15, include_former: bool = False, known: list[dict] | None = None,
                   apify_max: int = 50, apify_mode: str = "short") -> None:
    """Who works at a company now — the People tab, assembled from public sources, verified."""
    slug = L.company_slug(linkedin) if linkedin else None
    chosen = list(sources or ALL_SOURCES)
    if locations is None:
        locations = city_locations(city)
        if locations:
            run.stats["city_filter"] = locations
    if not slug and any(x in chosen for x in ("linkedin-company", "apify")):
        try:
            co = L.resolve_company(run.fetcher, company, domain, website=website)
        except Blocked as e:
            co, run.stats["resolve_blocked"] = None, str(e)[:160]
        if co:
            slug = co["slug"]
            run.stats["resolved_linkedin"] = {"slug": slug, "matched_by": co["matched_by"]}
    book = Book()
    ctx = dict(run=run, company=company, domain=domain, website=website, slug=slug, city=city,
               github_org=github_org, segments=segments or list(SEGMENTS), known=known or [],
               titles=titles, locations=locations, apify_max=apify_max, apify_mode=apify_mode)
    run.stats["sources"] = {}
    for name in chosen:
        fn = SOURCES.get(name)
        if fn is None:
            run.stats["sources"][name] = {"error": "unknown source"}
            continue
        try:
            got = fn(book, **ctx) if name != "known" else fn(book, known=ctx["known"])
            run.stats["sources"][name] = {"found": got}
        except Blocked as e:
            run.stats["sources"][name] = {"blocked": str(e)[:160]}
        except Exception as e:  # noqa: BLE001 — one dead source never sinks the run
            log.info("company-people: %s failed: %s", name, e)
            run.stats["sources"][name] = {"error": f"{type(e).__name__}: {str(e)[:160]}"}
    if verify:
        _verify(run, book, company=company, slug=slug, domain=domain, max_verify=max_verify)
    people = [_shape(r, company) for r in book.by_key.values() if r.get("name")]
    people = [p for p in people if (include_former or p["status"] != "former")
              and _matches(p, titles, exclude_titles, locations)]
    people.sort(key=lambda p: (STATUS_ORDER[p["status"]], p["referral_rank"] or 99))
    counts: dict[str, int] = {}
    for p in people[:max_items]:
        counts[p["status"]] = counts.get(p["status"], 0) + 1
        run.push(p)
    run.stats["by_status"] = counts
