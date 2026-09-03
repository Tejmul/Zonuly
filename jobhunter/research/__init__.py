"""Web research — JobHunter's data-acquisition layer.

One import for everything the agent needs to look at the outside world:

    from jobhunter import research

    research.search_web("seed-stage AI infra startups hiring in London")
    research.read_page("https://example.com/careers")
    research.search_github("llm evaluation framework")
    research.search_reddit("what is it like working at <company>")
    research.search_youtube("<founder> interview")
    research.research_company("Acme AI", depth="deep")
    research.find_startups(topic="AI", regions=["United States", "United Kingdom"])
    research.doctor()

Backends come from Agent Reach (github.com/Panniantong/Agent-Reach) — mcporter/Exa,
Jina Reader, gh, yt-dlp, rdt. Agent Reach installs and health-checks them; this
package routes to them and turns their output into records. It is not vendored:
Agent Reach exposes no read/search API, its own skill tells agents to call the
upstream tools directly, so a copy would duplicate installers and gain nothing.

Layering: this sits beside `scrapers` and `contacts`. It may use `db` (for its own
cache table), `CONFIG` and `scrapers.base`; it must never import `matcher`,
`pipeline`, `kg`, `outreach` or `api`. Acquisition here, judgement there.

Every entry point returns a plain dict and never raises for a dead backend — an
`error` key plus a `hint` is the truthful answer when a channel is not set up.
See `system docs/AGENT-REACH-INTEGRATION.md` for setup.
"""

from __future__ import annotations

from jobhunter.research import backends, cache, web
from jobhunter.research.agent import company as research_company
from jobhunter.research.agent import (
    detect_ats,
    extract_company_name,
    extract_funding,
    hiring_signals,
)
from jobhunter.research.agent import startups as find_startups
from jobhunter.research.github import find_org as github_org
from jobhunter.research.github import org_repos as github_org_repos
from jobhunter.research.github import search_repos as search_github
from jobhunter.research.github import search_users as search_github_users
from jobhunter.research.models import (
    CompanyResearch,
    FundingSignal,
    Page,
    RedditPost,
    RepoHit,
    SearchResult,
    Video,
)
from jobhunter.research.reddit import search as search_reddit
from jobhunter.research.reddit import status as reddit_status
from jobhunter.research.web import read as read_page
from jobhunter.research.web import search as search_web
from jobhunter.research.web import search_x
from jobhunter.research.youtube import search as search_youtube
from jobhunter.research.youtube import transcript as youtube_transcript


def doctor() -> dict:
    """Which research channels work on this machine right now, and how to fix the rest."""
    report = backends.doctor()
    report["reddit"] = reddit_status()
    report["cache"] = cache.stats()
    report["exa_budget"] = web.exa_budget()
    return report


__all__ = [
    # channels
    "search_web",
    "search_x",
    "read_page",
    "search_github",
    "search_github_users",
    "github_org",
    "github_org_repos",
    "search_reddit",
    "reddit_status",
    "search_youtube",
    "youtube_transcript",
    # composed tasks
    "research_company",
    "find_startups",
    # extraction helpers (pure functions, safe to reuse)
    "extract_funding",
    "extract_company_name",
    "hiring_signals",
    "detect_ats",
    # infrastructure
    "doctor",
    "backends",
    "cache",
    # records
    "SearchResult",
    "Page",
    "RepoHit",
    "RedditPost",
    "Video",
    "FundingSignal",
    "CompanyResearch",
]
