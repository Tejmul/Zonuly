"""Every public way into a company — the Slack, the repo, the docs, the forum, the socials.

Tejmul, 2026-11-08: "mention it on the OpenHands page itself … while scraping companies, find
all the required things you can find, bro. It would be useful for us only."

The reason this is worth a stage of its own: when a company has **no open postings** — OpenHands
on 2026-11-08 had zero — the careers page is a dead end and the only ways in left are the ones a
human can walk into. Their Slack. Their repo's good-first-issues. Their Discord. Those are public,
they are stable, and they are where the maintainers actually are. An address is one way to reach a
person; a room they are already in is another, and it does not need verifying.

NAMING: this module is `ways_in`, not `channels`. `jobhunter/channels.py` already means
something else and has since the audit landed — it is the health of each *research* channel
(Exa, Jina, GitHub, DuckDuckGo), written by every call site. Two different things called
"channel" in one package is how a scraper ends up recording a Slack link as an outage.

WHAT IS COLLECTED, and from where:
    repo / sdk        the GitHub org, and its most-starred non-fork repos
    slack / discord   invite links, read out of the repo README and the site — resolved through
                      their redirect (go.openhands.dev/slack -> join.slack.com/...) so the row
                      holds the link that actually opens, not the shortener
    docs / blog       docs.* and /blog, where the engineering writing is
    forum             Discourse / GitHub Discussions
    social            X, LinkedIn, YouTube — last, because they are the least useful

NOTHING IS GUESSED. A link is stored only when it was read from a page we fetched, and the row
carries the page it came from. There is no "most companies use slack.company.com" rule here, for
the same reason there is no address pattern-filling: a link that 404s wastes his time, and a wrong
one sends him somewhere that is not them.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Optional

import httpx
from sqlmodel import Field, SQLModel, col, select

from jobhunter.db import Company, get_session, init_db, utcnow

log = logging.getLogger(__name__)

#: kind -> (label for the UI, how much it is worth when a company is not posting roles)
KINDS: dict[str, tuple[str, int]] = {
    "slack": ("Slack", 100),
    "discord": ("Discord", 95),
    "repo": ("Repository", 90),
    "good_first_issue": ("Good first issues", 88),
    "discussions": ("Discussions", 70),
    "forum": ("Forum", 68),
    "docs": ("Docs", 60),
    "blog": ("Blog", 55),
    "youtube": ("YouTube", 40),
    "x": ("X", 30),
    "linkedin": ("LinkedIn", 30),
}

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("slack", re.compile(r"https?://[^\s\"'<>)]*(?:join\.slack\.com/[^\s\"'<>)]+|"
                         r"[^\s\"'<>)]*slack[^\s\"'<>)]*)", re.I)),
    ("discord", re.compile(r"https?://(?:www\.)?(?:discord\.(?:gg|com)/[^\s\"'<>)]+|"
                           r"[^\s\"'<>)]*/discord)", re.I)),
    ("discussions", re.compile(r"https?://github\.com/[^\s\"'<>)/]+/[^\s\"'<>)/]+/discussions", re.I)),
    ("forum", re.compile(r"https?://(?:discuss|forum|community)\.[^\s\"'<>)]+", re.I)),
    ("docs", re.compile(r"https?://docs\.[^\s\"'<>)]+", re.I)),
    ("youtube", re.compile(r"https?://(?:www\.)?youtube\.com/(?:@|c/|channel/)[^\s\"'<>)]+", re.I)),
    ("x", re.compile(r"https?://(?:www\.)?(?:x|twitter)\.com/(?!intent|share|home)[A-Za-z0-9_]{2,30}/?(?=[\s\"'<>)]|$)", re.I)),
    ("linkedin", re.compile(r"https?://(?:www\.)?linkedin\.com/company/[^\s\"'<>)/]+", re.I)),
]

#: links that are never a way in — the vendor's own marketing, a badge, someone else's project
_JUNK = re.compile(r"shields\.io|badge|\.svg|\.png|\.jpg|slack\.com/(?:downloads|apps|help|intl)|"
                   r"discord\.com/(?:developers|terms|privacy)|linkedin\.com/company/linkedin|"
                   r"x\.com/(?:intent|home|share)|localhost|example\.com|schema\.org|w3\.org", re.I)


class CompanyChannel(SQLModel, table=True):
    """One public room, repo or page belonging to a company. The URL was read, never built."""

    __tablename__ = "company_channel"
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(index=True, foreign_key="company.id")
    kind: str = Field(index=True)                 # slack | discord | repo | docs | …
    url: str
    label: Optional[str] = None                   # what to call it on the page
    detail: Optional[str] = None                  # "92,948 stars", "8 open good first issues"
    evidence: Optional[str] = None                # the page we read it from
    rank: int = Field(default=50, index=True)     # KINDS order, so the useful ones sort first
    first_seen: datetime = Field(default_factory=utcnow)
    last_seen: datetime = Field(default_factory=utcnow)


#: kinds where every deep link is the same destination — one row per host is the useful shape.
#: Ten `docs.openhands.dev/...` rows say nothing eleven would not; the docs site is the way in.
_ROOT_ONLY = {"docs", "forum", "blog"}


def canonical(kind: str, url: str) -> str:
    """The form of a link worth storing: the site itself, for kinds where the page is incidental."""
    if kind not in _ROOT_ONLY:
        return url
    m = re.match(r"(https?://[^/\s]+)", url)
    return m.group(1) if m else url


def resolve(url: str, *, timeout: float = 10.0) -> str:
    """Follow a shortener to the link that actually opens.

    `go.openhands.dev/slack` is a 307 to a `join.slack.com` invite. Storing the shortener would
    work today and rot silently; storing what it resolves to is the thing he can click."""
    try:
        with httpx.Client(follow_redirects=True, timeout=timeout) as cl:
            r = cl.head(url)
            if r.status_code >= 400:
                r = cl.get(url)
            return str(r.url) or url
    except (httpx.HTTPError, OSError) as e:
        log.debug("resolve %s: %s", url, e)
        return url


def links_in(text: str, *, source: str) -> list[tuple[str, str, str]]:
    """(kind, url, evidence) for every way-in link in a page. Junk and duplicates dropped."""
    out: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for kind, pat in _PATTERNS:
        for m in pat.finditer(text or ""):
            url = m.group(0).rstrip(".,);:'\"")
            if _JUNK.search(url) or url.lower() in seen:
                continue
            seen.add(url.lower())
            out.append((kind, url, source))
    return out


def save(session, company_id: int, kind: str, url: str, *, evidence: str,
         label: str | None = None, detail: str | None = None) -> bool:
    """Upsert one channel. Returns True when it is new."""
    row = session.exec(
        select(CompanyChannel)
        .where(CompanyChannel.company_id == company_id)
        .where(CompanyChannel.kind == kind)
        .where(CompanyChannel.url == url)
    ).first()
    new = row is None
    if row is None:
        row = CompanyChannel(company_id=company_id, kind=kind, url=url)
    row.label = label or KINDS.get(kind, (kind, 50))[0]
    row.detail = detail or row.detail
    row.evidence = evidence
    row.rank = KINDS.get(kind, (kind, 50))[1]
    row.last_seen = utcnow()
    session.add(row)
    return new


# --------------------------------------------------------------- collecting

def _github(path: str, *, raw: bool = False) -> object | None:
    from jobhunter.research import backends

    tok = backends.github_token()
    hdr = {"Accept": "application/vnd.github.raw" if raw else "application/vnd.github+json"}
    if tok:
        hdr["Authorization"] = f"Bearer {tok}"
    try:
        with httpx.Client(timeout=20.0, follow_redirects=True) as cl:
            r = cl.get(f"https://api.github.com{path}", headers=hdr)
            if r.status_code >= 400:
                return None
            return r.text if raw else r.json()
    except (httpx.HTTPError, OSError, ValueError) as e:
        log.debug("github %s: %s", path, e)
        return None


def _page(url: str) -> str:
    try:
        with httpx.Client(timeout=20.0, follow_redirects=True,
                          headers={"User-Agent": "zonuly-channels/1.0"}) as cl:
            r = cl.get(url)
            return r.text if r.status_code < 400 else ""
    except (httpx.HTTPError, OSError) as e:
        log.debug("page %s: %s", url, e)
        return ""


def collect(company_id: int, *, read_site: bool = True) -> dict:
    """Find every public way into one company and store what was actually read.

    Order matters: the repo README first, because an open-source company puts its Slack invite
    there and keeps it current, and because the repo itself is a way in — a merged pull request
    is how you become someone a maintainer knows. The marketing site is read second and usually
    only adds socials."""
    init_db()
    stats = {"found": 0, "new": 0, "sources": []}
    with get_session() as s:
        co = s.get(Company, company_id)
        if co is None:
            raise KeyError(f"no company {company_id}")

        texts: list[tuple[str, str]] = []

        # 1. the GitHub org: its best repos, and the README of the biggest one
        if co.github_org:
            repos = _github(f"/orgs/{co.github_org}/repos?per_page=100&sort=updated")
            if isinstance(repos, list):
                live = sorted((r for r in repos if not r.get("fork")),
                              key=lambda r: -(r.get("stargazers_count") or 0))
                for r in live[:4]:
                    stars = r.get("stargazers_count") or 0
                    if save(s, company_id, "repo", r["html_url"],
                            label=r["name"],
                            detail=f"{stars:,}\u2605 \u00b7 {(r.get('language') or 'code')}"
                                   f" \u00b7 pushed {(r.get('pushed_at') or '')[:10]}",
                            evidence=f"github.com/orgs/{co.github_org}/repos"):
                        stats["new"] += 1
                    stats["found"] += 1
                if live:
                    top = live[0]
                    readme = _github(f"/repos/{top['full_name']}/readme", raw=True)
                    if isinstance(readme, str) and readme:
                        texts.append((readme, f"{top['full_name']} README"))
                        stats["sources"].append("repo README")
                    # good first issues — the cheapest door when nobody is posting roles
                    gfi = _github(f"/repos/{top['full_name']}/issues"
                                  "?labels=good%20first%20issue&state=open&per_page=30")
                    if isinstance(gfi, list) and gfi:
                        if save(s, company_id, "good_first_issue",
                                f"{top['html_url']}/issues?q=is%3Aopen+label%3A%22good+first+issue%22",
                                label="Good first issues",
                                detail=f"{len(gfi)} open in {top['name']}",
                                evidence=f"{top['full_name']} issues API"):
                            stats["new"] += 1
                        stats["found"] += 1

        # 2. their own site
        if read_site and co.website:
            html = _page(co.website)
            if html:
                texts.append((html, co.website))
                stats["sources"].append("website")

        for text, source in texts:
            for kind, url, _src in links_in(text, source=source):
                # a shortener is stored as what it opens, so the row stays clickable
                final = resolve(url) if ("slack" in kind and "join.slack.com" not in url) else url
                final = canonical(kind, final)
                if save(s, company_id, kind, final, evidence=f"read from {source}"):
                    stats["new"] += 1
                stats["found"] += 1
        s.commit()
    return stats


def for_company(company_id: int) -> list[dict]:
    """Every way in, best first — what the company page shows."""
    init_db()
    with get_session() as s:
        rows = s.exec(
            select(CompanyChannel)
            .where(CompanyChannel.company_id == company_id)
            .order_by(col(CompanyChannel.rank).desc())
        ).all()
        return [{"kind": r.kind, "label": r.label, "url": r.url, "detail": r.detail,
                 "evidence": r.evidence, "last_seen": r.last_seen} for r in rows]


__all__ = ["CompanyChannel", "KINDS", "links_in", "resolve", "save", "collect", "canonical",
           "for_company"]
