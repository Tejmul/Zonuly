"""Where a person can be reached on X — a DM route for the people we cannot email.

Tejmul, 2026-09-19: "we are thinking of finding and sending cold DMs on X". Finding is
this module; sending is not — a DM leaves through the same review queue as an email,
and only a human presses send (constraint:human-review). Two sources, both public:

  1. their own GitHub profile's `twitter_username` (github_miner reads it for free), and
  2. X's own search through the throwaway read-only session (research/x_search.py):
     posts matching "<name>" <company>, and the author whose display name is the
     person's name is the person. A same-name stranger at another company fails the
     company check. Web search is no use here — x.com is not in Exa's index.

Nothing is invented: a handle is stored only when one of those two said so, with the
post it was read from. X search is capped per day (research.x_daily_searches), so a
company gets its best few people looked up, founders and senior engineers first.
"""

from __future__ import annotations

import logging
import re

from sqlmodel import col, select

from jobhunter.db import Company, Contact, get_session, init_db

log = logging.getLogger(__name__)

_PROFILE = re.compile(r"^https?://(?:www\.)?(?:x|twitter)\.com/([A-Za-z0-9_]{1,15})/?$", re.I)
_RESERVED = {"home", "search", "explore", "i", "intent", "share", "hashtag", "login", "settings", "messages"}


def _name_stems(name: str) -> list[str]:
    return [re.sub(r"[^a-z]", "", p.lower()) for p in name.split() if len(p) > 2]


def _stem(text: str | None) -> str:
    return re.sub(r"[^a-z]", "", (text or "").lower())


def find_for_contact(contact_id: int) -> dict:
    """One person: one X search; accept an author only if their display name is the
    person's name and the post or bio places them at the company."""
    from jobhunter.research import x_search

    init_db()
    with get_session() as session:
        p = session.get(Contact, contact_id)
        if p is None:
            return {"error": f"no contact {contact_id}"}
        if p.x_handle:
            return {"contact": p.name, "handle": p.x_handle, "via": "already known"}
        if not p.name:
            return {"contact": None, "handle": None, "why": "no name to search for"}
        company = session.get(Company, p.company_id)
        name, cname = p.name, company.name if company else ""

    stems = _name_stems(name)
    if len(stems) < 2:
        return {"contact": name, "handle": None, "why": "need a first and last name"}
    if x_search.budget()["left"] <= 0:
        return {"contact": name, "handle": None, "why": "x: daily search cap reached — resumes tomorrow"}

    cstem = _stem(cname)[:6]
    res = x_search.search(f'"{name}" {cname}', limit=20)
    if res.get("error"):
        return {"contact": name, "handle": None, "why": res["error"]}
    by_author: dict[str, dict] = {}
    for post in res.get("posts") or []:
        by_author.setdefault(post["handle"], {"name": post.get("name"), "texts": []})["texts"].append(post.get("text") or "")
    for handle, info in by_author.items():
        if handle.lower() in _RESERVED or not all(s in _stem(info["name"]) for s in stems[:2]):
            continue
        # their own words, or the display name itself, have to place them at the company
        if cstem and cstem not in _stem(" ".join(info["texts"]) + (info["name"] or "")):
            continue
        with get_session() as session:
            p = session.get(Contact, contact_id)
            p.x_handle = handle
            p.research_notes = f"{p.research_notes or ''}\nX handle from x.com/{handle} (own post names {cname})".strip()
            session.add(p)
            session.commit()
        return {"contact": name, "handle": handle, "via": f"x.com/{handle}"}
    return {"contact": name, "handle": None, "why": "no post author with that name at that company"}


def find_for_company(company_id: int, *, limit: int = 5) -> dict:
    """The people at one company most worth a DM — best referral rank first, named,
    without a handle yet — up to `limit` searches."""
    init_db()
    with get_session() as session:
        rows = session.exec(
            select(Contact).where(Contact.company_id == company_id, col(Contact.x_handle).is_(None),
                                  col(Contact.name).is_not(None))
        ).all()
        rows = sorted(rows, key=lambda p: ((p.referral_rank or 9), p.email is not None))[:limit]
        ids = [(p.id, p.name) for p in rows]
        already = session.exec(select(Contact.id).where(Contact.company_id == company_id,
                                                        col(Contact.x_handle).is_not(None))).all()
    out = {"searched": 0, "found": len(already), "new": 0, "handles": []}
    for cid, _ in ids:
        r = find_for_contact(cid)
        out["searched"] += 1
        if r.get("handle") and r.get("via") != "already known":
            out["new"] += 1
            out["handles"].append({"name": r["contact"], "handle": r["handle"]})
    out["found"] += out["new"]
    return out


def backfill_github(company_ids: list[int] | None = None, *, limit: int = 400) -> dict:
    """X handles for contacts found on GitHub before their profile's `twitter_username`
    was read — one profile call each, inside GitHub's hourly budget."""
    import asyncio

    from jobhunter.contacts import github_miner
    from jobhunter.scrapers.base import make_client

    init_db()
    with get_session() as session:
        stmt = select(Contact).where(Contact.source == "github", col(Contact.x_handle).is_(None),
                                     col(Contact.github).is_not(None))
        if company_ids:
            stmt = stmt.where(col(Contact.company_id).in_(company_ids))
        rows = session.exec(stmt).all()
        todo = [(p.id, p.github.rstrip("/").split("/")[-1]) for p in rows][:limit]

    async def run() -> list[tuple[int, str | None]]:
        async with make_client() as http:
            out = []
            for cid, login in todo:
                prof = await github_miner.user_profile(http, login)
                out.append((cid, prof.twitter if prof else None))
            return out

    found = 0
    with get_session() as session:
        for cid, handle in asyncio.run(run()) if todo else []:
            if not handle:
                continue
            p = session.get(Contact, cid)
            p.x_handle = handle
            p.research_notes = f"{p.research_notes or ''}\nX handle from their GitHub profile".strip()
            session.add(p)
            found += 1
        session.commit()
    log.info("x_handles: github backfill %d checked, %d handles", len(todo), found)
    return {"checked": len(todo), "handles": found}
