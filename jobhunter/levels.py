"""levels.fyi — real, self-reported compensation per company, read through scrape.do.

The pay bottleneck: most postings never state a number, so ~1,200 companies sit at
Pay Power "unknown". levels.fyi carries crowd-sourced total-comp for a company's
engineering levels; its pages block a datacenter fetch, which is exactly what scrape.do
is for. We read the company's page, take the entry-level / lowest software-engineer
total comp as the fresher figure, convert to INR LPA, and store it with the URL as
evidence. Nothing is invented: a company with no levels.fyi page stays "unknown".

Layering: reads research.web (scrape.do) + db + targeting; writes company pay fields.
"""

from __future__ import annotations

import json
import logging
import re
from urllib.parse import quote

from sqlmodel import col, select

from jobhunter import CONFIG
from jobhunter.db import Company, get_session, init_db, utcnow
from jobhunter.research import web

log = logging.getLogger(__name__)

_S = CONFIG.get("search") or {}
USD_TO_INR = float(_S.get("usd_to_inr", 88))
_SLUG = re.compile(r"[^a-z0-9]+")

# levels.fyi ships its data as JSON in a Next.js __NEXT_DATA__ blob; the numbers we want
# are "totalCompensation"/"basePay" with a level title. We read the JSON when present and
# fall back to the visible "$NNN,NNN" figures near "Entry Level" / "SWE" in the text.
_NEXT = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
_MONEY = re.compile(r"\$\s?(\d{2,3}(?:,\d{3})+|\d{3,7})")
_ENTRY = re.compile(r"(entry|new grad|l3|ic1|e3|swe i\b|junior|associate)", re.I)


def _slug(name: str) -> str:
    return _SLUG.sub("-", (name or "").lower()).strip("-")


def _walk_comp(obj, out: list):
    """Collect {level, total_usd} from levels.fyi's nested JSON."""
    if isinstance(obj, dict):
        tc = obj.get("totalCompensation") or obj.get("totalComp") or obj.get("compensation")
        title = obj.get("levelTitle") or obj.get("level") or obj.get("title")
        if isinstance(tc, (int, float)) and tc > 10000:
            out.append({"level": str(title or "")[:60], "total_usd": float(tc)})
        for v in obj.values():
            _walk_comp(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _walk_comp(v, out)


def _fresher_usd(html: str) -> tuple[float | None, str | None]:
    """Lowest / entry-level total comp in USD, and the words it came from."""
    comps: list = []
    m = _NEXT.search(html or "")
    if m:
        try:
            _walk_comp(json.loads(m.group(1)), comps)
        except json.JSONDecodeError:
            pass
    if comps:
        entry = [c for c in comps if _ENTRY.search(c["level"])]
        pick = min(entry or comps, key=lambda c: c["total_usd"])
        return pick["total_usd"], f"levels.fyi {pick['level'] or 'lowest level'}: ${pick['total_usd']:,.0f} total comp"
    # text fallback: the smallest plausible salary figure on the page
    figures = [float(x.replace(",", "")) for x in _MONEY.findall(web.html_to_text(html, limit=20000) or "")]
    figures = [f for f in figures if 40_000 <= f <= 900_000]
    if figures:
        v = min(figures)
        return v, f"levels.fyi lowest listed total comp: ${v:,.0f}"
    return None, None


def lookup(company_id: int, *, fresh: bool = False) -> dict:
    """Read one company's levels.fyi page and write back a fresher PPO in INR LPA."""
    init_db()
    with get_session() as session:
        c = session.get(Company, company_id)
        if c is None:
            return {"error": f"no company {company_id}"}
        name = c.name
        if c.ppo_lpa and (c.ppo_evidence or "").startswith("levels.fyi"):
            return {"company": name, "skip": "already from levels.fyi", "ppo_lpa": c.ppo_lpa}
    url = f"https://www.levels.fyi/companies/{_slug(name)}/salaries/software-engineer"
    html = web.fetch_html_scrapedo(url, timeout=45)
    if not html:
        return {"company": name, "found": False, "url": url}
    # the page always carries generic "page not found" text in its search chrome, so the
    # real signal is whether a comp figure parsed — a company not on levels.fyi yields none
    usd, ev = _fresher_usd(html)
    if not usd:
        return {"company": name, "found": False, "reason": "no comp figure — not on levels.fyi", "url": url}
    lpa = round(usd * USD_TO_INR / 100_000, 1)
    with get_session() as session:
        c = session.get(Company, company_id)
        # only fill it in if we do not already have a *stated* figure from a posting
        if not c.ppo_lpa or (c.ppo_evidence or "").startswith("levels.fyi"):
            c.ppo_lpa = lpa
            c.ppo_evidence = f"{ev} ({url})"
            session.add(c)
            session.commit()
            from jobhunter.targeting import grade_company

            c = session.get(Company, company_id)
            g = grade_company(session, c)
            session.commit()
            c = session.get(Company, company_id)
            return {"company": name, "found": True, "usd": usd, "ppo_lpa": lpa,
                    "pay_power": c.pay_power_band, "tier": g.tier}
    return {"company": name, "found": True, "usd": usd, "ppo_lpa": lpa, "kept_stated": True}


def lookup_pending(limit: int = 40, *, shard: int | None = None, shards: int | None = None) -> dict:
    """Companies with roles and no pay figure, best tier first, inside the scrape.do budget."""
    from jobhunter.db import Job
    from jobhunter.targeting import TIER_ORDER

    init_db()
    with get_session() as session:
        rows = session.exec(select(Company)).all()
        with_jobs = set(session.exec(select(Job.company_id).distinct()).all())
        pending = [
            c for c in rows
            if c.tier != "reject" and c.id in with_jobs and c.ppo_lpa is None
            and "[levels]" not in (c.notes or "")
            and (shard is None or (c.id % shards) == shard)
        ]
        pending.sort(key=lambda c: (TIER_ORDER.get(c.tier or "unknown", 9), c.name))
        ids = [c.id for c in pending[:limit]]
    out = {"looked": 0, "found": 0}
    for cid in ids:
        if web.scrapedo_budget()["daily_left"] <= 0:
            out["stopped"] = "scrape.do daily budget"
            break
        r = lookup(cid)
        out["looked"] += 1
        if r.get("found"):
            out["found"] += 1
        with get_session() as session:   # mark it read so the next pass skips it
            c = session.get(Company, cid)
            if c and "[levels]" not in (c.notes or ""):
                c.notes = (c.notes + "\n" if c.notes else "") + "[levels] checked"
                session.add(c)
                session.commit()
    out["budget"] = web.scrapedo_budget()
    return out


__all__ = ["lookup", "lookup_pending"]
