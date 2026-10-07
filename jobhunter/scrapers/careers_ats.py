"""Careers pages and hosted ATS boards — recognising them, and reading their public JSON.

The Greenhouse / Lever / Ashby probe (discover.py) guesses a board slug from the company
name. Most Indian startups are on none of the three: they run Keka, Darwinbox, Zoho
Recruit, Freshteam, or a page on their own site — and the board is only findable by
following the "Careers" link on their homepage. This module is the acquisition half of
that: given a page's HTML (or the reader's Markdown) it says which board it links to,
which links look like a careers page, and which JobPostings the page declares in JSON-LD;
given a board it reads the board's public JSON where the provider has one.

What it never does: decide anything, write to the database, or call a model. The careers
pass (jobhunter/careers.py) does that, one layer up.

Layering: scrapers.base only.
"""

from __future__ import annotations

import json
import logging
import re
from urllib.parse import urljoin, urlparse

import httpx

from jobhunter.scrapers import ashby, greenhouse, lever
from jobhunter.scrapers.base import RawJob, html_to_text, looks_remote, parse_ts

log = logging.getLogger(__name__)

# ------------------------------------------------------------------ recognising a board

# (ats, pattern capturing the board slug). Order matters only where two could match the
# same URL; the first wins. A slug in _NOT_A_SLUG is a path word, not a company.
ATS_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("greenhouse", re.compile(r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board(?:/js)?\?for=)?([A-Za-z0-9_-]+)", re.I)),
    ("lever", re.compile(r"jobs\.(?:eu\.)?lever\.co/([A-Za-z0-9_.-]+)", re.I)),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_.%-]+)", re.I)),
    ("workable", re.compile(r"apply\.workable\.com/([A-Za-z0-9_-]+)", re.I)),
    ("workable", re.compile(r"\b([a-z0-9-]+)\.workable\.com", re.I)),
    ("smartrecruiters", re.compile(r"(?:jobs|careers)\.smartrecruiters\.com/([A-Za-z0-9_-]+)", re.I)),
    ("recruitee", re.compile(r"\b([a-z0-9-]+)\.recruitee\.com", re.I)),
    ("bamboohr", re.compile(r"\b([a-z0-9-]+)\.bamboohr\.com", re.I)),
    ("breezy", re.compile(r"\b([a-z0-9-]+)\.breezy\.hr", re.I)),
    ("keka", re.compile(r"\b([a-z0-9-]+)\.keka\.com/careers", re.I)),
    ("darwinbox", re.compile(r"\b([a-z0-9-]+)\.darwinbox\.(?:in|com)", re.I)),
    ("zohorecruit", re.compile(r"\b([a-z0-9-]+)\.zohorecruit\.(?:com|in|eu)", re.I)),
    ("freshteam", re.compile(r"\b([a-z0-9-]+)\.freshteam\.com", re.I)),
    ("teamtailor", re.compile(r"\b([a-z0-9-]+)\.teamtailor\.com", re.I)),
    ("wellfound", re.compile(r"(?:wellfound\.com|angel\.co)/company/([A-Za-z0-9_-]+)", re.I)),
    ("instahyre", re.compile(r"instahyre\.com/jobs-at-([A-Za-z0-9_-]+)", re.I)),
    ("cutshort", re.compile(r"cutshort\.io/company/([A-Za-z0-9_-]+)", re.I)),
]
_NOT_A_SLUG = {"www", "api", "app", "apply", "embed", "jobs", "careers", "career", "v0", "v1", "help",
               "support", "blog", "static", "assets", "cdn", "login", "signup", "j", "resources", "docs",
               "status", "developers", "about", "pricing", "en", "us", "in", "hire", "go", "info"}

# Job pages that are not an ATS but are a careers page all the same: kept as careers_url only.
PAGE_HOSTS = re.compile(r"(\.notion\.site/|notion\.so/|docs\.google\.com/(?:document|spreadsheets|forms)/)", re.I)

# A link that leads to the careers page, read from its URL path or its anchor text.
# "hiring" alone is a product word on half the HR-tech sites ("/products/gig-hiring"), so
# only "we're hiring" / "now hiring" count; a path segment must BE the word, not contain it.
_CAREERS_WORD = re.compile(
    r"(?:^|[/\s])(careers?|jobs?|join[-_ ]?(?:us|the[-_ ]team|our[-_ ]team)?|work[-_ ]with[-_ ]us|"
    r"we[-_ ]?(?:'re|are|re)[-_ ]hiring|now[-_ ]hiring|open[-_ ](?:roles|positions)|current[-_ ]openings|"
    r"vacanc(?:y|ies)|life[-_ ]at[-_ ][a-z0-9-]+)(?:$|[/.?#\s])", re.I)
_SKIP_LINK = re.compile(r"(\.(?:png|jpe?g|svg|gif|webp|css|js|pdf|ico)(?:\?|$)|^mailto:|^tel:|^javascript:|"
                        r"linkedin\.com|twitter\.com|x\.com/|facebook\.com|instagram\.com|youtube\.com)", re.I)

_HREF = re.compile(r"""(?:href|src|data-href|data-url)\s*=\s*["']([^"'<>\s]+)["']""", re.I)
_A_TAG = re.compile(r"<a\b[^>]*href\s*=\s*[\"']([^\"'<>\s]+)[\"'][^>]*>(.*?)</a>", re.I | re.S)
_MD_LINK = re.compile(r"\[([^\]]{0,120})\]\((https?://[^)\s]+)\)")
_BARE_URL = re.compile(r"https?://[^\s\"'<>)\]]+")


def detect_ats(text: str | None) -> tuple[str, str] | None:
    """(ats, slug) for the first hosted board this URL / HTML / Markdown points at."""
    if not text:
        return None
    for ats, pat in ATS_PATTERNS:
        for m in pat.finditer(text):
            slug = m.group(1).strip("/.").split("?")[0]
            if slug and slug.lower() not in _NOT_A_SLUG and len(slug) > 1:
                return ats, slug
    return None


def links(html_or_md: str, base: str) -> list[tuple[str, str]]:
    """(absolute url, anchor text) for every link on a page — HTML anchors, any other
    href/src (iframes and embed scripts carry the ATS), and Markdown links from the reader."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(href: str, text: str = "") -> None:
        href = (href or "").strip()
        if not href or href.startswith("#") or _SKIP_LINK.search(href):
            return
        url = urljoin(base, href)
        if not url.startswith("http") or url in seen:
            return
        seen.add(url)
        out.append((url, re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text or "")).strip()[:80]))

    for href, inner in _A_TAG.findall(html_or_md or ""):
        add(href, inner)
    for href in _HREF.findall(html_or_md or ""):
        add(href)
    for text, href in _MD_LINK.findall(html_or_md or ""):
        add(href, text)
    for href in _BARE_URL.findall(html_or_md or ""):
        add(href)
    return out


def careers_links(page_links: list[tuple[str, str]], site: str) -> list[str]:
    """Links that look like the careers page: on their own site (or a job-page host), the
    path or the anchor text says careers / jobs / join us. Shortest path first — /careers
    before /careers/senior-backend-engineer-2023."""
    host = (urlparse(site).hostname or "").lower().removeprefix("www.")
    out: list[str] = []
    for url, text in page_links:
        u = urlparse(url)
        h = (u.hostname or "").lower().removeprefix("www.")
        own = bool(host) and (h == host or h.endswith("." + host))
        if not (own or PAGE_HOSTS.search(url)):
            continue
        if _CAREERS_WORD.search(u.path or "") or (text and _CAREERS_WORD.search(text) and len(text) <= 40):
            clean = url.split("#")[0]
            if clean not in out:
                out.append(clean)
    out.sort(key=lambda x: (0 if _CAREERS_WORD.search(urlparse(x).path or "") else 1, len(x)))
    return out


# ------------------------------------------------------------------ JSON-LD JobPosting

_LD = re.compile(r"<script[^>]+type\s*=\s*[\"']application/ld\+json[\"'][^>]*>(.*?)</script>", re.I | re.S)


def _walk(node):
    if isinstance(node, list):
        for n in node:
            yield from _walk(n)
    elif isinstance(node, dict):
        yield node
        for key in ("@graph", "itemListElement", "item"):
            if key in node:
                yield from _walk(node[key])


def _ld_location(posting: dict) -> tuple[str | None, bool]:
    remote = str(posting.get("jobLocationType") or "").upper() == "TELECOMMUTE"
    places = posting.get("jobLocation") or []
    places = places if isinstance(places, list) else [places]
    parts: list[str] = []
    for p in places:
        addr = (p or {}).get("address") if isinstance(p, dict) else None
        if isinstance(addr, str):
            parts.append(addr)
        elif isinstance(addr, dict):
            country = addr.get("addressCountry")
            country = country.get("name") if isinstance(country, dict) else country
            bits = [addr.get("addressLocality"), addr.get("addressRegion"), country]
            parts.append(", ".join(str(b) for b in bits if b))
    loc = "; ".join(p for p in parts if p) or None
    return loc, remote


def jsonld_jobs(html: str, page_url: str, company_name: str, *, source: str = "careers") -> list[RawJob]:
    """Every JobPosting the page declares about itself in JSON-LD — structured, so trusted."""
    out: list[RawJob] = []
    for blob in _LD.findall(html or ""):
        try:
            data = json.loads(blob.strip())
        except (ValueError, TypeError):
            continue
        for node in _walk(data):
            kind = node.get("@type")
            kinds = kind if isinstance(kind, list) else [kind]
            if "JobPosting" not in kinds or not node.get("title"):
                continue
            loc, remote = _ld_location(node)
            desc = html_to_text(node.get("description")) if node.get("description") else None
            out.append(RawJob(
                company_name=company_name, title=str(node["title"]).strip(),
                url=str(node.get("url") or node.get("sameAs") or f"{page_url}#{_slug(node['title'])}"),
                source=source, location=loc, remote=remote or looks_remote(loc, node.get("title")),
                description=desc, posted_at=parse_ts(node.get("datePosted")),
            ))
    return out


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")[:80]


# ------------------------------------------------------------------ public board JSON

# What a board read found: HIT (the board answered, possibly with zero roles), MISS (no
# such board) or ERROR (network / 429 / 5xx — retryable, says nothing about the company).
HIT, MISS, ERROR = "hit", "miss", "error"


async def _get(http: httpx.AsyncClient, url: str, **kw) -> tuple[str, object]:
    try:
        r = await http.get(url, **kw)
    except Exception as e:  # noqa: BLE001 — a network failure is retryable, not a miss
        return ERROR, f"{type(e).__name__}"
    if r.status_code in (404, 410, 400, 401, 403):
        return MISS, None
    if r.status_code == 429 or r.status_code >= 500:
        return ERROR, f"HTTP {r.status_code}"
    try:
        return HIT, r.json()
    except ValueError:
        return MISS, None


def _place(*bits) -> str | None:
    return ", ".join(str(b) for b in bits if b and str(b).strip()) or None


async def _workable(http, slug, name):
    st, data = await _get(http, f"https://apply.workable.com/api/v1/widget/accounts/{slug}")
    if st != HIT:
        return st, [] if st == MISS else data
    out = []
    for j in (data or {}).get("jobs") or []:
        loc = _place(j.get("city"), j.get("state"), j.get("country"))
        url = j.get("url") or j.get("application_url") or (f"https://apply.workable.com/{slug}/j/{j.get('shortcode')}/" if j.get("shortcode") else None)
        if j.get("title") and url:
            out.append(RawJob(company_name=name, title=j["title"], url=url, source="workable", location=loc,
                              remote=bool(j.get("telecommuting")) or looks_remote(loc, j.get("title")),
                              description=html_to_text(j.get("description")) or None,
                              posted_at=parse_ts(j.get("published_on") or j.get("created_at")),
                              ats="workable", ats_slug=slug))
    return HIT, out


async def _smartrecruiters(http, slug, name):
    st, data = await _get(http, f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100")
    if st != HIT:
        return st, [] if st == MISS else data
    out = []
    for j in (data or {}).get("content") or []:
        loc = j.get("location") or {}
        where = loc.get("fullLocation") or _place(loc.get("city"), loc.get("region"), loc.get("country"))
        if j.get("name") and j.get("id"):
            out.append(RawJob(company_name=name, title=j["name"], url=f"https://jobs.smartrecruiters.com/{slug}/{j['id']}",
                              source="smartrecruiters", location=where,
                              remote=bool(loc.get("remote")) or looks_remote(where, j.get("name")),
                              posted_at=parse_ts(j.get("releasedDate")), ats="smartrecruiters", ats_slug=slug))
    return HIT, out


async def _recruitee(http, slug, name):
    st, data = await _get(http, f"https://{slug}.recruitee.com/api/offers/")
    if st != HIT:
        return st, [] if st == MISS else data
    out = []
    for j in (data or {}).get("offers") or []:
        loc = j.get("location") or _place(j.get("city"), j.get("country"))
        url = j.get("careers_url") or (f"https://{slug}.recruitee.com/o/{j.get('slug')}" if j.get("slug") else None)
        if j.get("title") and url:
            out.append(RawJob(company_name=name, title=j["title"], url=url, source="recruitee", location=loc,
                              remote=bool(j.get("remote")) or looks_remote(loc, j.get("title")),
                              description=html_to_text(j.get("description")) or None,
                              posted_at=parse_ts(j.get("published_at")), ats="recruitee", ats_slug=slug))
    return HIT, out


async def _bamboohr(http, slug, name):
    st, data = await _get(http, f"https://{slug}.bamboohr.com/careers/list", headers={"Accept": "application/json"})
    if st != HIT:
        return st, [] if st == MISS else data
    out = []
    for j in (data or {}).get("result") or []:
        loc = j.get("location") or {}
        ats_loc = j.get("atsLocation") or {}
        where = _place(loc.get("city") or ats_loc.get("city"), loc.get("state") or ats_loc.get("state"), ats_loc.get("country"))
        if j.get("jobOpeningName") and j.get("id"):
            out.append(RawJob(company_name=name, title=j["jobOpeningName"], url=f"https://{slug}.bamboohr.com/careers/{j['id']}",
                              source="bamboohr", location=where,
                              remote=bool(j.get("isRemote")) or looks_remote(where, j.get("jobOpeningName")),
                              ats="bamboohr", ats_slug=slug))
    return HIT, out


async def _breezy(http, slug, name):
    st, data = await _get(http, f"https://{slug}.breezy.hr/json")
    if st != HIT:
        return st, [] if st == MISS else data
    out = []
    for j in data if isinstance(data, list) else []:
        loc = j.get("location") or {}
        where = loc.get("name") if isinstance(loc, dict) else None
        if j.get("name") and j.get("url"):
            out.append(RawJob(company_name=name, title=j["name"], url=j["url"], source="breezy", location=where,
                              remote=bool(isinstance(loc, dict) and loc.get("is_remote")) or looks_remote(where, j.get("name")),
                              posted_at=parse_ts(j.get("published_date")), ats="breezy", ats_slug=slug))
    return HIT, out


def _existing(fetch):
    """Greenhouse / Lever / Ashby: the fetchers in this package. They swallow errors into
    an empty list, so an empty answer is reported as a MISS."""
    async def run(http, slug, name):
        jobs = await fetch(http, slug, name)
        return (HIT, jobs) if jobs else (MISS, [])
    return run


# Boards with a public JSON API. The rest (Keka, Darwinbox, Zoho Recruit, Freshteam,
# Teamtailor, Wellfound, Instahyre, Cutshort) are read as pages by the careers pass.
API_FETCHERS = {
    "greenhouse": _existing(greenhouse.fetch_board),
    "lever": _existing(lever.fetch_board),
    "ashby": _existing(ashby.fetch_board),
    "workable": _workable,
    "smartrecruiters": _smartrecruiters,
    "recruitee": _recruitee,
    "bamboohr": _bamboohr,
    "breezy": _breezy,
}

# The board's own page, for the ones read as text — and for careers_url on every board.
BOARD_PAGES = {
    "greenhouse": "https://job-boards.greenhouse.io/{slug}",
    "lever": "https://jobs.lever.co/{slug}",
    "ashby": "https://jobs.ashbyhq.com/{slug}",
    "workable": "https://apply.workable.com/{slug}/",
    "smartrecruiters": "https://jobs.smartrecruiters.com/{slug}",
    "recruitee": "https://{slug}.recruitee.com/",
    "bamboohr": "https://{slug}.bamboohr.com/careers",
    "breezy": "https://{slug}.breezy.hr/",
    "keka": "https://{slug}.keka.com/careers",
    "darwinbox": "https://{slug}.darwinbox.in/ms/candidate/careers",
    "zohorecruit": "https://{slug}.zohorecruit.com/jobs/Careers",
    "freshteam": "https://{slug}.freshteam.com/jobs",
    "teamtailor": "https://{slug}.teamtailor.com/jobs",
    "wellfound": "https://wellfound.com/company/{slug}/jobs",
    "instahyre": "https://www.instahyre.com/jobs-at-{slug}/",
    "cutshort": "https://cutshort.io/company/{slug}/jobs",
}


async def read_board(http: httpx.AsyncClient, ats: str, slug: str, company_name: str) -> tuple[str, list[RawJob] | str | None]:
    """(HIT, jobs) | (MISS, []) | (ERROR, why). Only for boards in API_FETCHERS."""
    fetch = API_FETCHERS.get(ats)
    if fetch is None:
        return MISS, []
    try:
        return await fetch(http, slug, company_name)
    except Exception as e:  # noqa: BLE001 — a malformed board must not end the pass
        log.warning("board %s/%s failed: %s", ats, slug, e)
        return ERROR, f"{type(e).__name__}"


__all__ = ["detect_ats", "links", "careers_links", "jsonld_jobs", "read_board", "API_FETCHERS",
           "BOARD_PAGES", "ATS_PATTERNS", "PAGE_HOSTS", "HIT", "MISS", "ERROR"]
