"""HTTP surface for the research layer — `/api/research/*`.

A separate APIRouter so `api.py` only gains an include_router line, and so the
layer can be mounted, moved or dropped without touching the rest of the backend.

These are `def` (not `async def`) handlers on purpose: every call underneath is a
blocking subprocess or a sync HTTP fetch, and FastAPI runs sync handlers in its
threadpool, so a 30-second Exa call cannot stall the event loop the dashboard
depends on.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Body, Query

from jobhunter import research

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/research", tags=["research"])


@router.get("/doctor")
def research_doctor() -> dict:
    """Which channels are live, which need setup, and what the fix is."""
    return research.doctor()


@router.get("/search")
def search(
    q: str = Query(..., min_length=2, description="Describe the ideal page, not keywords"),
    limit: int = Query(8, ge=1, le=25),
    fresh: bool = Query(False, description="Bypass the cache"),
) -> dict:
    return research.search_web(q, limit=limit, fresh=fresh)


@router.get("/x")
def x_posts(
    q: str = Query(..., min_length=2, description="What the ideal post says, e.g. 'we are hiring a founding AI engineer, remote'"),
    limit: int = Query(20, ge=1, le=50),
    days: int = Query(30, ge=1, le=365, description="Reported back only — the search engine gives no dates yet"),
    fresh: bool = Query(False, description="Bypass the cache"),
) -> dict:
    """Posts on X via a site:x.com web search — no X account, no cookies, no X API. Records only."""
    return research.search_x(q, limit=limit, days=days, fresh=fresh)


@router.get("/read")
def read(
    url: str = Query(..., description="Public http(s) URL"),
    max_chars: int = Query(12000, ge=500, le=60000),
    fresh: bool = False,
) -> dict:
    return research.read_page(url, max_chars=max_chars, fresh=fresh)


@router.get("/github")
def github(q: str = Query(..., min_length=2), limit: int = Query(10, ge=1, le=50), fresh: bool = False) -> dict:
    return research.search_github(q, limit=limit, fresh=fresh)


@router.get("/reddit")
def reddit(
    q: str = Query(..., min_length=2),
    limit: int = Query(10, ge=1, le=50),
    subreddit: str | None = None,
    fresh: bool = False,
) -> dict:
    return research.search_reddit(q, limit=limit, subreddit=subreddit, fresh=fresh)


@router.get("/youtube")
def youtube(q: str = Query(..., min_length=2), limit: int = Query(5, ge=1, le=25), fresh: bool = False) -> dict:
    return research.search_youtube(q, limit=limit, fresh=fresh)


@router.get("/youtube/transcript")
def youtube_transcript(url: str = Query(...), lang: str = "en") -> dict:
    return research.youtube_transcript(url, lang=lang)


@router.get("/company")
def company(
    name: str = Query(..., min_length=1),
    depth: str = Query("standard", pattern="^(quick|standard|deep)$"),
    website: str | None = None,
    fresh: bool = False,
) -> dict:
    return research.research_company(name, website=website, depth=depth, fresh=fresh)


@router.post("/startups")
def startups(
    topic: str = Body("AI"),
    regions: list[str] | None = Body(None),
    stages: list[str] | None = Body(None),
    limit: int = Body(10),
    enrich: int = Body(5),
    fresh: bool = Body(False),
) -> dict:
    """Recently funded startups with funding and hiring signals.

    POST rather than GET because the region/stage lists are the point of the call
    and a query string is the wrong shape for them.
    """
    return research.find_startups(
        topic=topic,
        regions=regions,
        stages=stages,
        limit=max(1, min(limit, 40)),
        enrich=max(0, min(enrich, 20)),
        fresh=fresh,
    )


@router.post("/cache/purge")
def purge(older_than_hours: int | None = Body(None, embed=True)) -> dict:
    return {"deleted": research.cache.purge(older_than_hours), "stats": research.cache.stats()}
