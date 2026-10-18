"""LinkedIn actors — guest pages only, the shapes Apify's no-cookies actors sell.

Found 2026-10-18 by probing what linkedin.com serves a logged-out client (REA workflow,
system docs/REA-INTEGRATION.md §4):

    jobs search   /jobs-guest/jobs/api/seeMoreJobPostings/search   HTML cards, 10 a page,
                  no login, filters: keywords, location, f_WT=2 remote, f_TPR=r<secs>,
                  f_E experience (1 internship, 2 entry, 3 associate, 4 mid-senior)
    job detail    /jobs-guest/jobs/api/jobPosting/<id>   description, seniority,
                  employment type, industries, applicant count
    company       /company/<slug>   JSON-LD Organization (name, description, employee
                  count, address) + the About <dl> (website, industry, size, HQ, type,
                  founded, specialties) + a handful of employee profile links
    profile       /in/<slug>   JSON-LD Person: name, location, about, worksFor with start
                  (and end) years, alumniOf with years — current vs former is read from
                  the missing endDate, never guessed
    people search /search/results/people   LOGIN ONLY — not used. Employee discovery is
                  search-engine profile URLs + the company page's links, each verified
                  by opening the profile.

Every record carries `source_url` and the fields it was read from; a field the page did
not carry stays None (guarantee:no-fabrication).
"""

from __future__ import annotations

import html as htmllib
import json
import logging
import re
from typing import Iterable

from jobhunter.actors.core import Blocked, Fetcher, Run

log = logging.getLogger(__name__)

BASE = "https://www.linkedin.com"
EXPERIENCE = {"internship": 1, "entry": 2, "associate": 3, "mid-senior": 4, "director": 5, "executive": 6}

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
_LDJSON = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)
_PROFILE_URL = re.compile(r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/in/([A-Za-z0-9\-_%]+)", re.I)
_COMPANY_URL = re.compile(r"linkedin\.com/company/([A-Za-z0-9\-_%]+)", re.I)


def _text(s: str | None) -> str | None:
    if s is None:
        return None
    t = _WS.sub(" ", htmllib.unescape(_TAG.sub(" ", s))).strip()
    return t or None


def _find(pattern: str, h: str) -> str | None:
    m = re.search(pattern, h, re.S)
    return _text(m.group(1)) if m else None


def _ld(h: str, kind: str) -> dict | None:
    for raw in _LDJSON.findall(h):
        try:
            d = json.loads(raw)
        except ValueError:
            continue
        for x in d.get("@graph", [d]) if isinstance(d, dict) else d:
            if isinstance(x, dict) and x.get("@type") == kind:
                return x
    return None


def profile_slug(url: str) -> str | None:
    m = _PROFILE_URL.search(url or "")
    return m.group(1).lower() if m else None


def profile_url(slug_or_url: str) -> str:
    slug = profile_slug(slug_or_url) or slug_or_url.strip("/").split("/")[-1]
    return f"{BASE}/in/{slug}"


def company_slug(s: str) -> str:
    m = _COMPANY_URL.search(s or "")
    return (m.group(1) if m else s.strip("/")).lower()


# ------------------------------------------------------------------ jobs

def _parse_cards(h: str) -> list[dict]:
    out = []
    for card in h.split("<li")[1:]:
        jid = re.search(r"urn:li:jobPosting:(\d+)", card)
        if not jid:
            continue
        link = re.search(r'class="base-card__full-link[^"]*"\s+href="([^"?]+)', card)
        co = re.search(r'base-search-card__subtitle">\s*<a[^>]*href="([^"?]+)[^"]*"[^>]*>(.*?)</a>', card, re.S)
        when = re.search(r'<time[^>]*datetime="([^"]+)"', card)
        out.append({
            "job_id": jid.group(1),
            "title": _find(r'base-search-card__title">(.*?)</h3>', card),
            "company": _text(co.group(2)) if co else _find(r'base-search-card__subtitle">(.*?)</h4>', card),
            "company_url": co.group(1) if co else None,
            "location": _find(r'job-search-card__location">(.*?)</span>', card),
            "posted_on": when.group(1) if when else None,
            "badge": _find(r'job-posting-benefits__text">(.*?)</span>', card),
            "url": link.group(1) if link else f"{BASE}/jobs/view/{jid.group(1)}",
        })
    return out


def job_detail(f: Fetcher, job_id: str) -> dict | None:
    page = f.get(f"{BASE}/jobs-guest/jobs/api/jobPosting/{job_id}",
                 expect=lambda t: "top-card-layout__title" in t)
    if page is None or "top-card-layout__title" not in page.text:
        return None
    h = page.text
    criteria = {
        _text(k): _text(v) for k, v in re.findall(
            r'description__job-criteria-subheader">(.*?)</h3>\s*<span[^>]*>(.*?)</span>', h, re.S)
    }
    applicants = _find(r'num-applicants__caption[^>]*>(.*?)</(?:span|figcaption)>', h)
    # "Meet the hiring team" — shown on some postings: the poster's name, title and profile
    poster = None
    i = h.find("message-the-recruiter")
    if i >= 0:
        block = h[i:i + 4000]
        link = re.search(r'href="(https?://[^"]*linkedin\.com/in/[^"?]+)', block)
        poster = {
            "name": _find(r'base-main-card__title[^>]*>(.*?)</h3>', block),
            "title": _find(r'base-main-card__subtitle[^>]*>(.*?)</h4>', block),
            "linkedin_url": profile_url(link.group(1)) if link else None,
        }
        if not poster["name"]:
            poster = None
    return {
        "job_poster": poster,
        "description": _find(r'show-more-less-html__markup[^>]*>(.*?)</div>', h),
        "seniority": criteria.get("Seniority level"),
        "employment_type": criteria.get("Employment type"),
        "job_function": criteria.get("Job function"),
        "industries": criteria.get("Industries"),
        "applicants": applicants,
        "posted_ago": _find(r'posted-time-ago__text[^>]*>(.*?)</span>', h),
    }


def jobs(run: Run, *, keywords: str, location: str | None = None, remote: bool = False,
         posted_days: int | None = None, experience: str | None = None, max_items: int = 50,
         details: bool = False) -> None:
    """Job search, the guest endpoint, paged until `max_items` or the results end."""
    f = run.fetcher
    params: dict = {"keywords": keywords}
    if location:
        params["location"] = location
    if remote:
        params["f_WT"] = 2
    if posted_days:
        params["f_TPR"] = f"r{int(posted_days) * 86400}"
    if experience:
        levels = [str(EXPERIENCE[e.strip()]) for e in experience.split(",") if e.strip() in EXPERIENCE]
        if levels:
            params["f_E"] = ",".join(levels)
    seen: set[str] = set()
    start = 0
    while run.items < max_items:
        page = f.get(f"{BASE}/jobs-guest/jobs/api/seeMoreJobPostings/search", params={**params, "start": start})
        cards = _parse_cards(page.text) if page else []
        if not cards:
            break
        for c in cards:
            if c["job_id"] in seen or run.items >= max_items:
                continue
            seen.add(c["job_id"])
            if details:
                c.update(job_detail(f, c["job_id"]) or {})
            c["source_url"] = c["url"]
            run.push(c)
        start += len(cards)
    run.stats["pages"] = start


# ------------------------------------------------------------------ company

def parse_company(h: str, url: str) -> dict | None:
    org = _ld(h, "Organization")
    if not org:
        return None
    about = {
        _text(k): v for k, v in re.findall(r"<dt[^>]*>(.*?)</dt>\s*<dd[^>]*>(.*?)</dd>", h, re.S)
    }
    site = about.get("Website")
    site_href = re.search(r'href="([^"]+)"', site or "")
    if site_href and "/redir/redirect" in site_href.group(1):   # LinkedIn's wrapper → the real URL
        from urllib.parse import parse_qs, urlparse

        real = parse_qs(urlparse(htmllib.unescape(site_href.group(1))).query).get("url")
        site_href = re.match(r"(.+)", real[0]) if real else site_href
    size = org.get("numberOfEmployees") or {}
    addr = org.get("address") or {}
    employees = []
    for m in re.finditer(r'href="(https?://[^"]*linkedin\.com/in/[^"?]+)\?[^"]*trk=org-employees[^"]*"[^>]*>(.*?)</a>', h, re.S):
        employees.append({"url": profile_url(m.group(1)), "card": _text(m.group(2))})
    if not employees:   # the section is not always tagged; any profile link is a candidate
        employees = [{"url": profile_url(u), "card": None} for u in dict.fromkeys(_PROFILE_URL.findall(h))]
    return {
        "name": org.get("name"),
        "linkedin_url": url,
        "slug": company_slug(url),
        "description": org.get("description"),
        "employees_on_linkedin": size.get("value") if isinstance(size, dict) else None,
        "website": (site_href.group(1) if site_href else _text(site or "")) or None,
        "industry": _text(about.get("Industry")),
        "company_size": _text(about.get("Company size")),
        "headquarters": _text(about.get("Headquarters")),
        "type": _text(about.get("Type")),
        "founded": _text(about.get("Founded")),
        "specialties": _text(about.get("Specialties")),
        "city": addr.get("addressLocality") if isinstance(addr, dict) else None,
        "country": addr.get("addressCountry") if isinstance(addr, dict) else None,
        "employee_profiles": [dict(e) for e in {e["url"]: e for e in employees}.values()],
        "source_url": url,
    }


def fetch_company(f: Fetcher, slug_or_url: str) -> dict | None:
    url = f"{BASE}/company/{company_slug(slug_or_url)}/"
    page = f.get(url, expect=lambda t: '"Organization"' in t)
    return parse_company(page.text, url) if page else None


def company(run: Run, *, companies: Iterable[str]) -> None:
    """Public company pages: about, size, HQ, specialties, a few employee links."""
    for s in companies:
        rec = fetch_company(run.fetcher, s)
        run.push(rec or {"slug": company_slug(s), "error": "no public page / no data", "source_url": None})


# ------------------------------------------------------------------ profile

def _year(member: dict | None, key: str):
    return (member or {}).get(key) if isinstance(member, dict) else None


def parse_profile(h: str, url: str) -> dict | None:
    p = _ld(h, "Person")
    if not p:
        return None
    title = _find(r"<title>(.*?)</title>", h) or ""
    name = p.get("name")
    headline = None
    if " | LinkedIn" in title:
        head = title.split(" | LinkedIn")[0]
        if name and head.startswith(name + " - "):
            headline = head[len(name) + 3:]
    current, past = [], []
    for org in p.get("worksFor") or []:
        m = org.get("member") or {}
        row = {"company": (org.get("name") or "").strip() or None, "company_url": org.get("url"),
               "start": _year(m, "startDate"), "end": _year(m, "endDate")}
        (past if row["end"] else current).append(row)
    education = [{"school": e.get("name"), "school_url": e.get("url"),
                  "start": _year(e.get("member"), "startDate"), "end": _year(e.get("member"), "endDate")}
                 for e in p.get("alumniOf") or [] if e.get("@type") == "EducationalOrganization"]
    addr = p.get("address") or {}
    stats = p.get("interactionStatistic") or {}
    return {
        "name": name,
        "headline": headline,
        "job_titles": p.get("jobTitle") or [],
        "location": addr.get("addressLocality") if isinstance(addr, dict) else None,
        "country": addr.get("addressCountry") if isinstance(addr, dict) else None,
        "about": p.get("description"),
        "current": current,
        "past": past,
        "education": education,
        "followers": stats.get("userInteractionCount") if isinstance(stats, dict) else None,
        "linkedin_url": url,
        "source_url": url,
    }


def fetch_profile(f: Fetcher, slug_or_url: str) -> dict | None:
    url = profile_url(slug_or_url)
    page = f.get(url, expect=lambda t: '"Person"' in t)
    return parse_profile(page.text, url) if page else None


def profiles(run: Run, *, urls: Iterable[str]) -> None:
    """Public profiles: current and former employers with years, schools, location."""
    for u in urls:
        rec = fetch_profile(run.fetcher, u)
        run.push(rec or {"linkedin_url": profile_url(u), "error": "no public profile data", "source_url": None})


# ------------------------------------------------------------------ employees

def _works_at(rec: dict, name: str, slug: str | None, domain: str | None) -> dict | None:
    """The current position at this company, matched by its LinkedIn company URL first
    (exact), then by name — None when the profile does not say they are there now."""
    from jobhunter import guardrails

    for job in rec.get("current") or []:
        if slug and job.get("company_url") and company_slug(job["company_url"]) == slug:
            return {**job, "matched_by": "company_url"}
    for job in rec.get("current") or []:
        # 2026-10-18: "Braintrust" (braintrust.dev) vs Braintrust the talent network — when
        # the company's own page is known, a position linked to ANOTHER page is a namesake;
        # the name alone decides only for a position with no company link at all
        if slug and job.get("company_url"):
            continue
        if job.get("company") and guardrails.names_company(job["company"], name, domain):
            return {**job, "matched_by": "name"}
    return None


def resolve_company(f: Fetcher, name: str, domain: str | None, *, website: str | None = None,
                    max_tries: int = 4) -> dict | None:
    """The company's LinkedIn page from its name + domain: guessed slugs and search hits,
    each opened, kept only when the page's website is on the company's domain (or, with no
    domain, its name matches exactly). None rather than a namesake."""
    from jobhunter.research import web

    def norm_host(u: str | None) -> str:
        h = re.sub(r"^https?://", "", (u or "").lower()).split("/")[0]
        return h[4:] if h.startswith("www.") else h

    label = (domain or "").split(".")[0]
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    guesses = list(dict.fromkeys(x for x in (label, base, base.replace("-", ""), f"{base}-ai", f"{label}-hq",
                                             f"{label}{domain.split('.')[-1]}" if domain else None) if x))
    # the company's own homepage links its LinkedIn page (footer / social icons) — the
    # company naming itself, so that slug is tried first and needs no website match
    own: list[str] = []
    if website:
        from jobhunter.contacts import team_people

        try:
            status, home = team_people._get(website)
        except Exception:  # noqa: BLE001
            status, home = 0, ""
        if status and status < 400 and home:
            own = list(dict.fromkeys(m.lower() for m in _COMPANY_URL.findall(home)
                                     if m.lower() not in ("linkedin", "company")))
    for slug in own[:2]:
        co = fetch_company(f, slug)
        if co:
            return {**co, "matched_by": "own-website"}
    if not web.search_down():
        res = web.search(f'"{name}" site:linkedin.com/company', limit=8, only=web.KEYLESS)
        for r in res.get("results") or []:
            m = _COMPANY_URL.search(r.get("url") or "")
            if m:
                guesses.insert(0, m.group(1).lower())
    want = norm_host(domain)
    for slug in list(dict.fromkeys(guesses))[:max_tries]:
        co = fetch_company(f, slug)
        if not co:
            continue
        site = norm_host(co.get("website"))
        if want and site and (site == want or site.endswith("." + want) or want.endswith("." + site)):
            return {**co, "matched_by": "website"}
        if not want and (co.get("name") or "").strip().lower() == name.strip().lower():
            return {**co, "matched_by": "name"}
    return None


def _search_candidates(name: str, domain: str | None, city: str | None, exa: bool) -> list[dict]:
    """Profile URLs from search engines, with the result title as a hint. Keyless first;
    Exa only when asked (it is metered and capped)."""
    from jobhunter.contacts import linkedin_public as lp
    from jobhunter.research import web

    out: list[dict] = []
    where = f" {city}" if city else ""
    for words in ("", "engineer", "founder OR CTO", '"engineering manager" OR "tech lead"'):
        if web.search_down():
            break
        res = web.search(lp._query(name, words, where), limit=10, only=web.KEYLESS)
        for r in res.get("results") or []:
            if profile_slug(r.get("url") or ""):
                out.append({"url": profile_url(r["url"]), "hint": r.get("title"), "via": res.get("backend")})
    if exa and lp.exa_people_available():
        who = f"{name} ({domain})" if domain else name
        hits = web._exa_api_search(f"people currently working at {who}{where}", 15, 60,
                                   include_domains=["linkedin.com"]) or []
        out += [{"url": profile_url(h.url), "hint": h.title, "via": "exa"} for h in hits if profile_slug(h.url)]
    return out


def employees(run: Run, *, company: str, linkedin: str | None = None, domain: str | None = None,
              city: str | None = None, max_profiles: int = 15, exa: bool = False,
              extra_urls: Iterable[str] = ()) -> None:
    """Apify's "company employees" without a session: gather candidate profile URLs
    (company page links, search engines, URLs we already hold), open each profile, and
    keep the people whose profile says they work at `company` NOW."""
    f = run.fetcher
    slug = company_slug(linkedin) if linkedin else None
    cands: list[dict] = [{"url": profile_url(u), "hint": None, "via": "known"} for u in extra_urls]
    if slug:
        co = fetch_company(f, slug)
        if co:
            run.stats["company"] = {k: co[k] for k in ("name", "employees_on_linkedin", "headquarters", "website")}
            cands += [{"url": e["url"], "hint": e["card"], "via": "company-page"} for e in co["employee_profiles"]]
    cands += _search_candidates(company, domain, city, exa)
    uniq = list({profile_slug(c["url"]): c for c in cands if profile_slug(c["url"])}.values())
    run.stats.update(candidates=len(uniq), opened=0, current=0, former=0, unreadable=0, unverified=0)
    blocked = None
    for c in uniq:
        if run.stats["current"] + run.stats["unverified"] >= max_profiles:
            break
        if blocked is None:
            try:
                rec = fetch_profile(f, c["url"])
            except Blocked as e:
                blocked = str(e)
                run.stats["profiles_blocked"] = blocked
        if blocked is not None:
            # profiles are walled: the candidate is still a lead, stated no more firmly
            # than the search result or company page that named it
            run.stats["unverified"] += 1
            run.push({"name": c["hint"], "linkedin_url": c["url"], "found_via": c["via"],
                      "search_hint": c["hint"], "works_there_now": None, "position": None,
                      "evidence": f"not verified — profile route blocked ({blocked[:80]})",
                      "source_url": c["url"]})
            continue
        run.stats["opened"] += 1
        if not rec:
            run.stats["unreadable"] += 1
            continue
        job = _works_at(rec, company, slug, domain)
        if job:
            run.stats["current"] += 1
        else:
            run.stats["former"] += 1
        run.push({**rec, "found_via": c["via"], "search_hint": c["hint"],
                  "works_there_now": bool(job), "position": job,
                  "evidence": (f"profile lists {job['company']} with no end date"
                               + (f" (since {job['start']})" if job.get("start") else "")) if job else
                              "profile does not list this company as a current position"})
