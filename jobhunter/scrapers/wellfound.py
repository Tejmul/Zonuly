"""Wellfound (ex-AngelList Talent) — startup roles, India and remote.

Tejmul, 2026-09-19: "we have indeed found remote [Wellfound], where you can find a lot
of other clients and work also — scrape from there." Wellfound's SEO landing pages
(`/role/l/<role>/<location>`, `/role/r/<role>` for remote) are server-rendered and carry
the whole result set in `__NEXT_DATA__` as an Apollo cache: `StartupResult` rows
(name, slug, size) each pointing at their `JobListingSearchResult` rows (title, remote
config, locations, compensation, posted-at, the full description). No login, no
Playwright, no API key — one plain GET per page, read like any other public page.

The company pages and the logged-in job search are JS-only and are not touched. This
is a listing source: it says who is hiring for what; hiring_verify still decides
against the company's own site, and the fit gate still reads the description.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta, timezone

import httpx

from jobhunter import CONFIG
from jobhunter.scrapers.base import RawJob, get_text, parse_ts

log = logging.getLogger(__name__)

SOURCE = "wellfound"
BASE = "https://wellfound.com"
_S = CONFIG.get("sources") or {}

# role slugs as Wellfound spells them in its landing-page URLs
ROLES: list[str] = _S.get("wellfound_roles") or [
    "software-engineer", "backend-engineer", "full-stack-engineer", "machine-learning-engineer",
    "artificial-intelligence-engineer", "data-engineer", "founding-engineer", "devops-engineer",
]
# location slugs; "remote" uses the /role/r/ form
LOCATIONS: list[str] = _S.get("wellfound_locations") or ["india", "bangalore", "hyderabad", "remote"]
PAGES = int(_S.get("wellfound_pages", 2))
GAP_S = float(_S.get("wellfound_gap_seconds", 1.5))
# Wellfound keeps listings up for years; a 2022 posting is not a company hiring now
MAX_AGE_DAYS = int(_S.get("wellfound_max_age_days", 120))

_NEXT_DATA = re.compile(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
_KEEP_TITLE = re.compile(r"\b(engineer|developer|sde|swe|scientist|architect|programmer|ml|ai)\b", re.I)
_REMOTE_KINDS = {"REMOTE", "REMOTE_ONLY", "WFH_FLEXIBLE", "HYBRID"}
_SIZE = {
    "SIZE_1_10": 10, "SIZE_11_50": 50, "SIZE_51_200": 200, "SIZE_201_500": 500,
    "SIZE_501_1000": 1000, "SIZE_1001_5000": 5000, "SIZE_5000_PLUS": 5001,
}


def page_url(role: str, location: str, page: int) -> str:
    path = f"/role/r/{role}" if location == "remote" else f"/role/l/{role}/{location}"
    return f"{BASE}{path}" + (f"?page={page}" if page > 1 else "")


def parse(html: str) -> list[RawJob]:
    """Every (startup, job) pair on one landing page."""
    m = _NEXT_DATA.search(html or "")
    if not m:
        return []
    try:
        data = json.loads(m.group(1))["props"]["pageProps"]["apolloState"]["data"]
    except (ValueError, KeyError, TypeError):
        return []

    out: list[RawJob] = []
    seen: set[str] = set()
    for key, startup in data.items():
        if not key.startswith("StartupResult:") or not startup.get("name"):
            continue
        for ref in startup.get("highlightedJobListings") or []:
            job = data.get((ref or {}).get("__ref") or "")
            if not job or not job.get("id") or not job.get("title"):
                continue
            if job["id"] in seen or not _KEEP_TITLE.search(job["title"]):
                continue
            seen.add(job["id"])
            posted = parse_ts(job.get("liveStartAt"))
            if posted and posted.replace(tzinfo=None) < datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=MAX_AGE_DAYS):
                continue
            rc = job.get("remoteConfig") or {}
            remote_locs = job.get("acceptedRemoteLocationNames") or []
            remote = bool(job.get("remote")) or rc.get("kind") in _REMOTE_KINDS or bool(remote_locs)
            locations = job.get("locationNames") or []
            out.append(RawJob(
                company_name=startup["name"].strip(),
                title=job["title"].strip(),
                url=f"{BASE}/jobs/{job['id']}-{job.get('slug') or 'role'}",
                source=SOURCE,
                location=", ".join(locations) if locations else ("Remote" if remote else None),
                remote=remote,
                description=job.get("description") or None,
                posted_at=posted,
                salary_raw=(job.get("compensation") or "").strip() or None,
                extra={
                    "wellfound_startup": startup.get("slug"),
                    "company_size": _SIZE.get(startup.get("companySize") or ""),
                    "job_type": job.get("jobType"),
                    "remote_kind": rc.get("kind"),
                    "remote_locations": remote_locs,
                    "years_min": job.get("yearsExperienceMin"),
                    "years_max": job.get("yearsExperienceMax"),
                    "actively_hiring": any("ACTIVELY_HIRING" in (b or {}).get("__ref", "")
                                           for b in startup.get("badges") or []),
                },
            ))
    return out


async def fetch(http: httpx.AsyncClient, companies: list[dict] | None = None) -> list[RawJob]:
    """Every role × location landing page, `PAGES` deep, one polite GET at a time."""
    out: list[RawJob] = []
    seen_urls: set[str] = set()
    pages = 0
    for role in ROLES:
        for location in LOCATIONS:
            for page in range(1, PAGES + 1):
                html = await get_text(http, page_url(role, location, page))
                pages += 1
                jobs = parse(html or "")
                if not jobs:
                    break                      # past the last page, or the page changed shape
                for j in jobs:
                    if j.url not in seen_urls:
                        seen_urls.add(j.url)
                        out.append(j)
                await asyncio.sleep(GAP_S)
    log.info("wellfound: %d pages -> %d roles", pages, len(out))
    return out
