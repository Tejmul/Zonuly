"""India job postings — parsers for LinkedIn's public (guest) job search and the job
boards that list Bengaluru / Hyderabad roles.

Tejmul, 2026-10-13: "There are companies opening in India at ~₹37 LPA, like Gitforce —
scrape them too." Gitforce (Hyderabad) posts on LinkedIn Jobs and is re-listed by the
aggregators; none of our sources read either.

What is read, all without a login (constraint:no-linkedin-automation is about a
logged-in account — nothing here holds one):

  linkedin   the guest job-search fragment
             (linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search) — job cards —
             and the public job page (linkedin.com/jobs/view/<id>) for the description,
             the job criteria and LinkedIn's "Base pay range" when the employer set one;
             the public company page for the website, industry, size and HQ.
             Read through Jina Reader (LinkedIn answers a direct fetch from this machine
             with a 429 JavaScript challenge), so the parsers take Jina's Markdown and,
             for a direct fetch, LinkedIn's HTML.
  jobfound   jobfound.org/jobs/india — a re-list of LinkedIn roles with a pay band. The
             band is jobfound's own estimate (Gitforce's frontend role: LinkedIn states no
             pay, jobfound says "3-5 LPA"), so it is a LISTED figure, never pay evidence.
  instahyre  instahyre.com/jobs-in-<city>/ — company, title, city, founded, headcount
             and a one-line blurb per card. No pay.
  community  offer posts found by keyless search ("SDE 1 offer 37 LPA Hyderabad startup")
             — company names only, as leads to look up on LinkedIn. A number from a forum
             is an aggregate, never a company's pay evidence.

Pure functions only — no fetching, no database (jobhunter/india_jobs.py does that).
Layering: scrapers — normalize below.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from html import unescape
from urllib.parse import parse_qs, quote, unquote, urlparse

from jobhunter import normalize as norm

LINKEDIN_SOURCE = "linkedin-jobs"
SEARCH = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
GEO = {"Bengaluru": "Bengaluru, Karnataka, India", "Hyderabad": "Hyderabad, Telangana, India"}
KEYWORDS = ["software engineer", "AI engineer", "ML engineer", "backend engineer", "frontend engineer",
            "full stack engineer", "SDE", "founding engineer", "GenAI engineer"]


def search_url(keywords: str, location: str, start: int = 0, *, experience: str | None = "1,2",
               posted: str | None = "r604800") -> str:
    """The guest search fragment: f_E 1,2 = internship + entry level, f_TPR r604800 = past week."""
    q = f"?keywords={quote(keywords)}&location={quote(location)}"
    if experience:
        q += f"&f_E={quote(experience)}"
    if posted:
        q += f"&f_TPR={posted}"
    return f"{SEARCH}{q}&start={int(start)}"


_JOB_ID = re.compile(r"(?:/jobs/view/(?:[^/?#]*?-)?|currentJobId=|jobPosting:)(\d{8,12})\b")


def job_id(url: str | None) -> str | None:
    m = _JOB_ID.search(unquote(url or ""))
    return m.group(1) if m else None


def job_url(jid: str) -> str:
    """One canonical URL per posting — the dedup key Job.url."""
    return f"https://www.linkedin.com/jobs/view/{jid}"


def company_slug(url: str | None) -> str | None:
    m = re.search(r"linkedin\.com/company/([^/?#\s)]+)", url or "")
    return m.group(1) if m else None


def company_url(slug: str) -> str:
    return f"https://www.linkedin.com/company/{slug}"


_AGO = re.compile(r"\b(\d+|an?|one)\s+(minute|hour|day|week|month|year)s?\s+ago\b", re.I)
_UNIT_DAYS = {"minute": 1 / 1440, "hour": 1 / 24, "day": 1, "week": 7, "month": 30, "year": 365}


def relative_date(text: str | None, now: datetime | None = None) -> datetime | None:
    """'35 minutes ago', '2 days ago', '1 week ago' -> a datetime; 'just now' -> now."""
    if not text:
        return None
    now = now or datetime.utcnow()
    if re.search(r"\bjust now\b", text, re.I):
        return now
    m = _AGO.search(text)
    if not m:
        return None
    n = 1 if m.group(1).lower() in ("a", "an", "one") else int(m.group(1))
    return now - timedelta(days=n * _UNIT_DAYS[m.group(2).lower()])


@dataclass
class Card:
    """One job card from a search page."""

    title: str
    company: str
    url: str                       # canonical linkedin.com/jobs/view/<id>
    job_id: str
    location: str | None = None
    company_url: str | None = None
    posted_at: datetime | None = None
    salary_raw: str | None = None  # LinkedIn shows the employer-set pay band on some cards
    source: str = LINKEDIN_SOURCE
    extra: dict = field(default_factory=dict)


_MONEY_HINT = re.compile(r"[₹$€£]|\b(?:INR|USD|LPA|lakhs?)\b", re.I)


def _md_cards(text: str, now: datetime | None) -> list[Card]:
    out: list[Card] = []
    for chunk in re.split(r"\n(?=\*\s+\[)", "\n" + text):
        m = re.search(r"^\*\s+\[(?P<title>[^\]]+)\]\((?P<url>https?://[a-z.]*linkedin\.com/jobs/view/[^)\s]+)\)",
                      chunk.strip())
        if not m:
            continue
        jid = job_id(m.group("url"))
        if not jid:
            continue
        cm = re.search(r"^####\s+\[(?P<name>[^\]]+)\]\((?P<curl>[^)\s]+)\)", chunk, re.M) or \
            re.search(r"^####\s+(?P<name>[^\n\[]+?)\s*$", chunk, re.M)
        if not cm:
            continue
        name = cm.group("name").strip()
        curl = cm.groupdict().get("curl")
        tail = chunk[cm.end():].strip().splitlines()
        line = next((ln for ln in tail if ln.strip()), "")
        parts = [p.strip() for p in re.split(r"\s{2,}", line.strip()) if p.strip()]
        location = parts[0] if parts and not _AGO.search(parts[0]) else None
        salary = next((p for p in parts[1:] if _MONEY_HINT.search(p)), None)
        out.append(Card(title=unescape(m.group("title")).strip(), company=unescape(name), url=job_url(jid),
                        job_id=jid, location=location, company_url=curl.split("?")[0] if curl else None,
                        posted_at=relative_date(line, now), salary_raw=salary))
    return out


def _html_cards(html: str, now: datetime | None) -> list[Card]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    out: list[Card] = []
    for card in soup.select("div.base-card, div.job-search-card, li > div.base-search-card"):
        link = card.select_one("a.base-card__full-link") or card.select_one("a[href*='/jobs/view/']")
        urn = card.get("data-entity-urn") or ""
        jid = job_id(urn) or job_id(link.get("href") if link else None)
        title_el = card.select_one("h3.base-search-card__title") or card.select_one("h3")
        co_el = card.select_one("h4.base-search-card__subtitle") or card.select_one("h4")
        if not jid or not title_el or not co_el:
            continue
        co_link = co_el.select_one("a")
        loc = card.select_one("span.job-search-card__location")
        when = card.select_one("time")
        sal = card.select_one("span.job-search-card__salary-info")
        posted = None
        if when is not None and when.get("datetime"):
            try:
                posted = datetime.fromisoformat(when["datetime"])
            except ValueError:
                posted = relative_date(when.get_text(" "), now)
        out.append(Card(
            title=" ".join(title_el.get_text(" ").split()), company=" ".join(co_el.get_text(" ").split()),
            url=job_url(jid), job_id=jid,
            location=" ".join(loc.get_text(" ").split()) if loc else None,
            company_url=(co_link.get("href") or "").split("?")[0] if co_link else None,
            posted_at=posted, salary_raw=" ".join(sal.get_text(" ").split()) if sal else None))
    return out


def parse_cards(text: str | None, now: datetime | None = None) -> list[Card]:
    """Every job card on one guest search page — Jina Markdown or LinkedIn's own HTML."""
    if not text:
        return []
    cards = _html_cards(text, now) if ("base-card" in text or "base-search-card" in text) else _md_cards(text, now)
    seen: set[str] = set()
    return [c for c in cards if not (c.job_id in seen or seen.add(c.job_id))]


_WALL = re.compile(r"authwall|sign in to view more|join linkedin to see|uas/login\?session_redirect=[^)\s]*jobs-guest"
                   r"|security verification|unusual activity|let's do a quick security check"
                   r'|http-equiv="refresh"[^>]*url=https://www\.linkedin\.com', re.I)


def walled(text: str | None) -> bool:
    """A login / challenge page instead of content."""
    if not text or not text.strip():
        return True
    return bool(_WALL.search(text[:6000])) and "/jobs/view/" not in text[:20000]


def _criterion(text: str, label: str) -> str | None:
    m = re.search(rf"{label}\s*\n+\s*([^\n#*]+?)\s*(?:\n|$)", text, re.I)
    return m.group(1).strip() if m else None


def parse_job_page(text: str | None) -> dict:
    """The public job page: description, criteria, and the employer's stated pay.

    `salary_raw` is LinkedIn's "Base pay range" when present, else the description's
    first sentence that carries a pay figure — the posting's own words either way."""
    out = {"description": None, "seniority": None, "employment_type": None, "industry": None,
           "salary_raw": None, "location": None}
    if not text:
        return out
    t = text.replace("\r", "")
    out["seniority"] = _criterion(t, "Seniority level")
    out["employment_type"] = _criterion(t, "Employment type")
    out["industry"] = _criterion(t, "Industries")
    start = None
    for pat in (r"\*\*About the job:?\*\*", r"About the job:?\s*\n", r"\[Report this job\][^\n]*\n"):
        m = re.search(pat, t, re.I)
        if m:
            start = m.end() if "Report" in pat else m.start()
            break
    if start is None:
        cookie = [m.end() for m in re.finditer(r"Cookie Policy[^\n]*\n", t)]
        start = cookie[-1] if cookie else 0
    end = len(t)
    for pat in (r"\n\s*Show more\s+Show less", r"\n[*\s]*#*\s*Seniority level", r"\n#+\s*Similar jobs"):
        m = re.search(pat, t[start:], re.I)
        if m:
            end = min(end, start + m.start())
    desc = t[start:end].strip()
    desc = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", desc)            # images
    desc = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", desc)        # links -> their text
    desc = re.sub(r"\n{3,}", "\n\n", desc).strip()
    out["description"] = desc[:12000] or None
    m = re.search(r"Base pay range\s*\n+\s*([^\n]+)", t, re.I)
    if m and _MONEY_HINT.search(m.group(1)):
        out["salary_raw"] = m.group(1).strip()[:160]
    elif desc:
        for sent in re.split(r"(?<=[.!?])\s+|\n+", desc):
            if _MONEY_HINT.search(sent) and norm.parse_salary(sent).ok() and len(sent) < 400:
                out["salary_raw"] = sent.strip()[:300]
                break
    m = re.search(r"^####\s+\[[^\]]+\]\([^)]*\)\s*([^\n]+)$", t, re.M)
    if m:
        out["location"] = m.group(1).strip()
    return out


def _redirect_target(url: str) -> str:
    """linkedin.com/redir/redirect?url=https%3A%2F%2Fgitforce%2Eai -> https://gitforce.ai"""
    if "linkedin.com/redir" in url:
        target = (parse_qs(urlparse(url).query).get("url") or [""])[0]
        if target:
            return unquote(target).replace("%2E", ".")
    return url


def parse_company_page(text: str | None) -> dict:
    """The public company page: website, industry, size, headquarters, the About text."""
    out = {"website": None, "industry": None, "size": None, "headcount": None, "headcount_min": None, "hq": None,
           "about": None}
    if not text:
        return out
    t = text.replace("\r", "")
    m = re.search(r"Website\s*\[?\s*(https?://[^\s\]\)]+)\]?(?:\(([^)\s]+)\))?", t)
    if m:
        site = m.group(1)
        if "linkedin.com" in site and m.group(2):
            site = _redirect_target(m.group(2))
        elif "linkedin.com/redir" in site:
            site = _redirect_target(site)
        out["website"] = site.rstrip("/.")
    for key, label in (("industry", "Industry"), ("size", "Company size"), ("hq", "Headquarters")):
        m = re.search(rf"(?m)^\s*{label}[ \t]*(?:\n[ \t]*)?([^\n]+?)\s*$", t)
        if m:
            out[key] = m.group(1).strip()
    if out["size"]:
        nums = [int(n.replace(",", "")) for n in re.findall(r"\d[\d,]*", out["size"])]
        out["headcount"] = max(nums) if nums else None
        out["headcount_min"] = min(nums) if nums else None
    m = re.search(r"#+\s*About us\s*\n+(.+?)\n+\s*Website", t, re.S)
    if m:
        out["about"] = " ".join(m.group(1).split())[:1200]
    return out


# ------------------------------------------------------------------ jobfound.org

JOBFOUND_SOURCE = "jobfound"
JOBFOUND_INDIA = "https://jobfound.org/jobs/india"
_JF_CARD = re.compile(
    r"\[\]\((?P<url>https://jobfound\.org/job/[^)\s]+)\)\S?\s*\n+###\s+(?P<title>[^\n]+)\n+"
    r"(?P<company>[^\n·]+)·(?P<location>[^\n·]+)·(?P<ago>[^\n]+)\n(?P<meta>[^\n]+)")
_JF_META = re.compile(r"^(?P<kind>Full-time|Part-time|Internship|Contract|Temporary)?\s*"
                      r"(?P<band>Competitive|[^\s]*?\d[\d.,]*\s*-\s*[\d.,]+\s*LPA|[\d.,]+\s*LPA|\$[^ ]+(?: - \$[^ ]+)?)?\s*"
                      r"(?P<exp>Fresher|\d+(?:-\d+)?\+? yrs)?", re.I)


def parse_jobfound(text: str | None, now: datetime | None = None) -> list[dict]:
    """Cards on a jobfound listing. `listed_band` is jobfound's band — an estimate."""
    out = []
    for m in _JF_CARD.finditer(text or ""):
        meta = m.group("meta").strip()
        mm = _JF_META.match(meta)
        band = (mm.group("band") or "").strip() if mm else ""
        out.append({
            "url": m.group("url"), "title": m.group("title").strip(), "company": m.group("company").strip(),
            "location": m.group("location").strip(), "posted_at": relative_date(m.group("ago"), now),
            "kind": (mm.group("kind") or "") if mm else "", "listed_band": band if band and band != "Competitive" else None,
            "experience": (mm.group("exp") or "") if mm else "", "meta": meta,
        })
    return out


# ------------------------------------------------------------------ instahyre

INSTAHYRE_SOURCE = "instahyre"
INSTAHYRE_CITY = {"Bengaluru": "https://www.instahyre.com/jobs-in-bangalore/",
                  "Hyderabad": "https://www.instahyre.com/jobs-in-hyderabad/"}
_IH_CARD = re.compile(
    r"##\s+(?P<company>[^\n]+?)\s+-\s+(?P<title>[^\n!]+?)\s*!\[[^\]]*\]\([^)]*location[^)]*\)(?P<location>[^!]+?)\s*"
    r"!\[[^\]]*\]\([^)]*flag[^)]*\)Founded in (?P<founded>\d{4})\s*"
    r"!\[[^\]]*\]\([^)]*users[^)]*\)(?P<size>[\d,+\- ]+?employees)\s*(?P<blurb>.*?)\]\((?P<url>https://www\.instahyre\.com/job-[^)\s]+)\)",
    re.S)


def parse_instahyre(text: str | None) -> list[dict]:
    out = []
    for m in _IH_CARD.finditer(text or ""):
        blurb = re.sub(r"\s+\*\s+.*$", "", m.group("blurb").strip(), flags=re.S)   # drop the skill tags
        sizes = [int(n.replace(",", "")) for n in re.findall(r"\d[\d,]*", m.group("size"))]
        out.append({"company": m.group("company").strip(), "title": m.group("title").strip(),
                    "location": m.group("location").strip(), "founded": int(m.group("founded")),
                    "size": m.group("size").strip(), "headcount": max(sizes) if sizes else None,
                    "headcount_min": min(sizes) if sizes else None,
                    "blurb": blurb[:600], "url": m.group("url")})
    return out


# ------------------------------------------------------------------ community offer posts -> names

COMMUNITY_QUERIES = [
    '"SDE 1" offer "LPA" Hyderabad startup', '"SDE 1" offer "LPA" Bangalore startup',
    '"37 LPA" fresher Bengaluru startup', '"30 LPA" fresher offer startup Hyderabad',
    'r/developersIndia fresher offer LPA startup Bangalore', 'leetcode discuss compensation India fresher 2026 startup',
    '"new grad" India offer LPA startup AI', '"40 LPA" fresher offer startup India',
]
_LEAD_PATTERNS = [
    re.compile(r"\b(?:[Jj]oined|[Jj]oining|[Cc]onverted at)\s+(?P<name>[A-Z][\w&.\-]*(?:\s+(?:AI|Labs|[A-Z][\w&.\-]*)){0,2})"),
    re.compile(r"\b(?:offer|offered|joined|joining|selected|got in|internship|SDE[- ]?(?:1|I)|intern)\s+(?:from|at|@|in|by)\s+"
               r"(?P<name>[A-Z][\w&.\-]*(?:\s+(?:AI|Labs|[A-Z][\w&.\-]*)){0,2})"),
    re.compile(r"(?P<name>[A-Z][\w&.\-]*(?:\s+(?:AI|Labs|[A-Z][\w&.\-]*)){0,2})\s+(?:SDE[- ]?(?:1|I|2|II)\b|offer\b|"
               r"compensation\b|new grad offer)"),
]
_LEAD_STOP = {"the", "a", "an", "my", "an", "one", "this", "that", "india", "bangalore", "bengaluru", "hyderabad",
              "startup", "startups", "fresher", "freshers", "sde", "offer", "new", "got", "help", "should", "which",
              "leetcode", "reddit", "developersindia", "compensation", "salary", "ctc", "lpa", "base", "total",
              "is", "for", "from", "need", "advice", "vs", "and", "or", "remote", "big", "tech", "product", "service",
              "it", "mnc", "ai", "ml", "faang", "maang", "tier", "college", "campus", "off", "on", "pune", "chennai",
              "mumbai", "delhi", "noida", "gurgaon", "gurugram", "series", "seed", "funded", "yc", "early", "final",
              "accepted", "rejected", "received", "joining", "bonus", "stock", "esop", "esops", "january", "2026",
              "2025", "2027", "summer", "winter", "posted", "discuss"}


def offer_leads(text: str | None) -> list[str]:
    """Company names a forum post says it got an offer from. Leads to look up, not facts."""
    names: list[str] = []
    for rx in _LEAD_PATTERNS:
        for m in rx.finditer(text or ""):
            name = m.group("name").strip(" .-,;:!?")
            words = name.split()
            while len(words) > 1 and words[-1].lower() in _LEAD_STOP and words[-1] not in ("AI", "ML"):
                words.pop()
            while words and words[0].lower() in _LEAD_STOP and len(words) > 1:
                words.pop(0)
            name = " ".join(words)
            if len(name) < 3 or name.lower() in _LEAD_STOP or name.isdigit() or name.upper() == name and len(name) > 6:
                continue
            if name not in names:
                names.append(name)
    return names


__all__ = ["Card", "search_url", "parse_cards", "parse_job_page", "parse_company_page", "parse_jobfound",
           "parse_instahyre", "offer_leads", "job_id", "job_url", "company_slug", "company_url", "relative_date",
           "walled", "GEO", "KEYWORDS", "LINKEDIN_SOURCE", "JOBFOUND_SOURCE", "INSTAHYRE_SOURCE"]
