"""What a company's interviews are like, for the company page (2026-10-31, Tejmul: "scrape
information about the company … about the interviews and prep, it should show in the UI").

Sources, cheapest that works first:
  Google (Apify google-search-scraper)  finds the company's Glassdoor id and the blog / Reddit pages
  Glassdoor (Apify scrapifier~glassdoor-interviews-scraper)  candidate reports with their questions
  Reddit (Apify practicaltools~apify-reddit-api)  posts naming the company, with their comments
Glassdoor, AmbitionBox and Reddit refuse a plain read (captcha / network block), which is why
those go through Apify, inside the same daily cap as everything else (actors.apify.daily_usd).

Rows land in interview_note: a candidate's report as written, or a prep point that names the
reports behind it. Nothing is paraphrased into a claim the source did not make.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime

from sqlmodel import col, select

from jobhunter.actors import apify_source
from jobhunter.db import Company, InterviewNote, get_session, init_db, utcnow

log = logging.getLogger(__name__)

GOOGLE = "apify~google-search-scraper"
GLASSDOOR = "scrapifier~glassdoor-interviews-scraper"
REDDIT = "practicaltools~apify-reddit-api"
LABEL = "interview-prep"

_OUTCOME = {"ACCEPT_OFFER": "offer", "NO_OFFER": "no offer", "DECLINE_OFFER": "declined offer"}
_NOISE = ("i am a bot", "welcome to r/", "thank you for your submission", "thanks for submitting",
          "this is a reminder about the rules")
_ABOUT = re.compile(r"interview|\boa\b|online assessment|test link|round|hiring|shortlist|placement|offer|ctc", re.I)


def _glassdoor_id(name: str) -> tuple[str | None, list[dict]]:
    """The company's Glassdoor employer id (E1234567) from a Google search, and the organic
    results of the searches (kept for the caller)."""
    qs = [f"{name} glassdoor interview", f"{name} interview experience"]
    r = apify_source.run_actor(GOOGLE, {"queries": "\n".join(qs), "resultsPerPage": 10, "maxPagesPerQuery": 1,
                                        "countryCode": "in"},
                               per_item=0.0045, start_fee=0.0, max_items=len(qs), label=LABEL)
    if "error" in r:
        log.warning("interviews: google search for %s: %s", name, r["error"])
        return None, []
    results = [o for it in r["items"] for o in it.get("organicResults", [])]
    key = re.sub(r"[^a-z0-9]", "", name.lower())
    for o in results:
        m = re.search(r"glassdoor\.[a-z.]+/.*?(?:-E|_IE)(\d{4,})", o.get("url", ""))
        if m and key[:5] in re.sub(r"[^a-z0-9]", "", (o.get("title", "") + o["url"]).lower()):
            return m.group(1), results
    return None, results


def _upsert(session, company_id: int, source: str, source_id: str, **fields) -> bool:
    row = session.exec(select(InterviewNote).where(InterviewNote.company_id == company_id,
                                                   InterviewNote.source == source,
                                                   InterviewNote.source_id == source_id)).first()
    new = row is None
    if new:
        row = InterviewNote(company_id=company_id, source=source, source_id=source_id, kind=fields.pop("kind"))
    for k, v in fields.items():
        setattr(row, k, v)
    row.fetched_at = utcnow()
    session.add(row)
    return new


def _when(s: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None) if s else None
    except ValueError:
        return None


def save_glassdoor(company_id: int, items: list[dict]) -> int:
    """Glassdoor interview reports as the candidates wrote them. Returns how many were new."""
    added = 0
    with get_session() as s:
        for it in items:
            body = (it.get("processDescription") or "").strip()
            qs = [(q.get("question") if isinstance(q, dict) else str(q)).strip() for q in (it.get("questions") or [])]
            qs = [q for q in qs if q and q != body]
            if not body and not qs:
                continue
            added += _upsert(
                s, company_id, "glassdoor", str(it.get("interviewId")), kind="report",
                url=it.get("url"), role=it.get("jobTitle"),
                outcome=_OUTCOME.get(it.get("outcome") or "", "unknown"),
                difficulty=(it.get("difficulty") or "").replace("_", " ").lower() or None,
                experience=(it.get("experience") or "").lower() or None,
                body=body, questions=json.dumps(qs) if qs else None,
                reported_at=_when(it.get("reviewDateTime")),
            )
        s.commit()
    return added


def save_reddit(company_id: int, name: str, items: list[dict]) -> int:
    """Reddit posts that name the company and are about its hiring, with the comments that say
    something (bots, removed and deleted comments dropped). Returns how many were new."""
    added = 0
    key = name.lower()
    with get_session() as s:
        for it in items:
            title, text = it.get("title") or "", it.get("body") or it.get("selftext") or ""
            comments = [c.get("body", "") if isinstance(c, dict) else str(c) for c in (it.get("comments") or [])]
            comments = [c.strip() for c in comments
                        if c.strip() and c.strip() not in ("[removed]", "[deleted]")
                        and not any(n in c.lower() for n in _NOISE) and len(c.strip()) > 3]
            # the post itself is about the company: named in the title, or in a short body (a long
            # placement table or news piece that lists it in passing is not about its interviews)
            if key not in title.lower() and not (key in text.lower() and len(text) < 1500):
                continue
            if not _ABOUT.search(title + " " + text) and not (key in title.lower() and _ABOUT.search(" ".join(comments))):
                continue
            added += _upsert(
                s, company_id, "reddit", str(it.get("id") or it.get("url")), kind="report",
                url=it.get("url"), title=title.strip(), body=text.strip()[:4000],
                questions=json.dumps(comments[:20]) if comments else None,
                reported_at=_when(it.get("createdAt")),
            )
        s.commit()
    return added


def add_note(company_id: int, *, kind: str, source: str, source_id: str, body: str, **fields) -> bool:
    """One report, official statement or prep point read by hand from a page (a blog, the
    company's own post). Keyed by (source, source_id) so running it again updates the row."""
    with get_session() as s:
        new = _upsert(s, company_id, source, source_id, kind=kind, body=body, **fields)
        s.commit()
    return new


def scrape(company_id: int, *, glassdoor_max: int = 30, reddit_max: int = 40) -> dict:
    """Glassdoor reports and Reddit threads for one company, through Apify."""
    init_db()
    with get_session() as s:
        company = s.get(Company, company_id)
    if company is None:
        return {"error": f"no company {company_id}"}
    out: dict = {"company": company.name, "glassdoor": 0, "reddit": 0}
    gid, _ = _glassdoor_id(company.name)
    out["glassdoor_id"] = gid
    if gid:
        r = apify_source.run_actor(
            GLASSDOOR, {"startUrls": [{"url": f"https://www.glassdoor.com/Interview/x-Interview-Questions-E{gid}.htm"}],
                        "maxInterviewsPerCompany": glassdoor_max, "includeQuestions": True, "country": "www"},
            per_item=0.003, start_fee=0.0, max_items=glassdoor_max, label=LABEL)
        out["glassdoor"] = save_glassdoor(company_id, r["items"]) if "items" in r else r["error"]
    r = apify_source.run_actor(
        REDDIT, {"searches": [company.name], "maxItems": reddit_max, "fetchPostComments": True,
                 "searchPosts": True, "skipCommunity": True},
        per_item=0.0025, start_fee=0.0, max_items=reddit_max, label=LABEL)
    out["reddit"] = save_reddit(company_id, company.name, r["items"]) if "items" in r else r["error"]
    log.info("interviews: %s", out)
    return out


def for_company(company_id: int) -> dict:
    """The company page's interview section: prep points, the company's own word, then every
    report newest first."""
    with get_session() as s:
        rows = s.exec(select(InterviewNote).where(InterviewNote.company_id == company_id)
                      .order_by(col(InterviewNote.reported_at).desc().nulls_last(), InterviewNote.id)).all()

    def d(r: InterviewNote) -> dict:
        return {"id": r.id, "kind": r.kind, "source": r.source, "url": r.url, "role": r.role,
                "outcome": r.outcome, "difficulty": r.difficulty, "experience": r.experience,
                "title": r.title, "body": r.body, "questions": json.loads(r.questions) if r.questions else [],
                "evidence": r.evidence,
                "reported_at": r.reported_at.date().isoformat() if r.reported_at else None}

    prep = sorted((r for r in rows if r.kind == "prep"), key=lambda r: r.source_id or "")
    reports = [d(r) for r in rows if r.kind == "report"]
    return {
        "prep": [d(r) for r in prep],
        "official": [d(r) for r in rows if r.kind == "official"],
        "reports": reports,
        "counts": {
            "reports": len(reports),
            "offers": sum(1 for r in reports if r["outcome"] == "offer" or r["outcome"] == "declined offer"),
            "by_source": {src: sum(1 for r in reports if r["source"] == src) for src in {r["source"] for r in reports}},
        },
        "updated_at": max((r.fetched_at for r in rows), default=None),
    }
