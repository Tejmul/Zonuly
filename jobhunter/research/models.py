"""The shapes the research layer returns.

Deliberately dumb dataclasses. Nothing here knows about jobs, scoring or the
knowledge graph — the acquisition layer hands back plain records and the rest of
JobHunter decides what they mean.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime


def _clean(d: dict) -> dict:
    return {k: v for k, v in d.items() if v not in (None, "", [], {})}


@dataclass
class SearchResult:
    """One hit from a web search backend."""

    title: str
    url: str
    snippet: str | None = None
    published: str | None = None
    author: str | None = None
    source: str = "web"          # which backend produced it
    score: float | None = None

    def as_dict(self) -> dict:
        return _clean(asdict(self))


@dataclass
class Page:
    """A fetched page, reduced to readable text."""

    url: str
    title: str | None = None
    text: str = ""
    backend: str = ""
    fetched_at: datetime | None = None
    truncated: bool = False

    def as_dict(self) -> dict:
        d = asdict(self)
        d["fetched_at"] = self.fetched_at.isoformat() if self.fetched_at else None
        d["chars"] = len(self.text)
        return _clean(d)


@dataclass
class RepoHit:
    full_name: str
    url: str
    description: str | None = None
    language: str | None = None
    stars: int = 0
    pushed_at: str | None = None
    owner: str | None = None

    def as_dict(self) -> dict:
        return _clean(asdict(self))


@dataclass
class RedditPost:
    title: str
    url: str
    subreddit: str | None = None
    author: str | None = None
    score: int = 0
    num_comments: int = 0
    created: str | None = None
    text: str | None = None

    def as_dict(self) -> dict:
        return _clean(asdict(self))


@dataclass
class Video:
    title: str
    url: str
    channel: str | None = None
    video_id: str | None = None
    duration: int | None = None
    views: int | None = None
    transcript: str | None = None

    def as_dict(self) -> dict:
        return _clean(asdict(self))


@dataclass
class FundingSignal:
    """What a news snippet says about money raised. Extracted, never invented."""

    stage: str | None = None          # seed | series a | ... | None when unstated
    amount_raw: str | None = None     # "$50 million", as written
    amount_usd_m: float | None = None # normalized, best effort
    investors: list[str] = field(default_factory=list)
    announced: str | None = None
    evidence_url: str | None = None
    evidence_quote: str | None = None

    def as_dict(self) -> dict:
        return _clean(asdict(self))


@dataclass
class CompanyResearch:
    """Everything the acquisition layer could find about one company.

    `confidence` follows the house rule: nothing is stated more firmly than the
    evidence supports. Fields with no evidence stay None rather than being guessed.
    """

    name: str
    website: str | None = None
    domain: str | None = None
    one_liner: str | None = None
    location: str | None = None
    funding: FundingSignal | None = None
    github_org: str | None = None
    repos: list[RepoHit] = field(default_factory=list)
    careers_url: str | None = None
    hiring_signals: list[str] = field(default_factory=list)
    open_roles: list[str] = field(default_factory=list)
    reddit: list[RedditPost] = field(default_factory=list)
    videos: list[Video] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    backends_used: list[str] = field(default_factory=list)
    confidence: str = "scraped"   # verified | scraped | inferred
    researched_at: datetime | None = None

    def as_dict(self) -> dict:
        d = asdict(self)
        d["funding"] = self.funding.as_dict() if self.funding else None
        d["repos"] = [r.as_dict() for r in self.repos]
        d["reddit"] = [p.as_dict() for p in self.reddit]
        d["videos"] = [v.as_dict() for v in self.videos]
        d["researched_at"] = self.researched_at.isoformat() if self.researched_at else None
        return _clean(d)


__all__ = [
    "SearchResult",
    "Page",
    "RepoHit",
    "RedditPost",
    "Video",
    "FundingSignal",
    "CompanyResearch",
]
