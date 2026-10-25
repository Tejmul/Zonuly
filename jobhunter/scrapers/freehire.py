"""freehire.me — an open job aggregator (~50 ATS platforms plus Telegram job channels)
with a public JSON API and a seniority facet. No key, no account.

2026-10-26, Tejmul: fresher roles only (intern → full-time, SDE-1, AI Engineer I), and
any tech role will do — "if it is in tech, it shouldn't matter". So this asks for
`junior` and `intern` postings and keeps everything freehire tags as tech. Pay power is
the first filter, but freehire almost never carries pay; it supplies companies and
roles, and the stipend / PPO bar and pay-power grading (targeting.py) judge them.

India is Bengaluru and Hyderabad only; elsewhere only remote-global postings.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urlencode

import httpx

from jobhunter import CONFIG
from jobhunter.scrapers.base import RawJob, get_json, html_to_text, parse_ts

log = logging.getLogger(__name__)

SOURCE = "freehire"
API = "https://freehire.me/api/v1/agent/jobs/search"
_C = (CONFIG.get("sources") or {}).get("freehire_opts") or {}
DAYS = int(_C.get("posted_within_days", 14))
PAGES = int(_C.get("max_pages", 10))      # per query, 100 a page (the API caps offset + limit at 10,000)
SENIORITY = ("junior", "intern")
INDIA_CITIES = ("bengaluru", "bangalore", "hyderabad")
# freehire scrapes whole careers pages, so a tech-tagged posting can still be unpaid
_UNPAID = re.compile(r"\bunpaid\b|\bvolunteer\b", re.I)
_UTM = re.compile(r"[?&]utm_source=freehire\.me$")


def _queries() -> list[dict]:
    out = []
    for sen in SENIORITY:
        out.append({"countries": "in", "seniority": sen})
        out.append({"regions": "global", "work_mode": "remote", "seniority": sen})
    return out


def _keep(j: dict) -> bool:
    if j.get("is_tech") != "tech" or j.get("closed_at") or not j.get("company") or not j.get("title"):
        return False
    if (j.get("reality") or {}).get("fake_freshness") or _UNPAID.search(j["title"]):
        return False
    years = (j.get("enrichment") or {}).get("experience_years_min")
    if isinstance(years, (int, float)) and years > 1:
        return False
    if "in" in (j.get("countries") or []):
        return any(c.lower() in INDIA_CITIES for c in j.get("cities") or [])
    return True


def _raw(j: dict) -> RawJob:
    remote = j.get("work_mode") == "remote" or "global" in (j.get("regions") or [])
    sal = j.get("salary")
    return RawJob(
        company_name=j["company"].strip(),
        title=j["title"].strip(),
        url=_UTM.sub("", j.get("url") or f"https://freehire.me/jobs/{j['public_slug']}"),
        source=SOURCE,
        location=j.get("location") or ", ".join(j.get("cities") or []) or None,
        remote=remote,
        description=html_to_text(j.get("description")),
        posted_at=parse_ts(j.get("posted_at")),
        salary_raw=str(sal) if sal else None,
        extra={"apply_url": j.get("url"), "post_source": f"freehire/{j.get('source')}",
               "freehire_slug": j.get("public_slug"), "seniority": (j.get("enrichment") or {}).get("seniority")},
    )


async def fetch(http: httpx.AsyncClient, companies: list[dict]) -> list[RawJob]:  # noqa: ARG001 — board-wide, not per company
    seen: dict[str, RawJob] = {}
    for q in _queries():
        for page in range(PAGES):
            params = {**q, "posted_within_days": DAYS, "limit": 100, "offset": page * 100, "description_format": "text"}
            data = await get_json(http, f"{API}?{urlencode(params)}")
            rows = (data or {}).get("data") or []
            for j in rows:
                if _keep(j) and j.get("public_slug") not in seen:
                    seen[j["public_slug"]] = _raw(j)
            if len(rows) < 100:
                break
    log.info("freehire: %d fresher tech postings", len(seen))
    return list(seen.values())
