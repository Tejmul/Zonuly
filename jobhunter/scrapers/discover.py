"""ATS discovery — given a company name/website, find which public job board it uses.

Used two ways: to validate/repair companies.yaml, and to auto-grow the seed list
from YC and other company feeds.
"""

from __future__ import annotations

import logging
import re

import httpx

from jobhunter.scrapers.base import gather_limited

log = logging.getLogger(__name__)

PROBES = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
    "lever": "https://api.lever.co/v0/postings/{slug}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{slug}",
}


def slug_candidates(name: str, website: str | None = None) -> list[str]:
    """Plausible board tokens for a company, most likely first."""
    out: list[str] = []

    def add(s: str) -> None:
        s = s.strip("-").lower()
        if s and s not in out:
            out.append(s)

    base = re.sub(r"[^a-z0-9\s-]", "", name.lower())
    base = re.sub(r"\b(inc|llc|ltd|limited|corp|corporation|technologies|technology|labs|ai|io)\b", " ", base)
    add(re.sub(r"[\s-]+", "", base))          # "scale ai" -> "scale"
    add(re.sub(r"[\s-]+", "-", base.strip()))  # "scale ai" -> "scale"
    add(re.sub(r"[^a-z0-9]", "", name.lower()))  # "scale ai" -> "scaleai"
    add(re.sub(r"[^a-z0-9]+", "-", name.lower()))

    if website:
        host = re.sub(r"^https?://", "", website).split("/")[0]
        host = host.removeprefix("www.")
        add(host.split(".")[0])
    return [s for s in out if len(s) > 1][:5]


# What a probe found. MISS is definitive (the provider answered: no such board, or an
# empty one); ERROR is not (timeout, 429, 5xx, a body that did not parse) and must not
# be remembered as "no board" — it is retried on a later run.
HIT, MISS, ERROR = "hit", "miss", "error"


async def probe_status(http: httpx.AsyncClient, ats: str, slug: str) -> tuple[str, tuple[str, str, int] | None, str | None]:
    """(HIT|MISS|ERROR, (ats, slug, job_count) on a hit, the error on an ERROR)."""
    url = PROBES[ats].format(slug=slug)
    try:
        r = await http.get(url)
    except Exception as e:  # noqa: BLE001 — a network failure is retryable, not a miss
        log.warning("ats probe %s/%s failed: %s: %s", ats, slug, type(e).__name__, str(e)[:120])
        return ERROR, None, f"{type(e).__name__}"
    if r.status_code in (404, 410) or (r.status_code == 400 and ats == "lever"):
        return MISS, None, None
    if r.status_code == 429 or r.status_code >= 500:
        log.warning("ats probe %s/%s -> HTTP %s (retryable)", ats, slug, r.status_code)
        return ERROR, None, f"HTTP {r.status_code}"
    if r.status_code != 200:
        return MISS, None, None
    try:
        data = r.json()
    except Exception as e:  # noqa: BLE001
        log.warning("ats probe %s/%s: body is not JSON (%s)", ats, slug, type(e).__name__)
        return ERROR, None, "unparseable body"
    jobs = data if isinstance(data, list) else (data.get("jobs") or [])
    if not isinstance(jobs, list) or not jobs:
        return MISS, None, None
    return HIT, (ats, slug, len(jobs)), None


async def probe(http: httpx.AsyncClient, ats: str, slug: str) -> tuple[str, str, int] | None:
    """Return (ats, slug, job_count) if this board exists and has postings."""
    _, hit, _ = await probe_status(http, ats, slug)
    return hit


async def find_ats_status(
    http: httpx.AsyncClient,
    name: str,
    website: str | None = None,
    *,
    hint: str | None = None,
) -> tuple[tuple[str, str, int] | None, int]:
    """(the board with the most jobs or None, how many probes errored). A None with
    errors > 0 is "could not tell", not "no board"."""
    slugs = slug_candidates(name, website)
    atss = [hint] if hint in PROBES else list(PROBES)
    if hint in PROBES:
        atss += [a for a in PROBES if a != hint]
    results = await gather_limited(
        [probe_status(http, a, s) for a in atss for s in slugs], limit=6
    )
    hits = [r[1] for r in results if r and r[0] == HIT]
    errors = sum(1 for r in results if not r or r[0] == ERROR)
    if not hits:
        return None, errors
    return max(hits, key=lambda h: h[2]), errors


async def find_ats(
    http: httpx.AsyncClient,
    name: str,
    website: str | None = None,
    *,
    hint: str | None = None,
) -> tuple[str, str, int] | None:
    """Probe every (ats, slug) combination; return the board with the most jobs."""
    hit, _ = await find_ats_status(http, name, website, hint=hint)
    return hit
