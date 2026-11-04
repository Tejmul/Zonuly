"""Communities — the underrated Slack and Discord groups where jobs are posted before they are listed.

Tejmul, 2026-11-03: "there are hell lot of oppurtunities i see in slack channels and communities
scrap them too we have huge advantage if we done so … so we need underated slack channels to do
that."

WHAT THIS CAN AND CANNOT DO, said plainly, because the difference decides the design:

  CAN     read the PUBLIC community directories (The Hive Index and the like). They publish the
          community's name, platform, topic, member count and join link. That is enough to answer
          "which rooms should I be in", which is the question worth answering first.
  CANNOT  read messages inside a Slack workspace. A workspace is private: posts are reachable only
          with an account that has joined it, and automating a joined personal account is both
          against Slack's terms and against this project's own rule (no personal accounts in
          scraping — the ban risk is his real LinkedIn, GitHub and now Slack identities).

So this module finds and ranks the rooms; joining is his, by hand, with the link it gives him. Once
he is in one and a channel has a PUBLIC job feed or archive, that feed can be scraped like any other
source — add it to `jobhunter/scrapers/`, not here.

UNDERRATED is the ranking, not a filter: a 300-member room where the founder posts a role herself is
worth more to one fresher than a 100,000-member room where every post has 400 applicants within the
hour. `score()` prefers the small and the specific, and says why in words.

Layering: db + research only. No model, no outreach, no addresses — nothing in here ever touches a
person's contact details.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel, col, select

from jobhunter.db import get_session, get_setting, init_db, set_setting, utcnow

log = logging.getLogger(__name__)


class Community(SQLModel, table=True):
    """One room — a Slack workspace, a Discord server, a forum — that posts jobs.

    Everything here is read off a public directory page and quoted; nothing is inferred. `url` is
    the directory's page for it, `join_url` the community's own link when the directory gives one.
    """

    __tablename__ = "community"
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    key: str = Field(index=True, unique=True)       # slug, for dedup across directories
    platform: Optional[str] = Field(default=None, index=True)    # slack | discord | circle | forum
    topic: Optional[str] = Field(default=None, index=True)       # remote | tech | startups | ai …
    members: Optional[int] = Field(default=None, index=True)
    description: Optional[str] = None
    url: Optional[str] = None                        # the directory page we read it from
    join_url: Optional[str] = None                   # where he joins, when the directory says
    has_jobs: bool = Field(default=False, index=True)    # the directory says it has a job board
    source: Optional[str] = Field(default=None, index=True)
    score: Optional[float] = Field(default=None, index=True)
    score_why: Optional[str] = None
    evidence: Optional[str] = None                   # the directory's own sentence
    joined: bool = Field(default=False, index=True)  # he has joined — set by hand, never by code
    first_seen: datetime = Field(default_factory=utcnow)
    last_seen: datetime = Field(default_factory=utcnow)


#: the public directories. Each is a listing page; nothing behind a login.
HIVE = "https://thehiveindex.com"
#: topic × platform pages worth walking, software-hiring first
TOPICS = ["artificial-intelligence", "machine-learning", "data-science", "devops", "programming",
          "developers", "tech", "startups", "remote", "python", "javascript", "saas", "product",
          "cybersecurity", "web3", "career", "freelance", "design", "data", "cloud", "open-source",
          "software-engineering", "analytics", "no-code", "founders", "indie-hackers"]
PLATFORMS = ["slack", "discord"]

_CURSOR = "communities:cursor"
_COMMUNITY_LINK = re.compile(r"\[([^\]\[]{2,70})\]\((https://thehiveindex\.com/communities/[^)\s]+)\)")
#: "4K members", "roughly 10k members", "1.2M members", "775 members" — and NOT a bare number that
#: merely happens to sit near the word. 2026-11-03: the loose form read "50" off a listing page for
#: a community with ~4,000 members, and a wrong size drives the whole score, so this one is strict.
_MEMBERS = re.compile(r"(?i)\b(?:roughly\s+|about\s+|over\s+|~)?(\d[\d,]*(?:\.\d+)?)\s*([km])?\s*\+?\s*members\b")


def members_from(text: str) -> int | None:
    """The member count a page states, in its own words, or None. Never a guess: a number that is
    not followed by the word "members" is not a member count."""
    m = _MEMBERS.search(text or "")
    if not m:
        return None
    try:
        n = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    mult = {"k": 1_000, "m": 1_000_000}.get((m.group(2) or "").lower(), 1)
    n = int(n * mult)
    return n if 1 <= n <= 20_000_000 else None
_JOBS_WORD = re.compile(r"(?i)\b(job board|jobs channel|#jobs|job posts|hiring channel|job listings|"
                        r"opportunities channel|gigs)\b")

#: a room this big is not underrated — his application is one of hundreds within the hour
_HUGE = 50_000
#: and one this small may be dead
_TINY = 50


def slug_of(url: str) -> str:
    m = re.search(r"/communities/([^/?#]+)", url or "")
    return (m.group(1) if m else (url or "")).strip("/").lower()


@dataclass
class Found:
    name: str
    key: str
    url: str
    platform: str | None = None
    topic: str | None = None
    members: int | None = None
    description: str | None = None
    join_url: str | None = None
    has_jobs: bool = False
    evidence: str | None = None


@dataclass
class Stats:
    pages: int = 0
    seen: int = 0
    added: int = 0
    updated: int = 0
    skipped: dict = field(default_factory=dict)
    seconds: int = 0

    def skip(self, why: str) -> None:
        self.skipped[why] = self.skipped.get(why, 0) + 1


def score(c: Found | Community) -> tuple[float, str]:
    """How much is this room worth to ONE fresher looking for a referral? Small and specific beats
    big and famous, because the whole advantage of a community post is that few people see it.

    Returns (0..100, the sentence that explains it). Never a silent number."""
    n = c.members
    why: list[str] = []
    s = 50.0
    if n is None:
        why.append("size unknown")
    elif n >= _HUGE:
        s -= 30
        why.append(f"{n:,} members — a post there is seen by everyone, so the edge is gone")
    elif n >= 10_000:
        s -= 10
        why.append(f"{n:,} members — large, still worth being in")
    elif n >= 500:
        s += 20
        why.append(f"{n:,} members — small enough that a post is actually read")
    elif n >= _TINY:
        s += 12
        why.append(f"{n:,} members — tiny, high signal if it is alive")
    else:
        s -= 15
        why.append(f"only {n} members — may be dead")
    if c.has_jobs:
        s += 25
        why.append("the directory says it has a job board or jobs channel")
    else:
        why.append("no job channel named — worth a look, not a priority")
    t = (c.topic or "").lower()
    if t in ("remote", "tech", "startups", "developers", "programming", "artificial-intelligence",
             "machine-learning", "devops", "data-science"):
        s += 10
        why.append(f"on topic for software hiring ({t})")
    return max(0.0, min(100.0, s)), "; ".join(why)


def parse_hive(text: str, *, topic: str, platform: str, page_url: str) -> list[Found]:
    """Communities named on one Hive Index topic × platform page.

    The page is markdown from the reader: a community is a link to /communities/<slug>/, and its
    member count and description sit in the lines around it. Anything not stated is left None —
    a missing member count is never guessed at."""
    out: dict[str, Found] = {}
    for m in _COMMUNITY_LINK.finditer(text):
        name, url = m.group(1).strip(), m.group(2).strip()
        key = slug_of(url)
        if not key or name.lower() in ("", "communities", "add community"):
            continue
        window = text[m.end():m.end() + 260]
        members = members_from(window)
        sentence = re.sub(r"\s+", " ", window[:300]).strip()
        prev = out.get(key)
        found = Found(name=name, key=key, url=url, platform=platform, topic=topic,
                      members=members if members is not None else (prev.members if prev else None),
                      description=sentence[:400] or None,
                      has_jobs=bool(_JOBS_WORD.search(window)) or (prev.has_jobs if prev else False),
                      evidence=f"{page_url}: {sentence[:200]}")
        out[key] = found
    return list(out.values())


def _save(session, f: Found, st: Stats) -> None:
    row = session.exec(select(Community).where(Community.key == f.key)).first()
    sc, why = score(f)
    if row is None:
        row = Community(name=f.name, key=f.key, platform=f.platform, topic=f.topic,
                        members=f.members, description=f.description, url=f.url,
                        join_url=f.join_url, has_jobs=f.has_jobs, source="hive-index",
                        score=sc, score_why=why, evidence=f.evidence)
        st.added += 1
    else:
        # a later page may know the member count the first one did not; never overwrite with None
        row.members = f.members if f.members is not None else row.members
        row.has_jobs = row.has_jobs or f.has_jobs
        row.description = row.description or f.description
        row.topic = row.topic or f.topic
        row.platform = row.platform or f.platform
        row.score, row.score_why = score(row)
        row.last_seen = utcnow()
        st.updated += 1
    session.add(row)


#: the real join link is behind the directory's redirect, or beside it with a ref parameter
_REDIRECT = re.compile(r"thehiveindex\.com/redirect/\?url=([^&)\s]+)")
_DIRECT = re.compile(r"\]\((https?://(?!thehiveindex|www\.altium|images\.unsplash|twitter\.com|"
                     r"www\.reddit\.com|docs\.google\.com)[^)\s]+)\)")


def join_link(text: str) -> str | None:
    """Where he actually joins, read off the directory's own page. None when the page does not say
    — a join link is never constructed from the community's name."""
    from urllib.parse import unquote
    m = _REDIRECT.search(text or "")
    if m:
        u = unquote(m.group(1))
        if u.startswith("http"):
            return u.split("?ref=")[0][:500]
    m = _DIRECT.search(text or "")
    if m:
        return m.group(1).split("?ref=")[0][:500]
    return None


def about(text: str) -> str | None:
    """The community's own description, the paragraph under "## About"."""
    m = re.search(r"(?s)##\s*About[^\n]*\n(.{40,900}?)(?:\n##|\Z)", text or "")
    if not m:
        return None
    return re.sub(r"\s+", " ", m.group(1)).strip()[:600] or None


def enrich(limit: int = 40, *, budget_s: int = 900, only_missing: bool = True) -> dict:
    """Read each community's own directory page for the JOIN LINK and its real description.

    2026-11-03: the listing pages name the rooms but not how to get in, so all 90 rows had no
    join_url and the list was unusable — he cannot join a name. The detail page carries the link
    behind the directory's redirect; this unwraps it and stores it. A page that does not give one
    leaves join_url null rather than inventing a slack.com URL."""
    from jobhunter.research import web

    init_db()
    st = {"read": 0, "join_links": 0, "described": 0, "jobs_found": 0, "skipped": 0}
    t0 = time.time()
    with get_session() as s:
        q = select(Community)
        if only_missing:
            q = q.where(col(Community.join_url).is_(None))
        rows = [(c.id, c.url) for c in s.exec(q.order_by(col(Community.score).desc().nulls_last())).all()]
    for cid, url in rows[:limit]:
        if time.time() - t0 > budget_s:
            break
        if not url:
            st["skipped"] += 1
            continue
        try:
            page = web.read(url, max_chars=20000)
        except Exception:                           # noqa: BLE001
            st["skipped"] += 1
            continue
        text = page.get("text") or ""
        if len(text) < 500:                         # a 404 comes back almost empty
            st["skipped"] += 1
            continue
        st["read"] += 1
        link, desc = join_link(text), about(text)
        n = members_from(text)
        with get_session() as s:
            c = s.get(Community, cid)
            if c is None:
                continue
            if link and not c.join_url:
                c.join_url, st["join_links"] = link, st["join_links"] + 1
            if desc:
                c.description, st["described"] = desc, st["described"] + 1
            if n is not None:
                c.members = n
            if not c.has_jobs and _JOBS_WORD.search(text):
                c.has_jobs, st["jobs_found"] = True, st["jobs_found"] + 1
            c.score, c.score_why = score(c)
            c.last_seen = utcnow()
            s.add(c)
            s.commit()
    st["seconds"] = round(time.time() - t0)
    log.info("communities enrich: %s", st)
    return st


def harvest(pages: int = 8, *, budget_s: int = 600) -> dict:
    """Walk the next `pages` topic × platform directory pages and store what they name."""
    from jobhunter.research import web

    init_db()
    st = Stats()
    t0 = time.time()
    pool = [(t, p) for t in TOPICS for p in PLATFORMS]
    with get_session() as s:
        try:
            cur = int(get_setting(s, _CURSOR, "0") or 0)
        except ValueError:
            cur = 0

    for i in range(pages):
        if time.time() - t0 > budget_s:
            st.skip("out of time")
            break
        topic, platform = pool[(cur + i) % len(pool)]
        url = f"{HIVE}/topics/{topic}/platform/{platform}/"
        try:
            page = web.read(url, max_chars=60000)
        except Exception as e:                      # noqa: BLE001 — one dead page is not the end
            st.skip(f"unreadable: {type(e).__name__}")
            continue
        text = page.get("text") or ""
        if not text:
            st.skip("empty page")
            continue
        st.pages += 1
        found = parse_hive(text, topic=topic, platform=platform, page_url=url)
        st.seen += len(found)
        with get_session() as s:
            for f in found:
                _save(s, f, st)
            s.commit()

    with get_session() as s:
        set_setting(s, _CURSOR, str((cur + pages) % len(pool)))
        s.commit()
    st.seconds = round(time.time() - t0)
    out = {"pages": st.pages, "seen": st.seen, "added": st.added, "updated": st.updated,
           "skipped": st.skipped, "seconds": st.seconds}
    log.info("communities harvest: %s", out)
    return out


def best(limit: int = 40, *, platform: str | None = None, with_jobs: bool = True) -> list[dict]:
    """The rooms worth joining, best first — small, on topic, with a job channel."""
    init_db()
    with get_session() as s:
        q = select(Community)
        if platform:
            q = q.where(Community.platform == platform)
        if with_jobs:
            q = q.where(col(Community.has_jobs).is_(True))
        rows = s.exec(q.order_by(col(Community.score).desc().nulls_last())).all()
    return [{"name": c.name, "platform": c.platform, "topic": c.topic, "members": c.members,
             "has_jobs": c.has_jobs, "score": round(c.score or 0), "why": c.score_why,
             "url": c.url, "join_url": c.join_url, "joined": c.joined}
            for c in rows[:limit]]


__all__ = ["Community", "harvest", "enrich", "best", "score", "parse_hive", "slug_of",
           "members_from", "join_link"]
