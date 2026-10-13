"""India job postings: Bengaluru / Hyderabad companies found by the roles they post.

Tejmul, 2026-10-13: "There are companies opening in India at ~₹37 LPA, like Gitforce —
scrape them too." India discovery (india.py) finds companies by their funding; this
finds them by their postings — LinkedIn Jobs (public guest pages, no login), the
aggregators that re-list them, and offer posts on community forums as leads.

ONE RUN (every india_jobs.every_hours; `harvest india-jobs` runs one now)
    linkedin   a few guest search pages, keyword × city rotated by a cursor, filtered to
               entry level + internship and the past week; then searches by name for the
               seed companies and the community leads. For the companies on those cards:
               their public company page (website, industry, size, HQ) when they are new,
               and their job pages (description, criteria, LinkedIn's "Base pay range" or
               a pay sentence in the description).
    jobfound   jobfound.org/jobs/india — its pay band is jobfound's estimate, so it is
               written to the company's notes as a listed figure, never onto the job row.
    instahyre  one city page per run.
    community  one keyless search of offer posts per run — names become leads for the
               LinkedIn name search; their numbers are never stored.

THE GATE, per company, before anything is written
    a staffing / recruitment agency, a "Confidential" poster or a post "for a client",
    an IT-services body shop (admission.body_shop); famous (admission.famous) or a
    10,001+ LinkedIn size band; no posting in Bengaluru or Hyderabad ("other city").
    What passes goes through harvest._upsert (admission.admit: name cleanup, dedup,
    overrated), its city through guardrails.set_city with the posting's own line as the
    quote, and its roles through pipeline.persist — normalise, is_senior, is_internship,
    salary_min/max_lpa from the posting's own figure — so the ₹20 LPA floor (india.py)
    and the stated-pay priority boost (targeting.priority) read posting evidence.
    New rows get india_found_at: the fill queues take them right after the ★ list.

GENTLE
    LinkedIn is read through Jina Reader (a direct fetch from this machine gets a 429
    JavaScript challenge). ≥ gap_seconds between two requests to one site; a 429, a 999
    or a login wall stops LinkedIn for backoff_hours (setting india_jobs:linkedin_until).

Layering: pipeline layer — scrapers, admission, guardrails, harvest, research below; tick,
api and the CLI above.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import httpx
from sqlmodel import col, select

from jobhunter import CONFIG, admission, channels, guardrails
from jobhunter import normalize as norm
from jobhunter.db import Company, Job, get_session, get_setting, init_db, set_setting, utcnow
from jobhunter.scrapers import india_jobs as ij
from jobhunter.scrapers.base import RawJob

log = logging.getLogger(__name__)

_C = CONFIG.get("india_jobs") or {}
EVERY_HOURS = float(_C.get("every_hours", 6))
SEARCHES = int(_C.get("searches_per_run", 4))
PAGE_READS = int(_C.get("page_reads_per_run", 10))
COMPANY_READS = int(_C.get("company_reads_per_run", 8))
LEAD_SEARCHES = int(_C.get("lead_searches_per_run", 4))
COMMUNITY = int(_C.get("community_queries_per_run", 1))
GAP_S = float(_C.get("gap_seconds", 4))
BACKOFF_H = float(_C.get("backoff_hours", 12))
LARGE = int(_C.get("large_headcount", 5000))
SEEDS: list[str] = [str(s) for s in (_C.get("seed_companies") or [])]
CITIES = ("Bengaluru", "Hyderabad")

K_LAST = "india_jobs:last_run"
K_UNTIL = "india_jobs:linkedin_until"
K_CURSOR = "india_jobs:cursor"
K_LEADS = "india_jobs:leads"
K_IH = "india_jobs:instahyre_cursor"
K_COMMUNITY = "india_jobs:community_cursor"
K_LAST_REPORT = "india_jobs:last_report"
CHANNEL = "linkedin-guest"


# ------------------------------------------------------------------ bookkeeping

@dataclass
class Report:
    sources: dict = field(default_factory=dict)
    rejects: dict = field(default_factory=dict)          # reason -> [company names]
    problems: list = field(default_factory=list)          # channel problems, in order
    new: list = field(default_factory=list)               # (company id, name, source)
    existing: list = field(default_factory=list)
    stated: dict = field(default_factory=dict)            # company name -> (lpa, title, url)
    listed: dict = field(default_factory=dict)            # company name -> (band, title, url) — jobfound's estimate
    leads: dict = field(default_factory=dict)
    pages: dict = field(default_factory=lambda: {"search": 0, "job": 0, "company": 0, "board": 0})

    def src(self, name: str) -> dict:
        return self.sources.setdefault(name, {"cards": 0, "companies": 0, "new": 0, "existing": 0, "jobs": 0})

    def reject(self, reason: str, name: str) -> None:
        names = self.rejects.setdefault(reason, [])
        if name not in names:
            names.append(name)


_last_hit: dict[str, float] = {}


def _pace(host: str) -> None:
    """At least GAP_S between two requests to one site."""
    t = _last_hit.get(host)
    if t is not None:
        wait = GAP_S - (time.time() - t)
        if wait > 0:
            time.sleep(wait)
    _last_hit[host] = time.time()


def _backoff_until(session) -> datetime | None:
    raw = get_setting(session, K_UNTIL, "")
    try:
        until = datetime.fromisoformat(raw) if raw else None
    except ValueError:
        return None
    return until if until and until > utcnow() else None


def _block(session, rep: Report, why: str) -> None:
    until = utcnow() + timedelta(hours=BACKOFF_H)
    set_setting(session, K_UNTIL, until.isoformat(timespec="seconds"))
    channels.record(CHANNEL, False, why, cooldown=True)
    rep.problems.append(f"linkedin: {why} — backing off until {until:%Y-%m-%d %H:%M}")


class Blocked(Exception):
    pass


def _linkedin(session, rep: Report, url: str, kind: str) -> str | None:
    """One LinkedIn page through Jina Reader, as Markdown. Raises Blocked on a 429, a 999
    or a login wall (and records the backoff); None on a one-off failure."""
    from jobhunter.research import web

    _pace("linkedin.com")
    try:
        r = httpx.get(web.JINA + url, headers={"User-Agent": web.JINA_UA, "Accept": "text/plain"},
                      timeout=60, follow_redirects=True)
    except Exception as e:  # noqa: BLE001
        rep.problems.append(f"jina: {type(e).__name__} on a {kind} page")
        channels.record("jina", False, f"{type(e).__name__}")
        return None
    body = r.text or ""
    if r.status_code >= 400:
        if r.status_code == 429 and "linkedin" not in body.lower():
            channels.record("jina", False, "HTTP 429")
            rep.problems.append("jina: HTTP 429 (its own rate limit) — stopped this run")
            raise Blocked("jina 429")
        if r.status_code in (429, 999, 451) or "429" in body[:600] or "999" in body[:600]:
            _block(session, rep, f"HTTP {r.status_code} on a {kind} page")
            raise Blocked(str(r.status_code))
        rep.problems.append(f"linkedin {kind} page: HTTP {r.status_code}")
        channels.record(CHANNEL, r.status_code == 404, f"HTTP {r.status_code}")
        return None
    channels.record("jina", True)
    marker = body.find("Markdown Content:")
    text = body[marker + len("Markdown Content:"):] if marker >= 0 else body
    rep.pages[kind] = rep.pages.get(kind, 0) + 1
    if kind == "search" and not text.strip():
        return ""                     # an empty result page (no more cards) is an answer
    if ij.walled(text):
        _block(session, rep, f"login wall / challenge on a {kind} page")
        raise Blocked("wall")
    channels.record(CHANNEL, True)
    return text


def _board(rep: Report, url: str) -> str | None:
    from jobhunter.research import web

    from urllib.parse import urlparse

    _pace(urlparse(url).netloc)
    page = web.read(url, max_chars=80000)
    if page.get("error") or not page.get("text"):
        rep.problems.append(f"{url}: {page.get('error') or 'empty'}")
        return None
    rep.pages["board"] += 1
    return page["text"]


def _leads(session) -> list[dict]:
    try:
        rows = json.loads(get_setting(session, K_LEADS, "[]") or "[]")
        return rows if isinstance(rows, list) else []
    except ValueError:
        return []


def _save_leads(session, rows: list[dict]) -> None:
    set_setting(session, K_LEADS, json.dumps(rows[-300:]))


def add_lead(session, name: str, source: str, evidence: str | None = None, *, city: str | None = None,
             band: str | None = None, slug: str | None = None, requeue: bool = False) -> bool:
    """Queue a company to look up on LinkedIn by name. The lookup is what admits it: a
    posting in Bengaluru / Hyderabad and a company page that passes the gate."""
    rows = _leads(session)
    key = admission.name_key(name)
    week_ago = (utcnow() - timedelta(days=7)).isoformat(timespec="seconds")
    # queued, or looked up in the last week: nothing to do
    # queued, or looked up in the last week: nothing to do — unless the lookup found it and
    # it is waiting for a company-page read (requeue)
    if not key or any(r.get("key") == key and (not r.get("done") or (r["done"] > week_ago and not requeue))
                      for r in rows):
        return False
    rows = [r for r in rows if r.get("key") != key]
    rows.append({"name": name, "key": key, "source": source, "evidence": (evidence or "")[:200],
                 "city": city, "band": band, "slug": slug,
                 "at": utcnow().isoformat(timespec="seconds"), "done": None})
    _save_leads(session, rows)
    return True


# ------------------------------------------------------------------ one company through the gate

@dataclass
class Posting:
    """One role as a source showed it, before the gate."""

    company: str
    title: str
    url: str
    source: str
    location: str | None = None
    posted_at: datetime | None = None
    salary_raw: str | None = None
    description: str | None = None
    company_url: str | None = None      # its LinkedIn company page
    listed_band: str | None = None      # an aggregator's estimate (jobfound) — notes only
    blurb: str | None = None            # a board's one-liner about the company
    headcount: int | None = None        # lower bound of the size band
    industry: str | None = None
    seniority: str | None = None


def _gate(name: str, posts: list[Posting], *, about: str | None = None, industry: str | None = None,
          headcount: int | None = None) -> str | None:
    """The reason this company is turned away, or None."""
    text = " ".join(filter(None, [about] + [p.description for p in posts] + [p.blurb for p in posts]))
    why = admission.body_shop(name, text, industry=industry or next((p.industry for p in posts if p.industry), None),
                              headcount=headcount or next((p.headcount for p in posts if p.headcount), None))
    if why:
        return why.split(" (")[0]
    hit = admission.famous(name)
    if hit:
        return f"famous ({hit[0]})"
    hc = headcount or next((p.headcount for p in posts if p.headcount), None)
    if hc and hc >= LARGE:
        return f"large enterprise ({LARGE:,}+ employees)"
    return None


def _city_line(name: str, p: Posting) -> str:
    where = {"linkedin-jobs": "LinkedIn job posting", "jobfound": "jobfound listing",
             "instahyre": "Instahyre listing"}.get(p.source, p.source)
    return f"{name} is hiring {p.title} in {p.location} ({where})"


def _admit(rep: Report, name: str, posts: list[Posting], *, info: dict | None = None) -> Company | None:
    """Write one company and its roles. Everything is checked by now except admission."""
    from jobhunter import harvest, pipeline

    info = info or {}
    src = posts[0].source
    st = rep.src(src)
    in_city = [p for p in posts if admission.india_city(p.location)]
    first = in_city[0]
    with get_session() as session:
        existing = admission.find_existing(session, name, admission.host_of(info.get("website")))
        hs = harvest.HarvestStats(src)
        slug = ij.company_slug(first.company_url)
        bits = [f"{len(in_city)} role(s), e.g. {first.title} — {first.location}"]
        if slug:
            bits.append(f"linkedin.com/company/{slug}")
        if info.get("industry"):
            bits.append(f"LinkedIn industry: {info['industry']}, {info.get('size') or 'size ?'}, HQ {info.get('hq') or '?'}")
        listed = [p for p in posts if p.listed_band]
        if listed:
            bits.append("jobfound lists " + "; ".join(f"{p.listed_band} for {p.title}" for p in listed[:3])
                        + " — jobfound's estimate, not stated by the employer")
        company = harvest._upsert(
            session, name=name, website=info.get("website"), source=src, description=None, region="india",
            note="; ".join(bits), claim={"text": _city_line(name, first)[:200], "url": first.url, "by": None},
            stats=hs)
        if company is None:
            reason = next(iter(hs.skipped), "refused by the admission gate")
            rep.reject(reason if not reason.startswith("overrated") else "famous / overrated", name)
            return None
        about = info.get("about") or next((p.blurb for p in posts if p.blurb), None)
        if about:
            guardrails.set_description(session, company, about, source=ij.company_url(slug) if slug else first.url,
                                       channel=CHANNEL if slug else src, own_site=bool(slug))
        # the posting's own line places them; the company page's HQ line if that is refused
        if not guardrails.set_city(session, company, _city_line(company.name, first), source=first.url,
                                   channel=CHANNEL if src == ij.LINKEDIN_SOURCE else src) and info.get("hq"):
            guardrails.set_city(session, company, f"{company.name} — Headquarters: {info['hq']} (LinkedIn company page)",
                                source=ij.company_url(slug) if slug else first.url, channel=CHANNEL)
        new = existing is None and hs.admitted > 0
        if new:
            company.india_found_at = utcnow()
            company.india_source = src
        session.add(company)
        session.commit()
        cid, cname, site = company.id, company.name, company.website
    (rep.new if new else rep.existing).append((cid, cname, src))
    st["companies"] += 1
    st["new" if new else "existing"] += 1
    if listed:
        p = max(listed, key=lambda x: _band_top(x.listed_band) or 0)
        rep.listed[cname] = (p.listed_band, p.title, p.url)

    raws = [RawJob(company_name=cname, title=p.title, url=p.url, source=p.source, location=p.location,
                   description=p.description, posted_at=p.posted_at, salary_raw=p.salary_raw,
                   company_website=site) for p in in_city]
    stats = pipeline.persist(raws, pipeline.ScrapeStats())
    st["jobs"] += stats.inserted
    for p in in_city:
        sal = norm.parse_salary(p.salary_raw, p.description)
        v = sal.max_lpa or sal.min_lpa
        if v and not norm.is_senior(p.title) and not norm.is_internship(p.title, p.description):
            if cname not in rep.stated or v > rep.stated[cname][0]:
                rep.stated[cname] = (v, p.title, p.url, sal.raw)
    return company


def _band_top(band: str | None) -> float | None:
    import re

    nums = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", band or "")]
    return max(nums) if nums else None


# ------------------------------------------------------------------ sources

def _process(rep: Report, groups: dict[str, list[Posting]], *, reads: dict, session_holder) -> None:
    """The gate and the writes for one source's postings, grouped by company."""
    for name, posts in groups.items():
        rep.src(posts[0].source)
        if not any(admission.india_city(p.location) for p in posts):
            other = next((admission.india_other_city(p.location) for p in posts if admission.india_other_city(p.location)), None)
            rep.reject(f"other city ({other})" if other else "no Bengaluru/Hyderabad posting", name)
            continue
        why = _gate(name, posts)
        if why:
            rep.reject(why, name)
            continue
        info: dict = {}
        if posts[0].source == ij.LINKEDIN_SOURCE:
            with get_session() as s:
                existing = admission.find_existing(s, name)
                known = set(s.exec(select(Job.url).where(col(Job.url).in_([p.url for p in posts]))).all())
            slug = next((ij.company_slug(p.company_url) for p in posts if ij.company_slug(p.company_url)), None)
            if existing is None and (not slug or reads["company"] <= 0):
                # a new company is admitted only after its company page passed the gate
                # (size, industry, website): no page this run -> a lead for the next one
                city = next((admission.india_city(p.location) for p in posts if admission.india_city(p.location)), None)
                if add_lead(session_holder, name, "linkedin-card", f"{posts[0].title} — {posts[0].location}",
                            city=city, band=next((p.listed_band for p in posts if p.listed_band), None), slug=slug,
                            requeue=True):
                    rep.leads.setdefault("deferred", []).append(name)
                continue
            try:
                if existing is None:
                    reads["company"] -= 1
                    text = _linkedin(session_holder, rep, ij.company_url(slug), "company")
                    info = ij.parse_company_page(text) if text else {}
                    if not info.get("industry") and not info.get("size"):
                        rep.problems.append(f"linkedin company page for {name}: no About section")
                        add_lead(session_holder, name, "linkedin-card", posts[0].title, slug=slug, requeue=True)
                        continue
                    why = _gate(name, posts, about=info.get("about"), industry=info.get("industry"),
                                headcount=info.get("headcount_min"))
                    if why:
                        rep.reject(why, name)
                        continue
                # job pages: fresher roles first, ones we do not hold yet
                for p in sorted(posts, key=lambda x: (norm.is_senior(x.title), x.url in known)):
                    if reads["job"] <= 0 or p.url in known:
                        break
                    reads["job"] -= 1
                    text = _linkedin(session_holder, rep, p.url, "job")
                    if not text:
                        continue
                    page = ij.parse_job_page(text)
                    p.description = page["description"]
                    p.salary_raw = p.salary_raw or page["salary_raw"]
                    p.industry = page["industry"]
                    p.seniority = page["seniority"]
                why = _gate(name, posts, about=info.get("about"), industry=info.get("industry"),
                            headcount=info.get("headcount_min"))
                if why:
                    rep.reject(why, name)
                    continue
            except Blocked:
                # LinkedIn stopped answering: an existing company's cards are still
                # postings and are stored; a new one waits as a lead
                reads["job"] = reads["company"] = 0
                if existing is None:
                    add_lead(session_holder, name, "linkedin-card", posts[0].title, slug=slug, requeue=True)
                    continue
        elif posts[0].source == ij.JOBFOUND_SOURCE:
            # jobfound re-lists LinkedIn roles: a lead, confirmed by the LinkedIn lookup
            # (its posting, its company page). Its band travels with the lead to the notes.
            with get_session() as s:
                existing = admission.find_existing(s, name)
            if existing is None:
                city = next((admission.india_city(p.location) for p in posts if admission.india_city(p.location)), None)
                band = next((p.listed_band for p in posts if p.listed_band), None)
                if add_lead(session_holder, name, "jobfound", f"{posts[0].title} — {posts[0].location}",
                            city=city, band=band):
                    rep.leads.setdefault("jobfound", []).append(name)
                if band:
                    rep.listed[name] = (band, posts[0].title, posts[0].url)
                continue
        _admit(rep, name, posts, info=info)


def _group(posts: list[Posting]) -> dict[str, list[Posting]]:
    out: dict[str, list[Posting]] = {}
    keys: dict[str, str] = {}
    for p in posts:
        if not p.company or not norm.title_relevant(p.title):
            continue
        k = admission.name_key(p.company) or p.company.lower()
        name = keys.setdefault(k, p.company.strip())
        if all(q.url != p.url for q in out.get(name, [])):
            out.setdefault(name, []).append(p)
    return out


def from_linkedin(session, rep: Report, *, searches: int, lead_searches: int, cities=CITIES) -> list[Posting]:
    """Guest search pages: keyword × city by cursor, then the seed / lead name searches."""
    posts: list[Posting] = []
    pool = [(kw, c) for kw in ij.KEYWORDS for c in cities]
    try:
        cursor = int(get_setting(session, K_CURSOR, "0") or 0)
    except ValueError:
        cursor = 0
    st = rep.src(ij.LINKEDIN_SOURCE)
    asked = 0
    done_keys: set[str] = set()
    rows: list[dict] = []
    looked: list[Posting] = []          # from the name lookups — first in line for the page reads
    try:
        while asked < searches:
            kw, city = pool[(cursor + asked) % len(pool)]
            asked += 1
            text = _linkedin(session, rep, ij.search_url(kw, ij.GEO[city]), "search")
            cards = ij.parse_cards(text) if text else []
            st["cards"] += len(cards)
            posts += [Posting(company=c.company, title=c.title, url=c.url, source=ij.LINKEDIN_SOURCE,
                              location=c.location, posted_at=c.posted_at, salary_raw=c.salary_raw,
                              company_url=c.company_url) for c in cards]
        # seeds (once ever) and leads, by name
        rows = _leads(session)
        for seed in SEEDS:
            if not any(r.get("key") == admission.name_key(seed) for r in rows) and add_lead(session, seed, "seed"):
                rows = _leads(session)
        # seeds first, then the oldest; a name search is scoped to a city — LinkedIn's
        # India-wide ranking buries a small company under bigger names
        todo = sorted((r for r in rows if not r.get("done")), key=lambda r: (r.get("source") != "seed", r.get("at") or ""))
        for r in todo[:lead_searches]:
            cities_for = [r["city"]] if r.get("city") in ij.GEO else list(cities)
            cards = []
            for city in cities_for:
                text = _linkedin(session, rep, ij.search_url(r["name"], ij.GEO[city], experience=None, posted=None),
                                 "search")
                cards = [c for c in (ij.parse_cards(text) if text else []) if admission.name_key(c.company) == r["key"]]
                if cards:
                    break
            r["done"] = utcnow().isoformat(timespec="seconds")
            r["found"] = len(cards)
            rep.leads.setdefault("looked_up", []).append(f"{r['name']} ({r['source']}): {len(cards)} posting(s)")
            st["cards"] += len(cards)
            looked += [Posting(company=c.company, title=c.title, url=c.url, source=ij.LINKEDIN_SOURCE,
                               location=c.location, posted_at=c.posted_at, salary_raw=c.salary_raw,
                               company_url=c.company_url, listed_band=r.get("band")) for c in cards]
            done_keys.add(r["key"])
    except Blocked:
        pass
    if done_keys:
        done = {r["key"]: r for r in rows if r.get("key") in done_keys}
        _save_leads(session, [done.get(r.get("key"), r) for r in _leads(session)])
    set_setting(session, K_CURSOR, str((cursor + asked) % len(pool)))
    # the looked-up companies first, then the search cards with fresher titles first
    return looked + sorted(posts, key=lambda p: norm.is_senior(p.title))


def from_jobfound(rep: Report) -> list[Posting]:
    text = _board(rep, ij.JOBFOUND_INDIA)
    cards = ij.parse_jobfound(text)
    rep.src(ij.JOBFOUND_SOURCE)["cards"] += len(cards)
    return [Posting(company=c["company"], title=c["title"], url=c["url"], source=ij.JOBFOUND_SOURCE,
                    location=c["location"], posted_at=c["posted_at"], listed_band=c["listed_band"]) for c in cards]


def from_instahyre(session, rep: Report, cities=CITIES) -> list[Posting]:
    try:
        cur = int(get_setting(session, K_IH, "0") or 0)
    except ValueError:
        cur = 0
    city = cities[cur % len(cities)]
    set_setting(session, K_IH, str(cur + 1))
    cards = ij.parse_instahyre(_board(rep, ij.INSTAHYRE_CITY[city]))
    rep.src(ij.INSTAHYRE_SOURCE)["cards"] += len(cards)
    return [Posting(company=c["company"], title=c["title"], url=c["url"], source=ij.INSTAHYRE_SOURCE,
                    location=c["location"], blurb=c["blurb"], headcount=c["headcount_min"]) for c in cards]


def from_community(session, rep: Report, *, queries: int) -> None:
    """Offer posts -> company names -> the lead queue. Nothing about them is stored."""
    from jobhunter.research import web

    try:
        cur = int(get_setting(session, K_COMMUNITY, "0") or 0)
    except ValueError:
        cur = 0
    added = []
    for i in range(queries):
        q = ij.COMMUNITY_QUERIES[(cur + i) % len(ij.COMMUNITY_QUERIES)]
        hit = web.search(q, limit=10, only=web.KEYLESS)
        if hit.get("error") and not hit.get("results"):
            rep.problems.append(f"community search: {hit['error'][:120]}")
        for r in hit.get("results") or []:
            text = f"{r.get('title') or ''}. {r.get('snippet') or ''}"
            for name in ij.offer_leads(text):
                if admission.famous(name) or admission.body_shop(name) or admission.find_existing(session, name):
                    continue
                if add_lead(session, name, "community", f"{r.get('url')}: {text[:160]}"):
                    added.append(name)
    set_setting(session, K_COMMUNITY, str(cur + queries))
    rep.leads["community_new"] = added


# ------------------------------------------------------------------ the step

def due(session) -> bool:
    raw = get_setting(session, K_LAST, "")
    try:
        return not raw or utcnow() - datetime.fromisoformat(raw) >= timedelta(hours=EVERY_HOURS)
    except ValueError:
        return True


def run(*, force: bool = False, searches: int | None = None, page_reads: int | None = None,
        company_reads: int | None = None, lead_searches: int | None = None, community: int | None = None,
        sources: tuple[str, ...] = ("linkedin", "jobfound", "instahyre", "community"),
        cities: tuple[str, ...] = CITIES, budget_s: int = 900) -> dict:
    """One pass over every source. Returns the report (also kept in india_jobs:last_report)."""
    init_db()
    t0 = time.time()
    rep = Report()
    with get_session() as session:
        if not force and not due(session):
            return {"skipped": f"ran less than {EVERY_HOURS:g}h ago (india_jobs.every_hours)"}
        set_setting(session, K_LAST, utcnow().isoformat(timespec="seconds"))
        until = _backoff_until(session)
        reads = {"job": PAGE_READS if page_reads is None else page_reads,
                 "company": COMPANY_READS if company_reads is None else company_reads}
        errors = {}
        if "community" in sources:
            try:
                from_community(session, rep, queries=COMMUNITY if community is None else community)
            except Exception as e:  # noqa: BLE001
                log.exception("india jobs: community failed")
                errors["community"] = f"{type(e).__name__}: {str(e)[:160]}"
        for name in sources:
            if name == "community" or time.time() - t0 > budget_s:
                continue
            try:
                if name == "linkedin":
                    if until and not force:
                        rep.problems.append(f"linkedin: in backoff until {until:%Y-%m-%d %H:%M}")
                        continue
                    posts = from_linkedin(session, rep, searches=SEARCHES if searches is None else searches,
                                          lead_searches=LEAD_SEARCHES if lead_searches is None else lead_searches,
                                          cities=cities)
                elif name == "jobfound":
                    posts = from_jobfound(rep)
                elif name == "instahyre":
                    posts = from_instahyre(session, rep, cities)
                else:
                    continue
                _process(rep, _group(posts), reads=reads, session_holder=session)
            except Exception as e:  # noqa: BLE001 — one source failing must not end the pass
                log.exception("india jobs %s failed", name)
                errors[name] = f"{type(e).__name__}: {str(e)[:160]}"
        out = summary(rep)
        out["seconds"] = round(time.time() - t0)
        if errors:
            out["errors"] = errors
        set_setting(session, K_LAST_REPORT, json.dumps(out, default=str)[:60000])
    log.info("india jobs: %s", {k: out[k] for k in ("new", "existing", "stated_pay") if k in out})
    return out


def _bucket(v: float) -> str:
    return "≥30" if v >= 30 else "20–30" if v >= 20 else "<20"


def summary(rep: Report) -> dict:
    stated = {"≥30": 0, "20–30": 0, "<20": 0}
    for v, *_ in rep.stated.values():
        stated[_bucket(v)] += 1
    listed = {"≥30": 0, "20–30": 0, "<20": 0}
    for band, *_ in rep.listed.values():
        top = _band_top(band)
        if top:
            listed[_bucket(top)] += 1
    new_names = {n for _, n, _ in rep.new}
    top = sorted(((v, n, t, u, raw) for n, (v, t, u, raw) in rep.stated.items() if n in new_names), reverse=True)[:15]
    return {
        "new": len(rep.new), "existing": len(rep.existing),
        "new_companies": [f"{n} ({s}, #{i})" for i, n, s in rep.new],
        "by_source": rep.sources,
        "stated_pay": {"companies": len(rep.stated), **stated},
        "top_new_by_stated_pay": [{"company": n, "lpa": v, "title": t, "said": raw, "url": u} for v, n, t, u, raw in top],
        "listed_by_jobfound_estimate": {"companies": len(rep.listed), **listed},
        "rejects": {k: {"count": len(v), "sample": v[:8]} for k, v in sorted(rep.rejects.items(), key=lambda kv: -len(kv[1]))},
        "leads": rep.leads, "pages": rep.pages, "channel_problems": rep.problems,
    }


def last_report() -> dict:
    init_db()
    with get_session() as session:
        try:
            return json.loads(get_setting(session, K_LAST_REPORT, "{}") or "{}")
        except ValueError:
            return {}


__all__ = ["run", "summary", "last_report", "add_lead", "due"]
