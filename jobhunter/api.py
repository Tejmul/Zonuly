"""FastAPI backend — every read and action the dashboard needs.

Long-running work (scrape, score, contact discovery, sending) is dispatched to
background tasks and reported through /api/tasks, so the UI never blocks on a
20-second LLM call.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any, Literal

import yaml
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlmodel import col, func, select

from jobhunter import CONFIG, ROOT
from jobhunter.db import Company, Contact, Email, Job, Reply, get_session, init_db
from jobhunter.research.routes import router as research_router

log = logging.getLogger(__name__)

_API = CONFIG.get("api") or {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    if _API.get("scheduler", True):
        from jobhunter import scheduler

        scheduler.start()
    yield
    from jobhunter import scheduler

    scheduler.stop()


app = FastAPI(title="JobHunter API", version="1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_API.get("cors_origins", ["http://localhost:3000"]),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Web research / data acquisition (Agent Reach backends) -> /api/research/*
app.include_router(research_router)


# ---------------------------------------------------------------- task registry

_tasks: dict[str, dict] = {}
_lock = threading.Lock()


def _run_task(name: str, fn, *args, **kwargs) -> str:
    task_id = uuid.uuid4().hex[:12]
    with _lock:
        _tasks[task_id] = {"id": task_id, "name": name, "status": "running", "started": _now(), "result": None}

    def runner() -> None:
        try:
            result = fn(*args, **kwargs)
            with _lock:
                _tasks[task_id].update(status="done", result=result, finished=_now())
        except Exception as e:  # noqa: BLE001 — surfaced through /api/tasks
            log.exception("task %s failed", name)
            with _lock:
                _tasks[task_id].update(status="error", error=str(e)[:500], finished=_now())

    threading.Thread(target=runner, daemon=True, name=f"task-{name}").start()
    return task_id


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@app.get("/api/tasks")
def list_tasks() -> list[dict]:
    with _lock:
        return sorted(_tasks.values(), key=lambda t: t["started"], reverse=True)[:25]


@app.get("/api/tasks/{task_id}")
def get_task(task_id: str) -> dict:
    with _lock:
        task = _tasks.get(task_id)
    if not task:
        raise HTTPException(404, "unknown task")
    return task


# ---------------------------------------------------------------- serializers

def _job_dict(job: Job, *, full: bool = False) -> dict:
    reasons, verdict, breakdown = [], None, {}
    if job.match_reasons:
        try:
            parsed = json.loads(job.match_reasons)
            reasons = parsed.get("reasons", [])
            verdict = parsed.get("verdict")
            breakdown = parsed.get("breakdown", {})
        except json.JSONDecodeError:
            verdict = job.match_reasons
    gaps = []
    if job.skill_gaps:
        try:
            gaps = json.loads(job.skill_gaps)
        except json.JSONDecodeError:
            gaps = [job.skill_gaps]

    d = {
        "id": job.id,
        "company_id": job.company_id,
        "company_name": job.company_name,
        "title": job.title,
        "location": job.location,
        "remote": job.remote,
        "url": job.url,
        "source": job.source,
        "posted_at": job.posted_at.isoformat() if job.posted_at else None,
        "scraped_at": job.scraped_at.isoformat() if job.scraped_at else None,
        "salary_min_lpa": job.salary_min_lpa,
        "salary_max_lpa": job.salary_max_lpa,
        "salary_raw": job.salary_raw,
        "currency": job.currency,
        "match_score": job.match_score,
        "embed_sim": job.embed_sim,
        "status": job.status,
        "reasons": reasons,
        "verdict": verdict,
        "breakdown": breakdown,
        "gaps": gaps,
    }
    if full:
        d["description"] = job.description
    return d


def _contact_dict(c: Contact, company_name: str | None = None) -> dict:
    notes = None
    if c.research_notes:
        try:
            notes = json.loads(c.research_notes)
        except json.JSONDecodeError:
            notes = {"notes": c.research_notes}
    return {
        "id": c.id,
        "company_id": c.company_id,
        "company_name": company_name,
        "name": c.name,
        "role": c.role,
        "email": c.email,
        "github": c.github,
        "linkedin": c.linkedin,
        "source": c.source,
        "confidence": c.confidence,
        "is_recruiter": c.is_recruiter,
        "research": notes,
        "researched_at": c.researched_at.isoformat() if c.researched_at else None,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


def _email_dict(e: Email, *, contact: Contact | None = None, company: Company | None = None) -> dict:
    return {
        "id": e.id,
        "contact_id": e.contact_id,
        "company_id": e.company_id,
        "job_id": e.job_id,
        "contact_name": contact.name if contact else None,
        "contact_role": contact.role if contact else None,
        "contact_confidence": contact.confidence if contact else None,
        "company_name": company.name if company else None,
        "to_email": e.to_email,
        "subject": e.subject,
        "body": e.body,
        "kind": e.kind,
        "status": e.status,
        "error": e.error,
        "gmail_thread_id": e.gmail_thread_id,
        "created_at": e.created_at.isoformat() if e.created_at else None,
        "approved_at": e.approved_at.isoformat() if e.approved_at else None,
        "sent_at": e.sent_at.isoformat() if e.sent_at else None,
        "followup_sent": e.followup_sent,
    }


# ---------------------------------------------------------------- health & overview

@app.get("/api/health")
def health() -> dict:
    from jobhunter import llm, matcher, scheduler
    from jobhunter.contacts import hunter
    from jobhunter.outreach import gmail, sender

    profile_path = ROOT / CONFIG["profile_path"]
    return {
        "ok": True,
        "llm": llm.health(),
        "gmail": gmail.status(),
        "quota": sender.quota(),
        "hunter": {"configured": hunter.available(), "lookups_left": hunter.budget_left()},
        "github_token": bool((CONFIG.get("contacts") or {}).get("github_token")),
        "profile": {"exists": profile_path.exists(), "path": str(profile_path)},
        "counts": matcher.counts(),
        "scheduler": scheduler.status(),
    }


@app.get("/api/models")
def models_status() -> dict:
    """OpenRouter: key presence, aliases, caps and today's spend. Never the key."""
    from jobhunter import llm

    return llm.health()


@app.get("/api/models/costs")
def models_costs(days: int = Query(1, ge=1, le=90), month: bool = False) -> dict:
    """What the model layer has cost, by alias and by stage."""
    from jobhunter import openrouter

    return {"spend": openrouter.spend(days=days, month=month), "budget": openrouter.budget_status()}


@app.get("/api/overview")
def overview() -> dict:
    from jobhunter import matcher
    from jobhunter.outreach import sender, tracker

    with get_session() as session:
        by_source = dict(
            session.exec(select(Job.source, func.count()).group_by(Job.source)).all()
        )
        week_ago = datetime.utcnow() - timedelta(days=7)
        new_this_week = session.exec(
            select(func.count()).select_from(Job).where(col(Job.scraped_at) >= week_ago)
        ).one()
        companies = session.exec(select(func.count()).select_from(Company)).one()
        contacts = session.exec(select(func.count()).select_from(Contact)).one()
        verified = session.exec(
            select(func.count()).select_from(Contact).where(Contact.confidence == "verified")
        ).one()
        top = session.exec(
            select(Job).where(Job.status == "high_match").order_by(col(Job.match_score).desc()).limit(8)
        ).all()
        recent_replies = session.exec(
            select(Reply).order_by(col(Reply.received_at).desc()).limit(5)
        ).all()
        reply_rows = []
        for r in recent_replies:
            email = session.get(Email, r.email_id)
            company = session.get(Company, email.company_id) if email else None
            reply_rows.append(
                {
                    "id": r.id,
                    "sentiment": r.sentiment,
                    "reason": r.sentiment_reason,
                    "from_addr": r.from_addr,
                    "company_name": company.name if company else None,
                    "received_at": r.received_at.isoformat() if r.received_at else None,
                }
            )

    return {
        "jobs": matcher.counts(),
        "funnel": tracker.funnel(),
        "quota": sender.quota(),
        "by_source": by_source,
        "new_this_week": new_this_week,
        "companies": companies,
        "contacts": {"total": contacts, "verified": verified},
        "top_matches": [_job_dict(j) for j in top],
        "recent_replies": reply_rows,
    }


# ---------------------------------------------------------------- jobs

@app.get("/api/jobs")
def list_jobs(
    q: str | None = None,
    status: str | None = None,
    source: str | None = None,
    min_score: int | None = None,
    min_lpa: float | None = None,
    remote: bool | None = None,
    sort: Literal["score", "salary", "recent", "similarity"] = "score",
    limit: int = Query(50, le=500),
    offset: int = 0,
) -> dict:
    with get_session() as session:
        stmt = select(Job)
        if status:
            stmt = stmt.where(Job.status == status)
        if source:
            stmt = stmt.where(Job.source == source)
        if min_score is not None:
            stmt = stmt.where(col(Job.match_score) >= min_score)
        if min_lpa is not None:
            stmt = stmt.where(col(Job.salary_max_lpa) >= min_lpa)
        if remote is not None:
            stmt = stmt.where(Job.remote == remote)
        if q:
            like = f"%{q}%"
            stmt = stmt.where(col(Job.title).like(like) | col(Job.company_name).like(like))

        total = session.exec(select(func.count()).select_from(stmt.subquery())).one()

        order = {
            "score": col(Job.match_score).desc(),
            "salary": col(Job.salary_max_lpa).desc(),
            "recent": col(Job.scraped_at).desc(),
            "similarity": col(Job.embed_sim).desc(),
        }[sort]
        rows = session.exec(stmt.order_by(order).offset(offset).limit(limit)).all()
        return {"total": total, "items": [_job_dict(j) for j in rows]}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: int) -> dict:
    with get_session() as session:
        job = session.get(Job, job_id)
        if job is None:
            raise HTTPException(404, "job not found")
        data = _job_dict(job, full=True)
        contacts = []
        if job.company_id:
            company = session.get(Company, job.company_id)
            data["company"] = (
                {
                    "id": company.id,
                    "name": company.name,
                    "website": company.website,
                    "domain": company.domain,
                    "github_org": company.github_org,
                    "email_pattern": company.email_pattern,
                    "notes": company.notes,
                    "contacts_found_at": company.contacts_found_at.isoformat()
                    if company.contacts_found_at else None,
                }
                if company else None
            )
            contacts = [
                _contact_dict(c, company.name if company else None)
                for c in session.exec(select(Contact).where(Contact.company_id == job.company_id)).all()
            ]
        data["contacts"] = contacts
        return data


class JobStatusIn(BaseModel):
    status: Literal["new", "scored", "high_match", "ignored", "applied"]


@app.patch("/api/jobs/{job_id}")
def update_job(job_id: int, payload: JobStatusIn) -> dict:
    with get_session() as session:
        job = session.get(Job, job_id)
        if job is None:
            raise HTTPException(404, "job not found")
        job.status = payload.status
        session.add(job)
        session.commit()
        return {"ok": True, "id": job_id, "status": payload.status}


class CompanyUpdateIn(BaseModel):
    notes: Optional[str] = None


@app.patch("/api/companies/{company_id}")
def update_company(company_id: int, payload: CompanyUpdateIn) -> dict:
    with get_session() as session:
        company = session.get(Company, company_id)
        if company is None:
            raise HTTPException(404, "company not found")
        if payload.notes is not None:
            company.notes = payload.notes
        session.add(company)
        session.commit()
        return {"ok": True, "id": company_id, "notes": company.notes}


@app.get("/api/sources")
def sources() -> dict:
    with get_session() as session:
        rows = session.exec(select(Job.source, func.count()).group_by(Job.source)).all()
    return {"sources": [{"name": s, "jobs": n} for s, n in rows], "enabled": CONFIG.get("sources", {})}


# ---------------------------------------------------------------- companies & contacts

@app.get("/api/companies")
def list_companies(
    has_contacts: bool | None = None,
    tier: str | None = Query(None, description="tier1 | tier2 | prospect | unknown | reject"),
    hiring: str | None = Query(None, description="verified | role_missing | not_authorized | unreachable"),
    sort: str = Query("score", description="score | tier | pay | name"),
    limit: int = Query(200, le=1000),
) -> list[dict]:
    with get_session() as session:
        companies = session.exec(select(Company).order_by(col(Company.name)).limit(limit)).all()
        out = []
        for c in companies:
            n_contacts = session.exec(
                select(func.count()).select_from(Contact).where(Contact.company_id == c.id)
            ).one()
            if has_contacts is True and n_contacts == 0:
                continue
            if has_contacts is False and n_contacts > 0:
                continue
            if tier and (c.tier or "unknown") != tier:
                continue
            if hiring and (c.hiring_status or "unchecked") != hiring:
                continue
            n_jobs = session.exec(
                select(func.count()).select_from(Job).where(Job.company_id == c.id)
            ).one()
            best = session.exec(
                select(func.max(Job.match_score)).where(Job.company_id == c.id)
            ).one()
            out.append(
                {
                    "id": c.id,
                    "name": c.name,
                    "website": c.website,
                    "domain": c.domain,
                    "github_org": c.github_org,
                    "email_pattern": c.email_pattern,
                    "ats": c.ats,
                    "jobs": n_jobs,
                    "contacts": n_contacts,
                    "best_score": best,
                    "contacts_found_at": c.contacts_found_at.isoformat() if c.contacts_found_at else None,
                    # --- targeting (jobhunter/targeting.py): why this company, or why not
                    "tier": c.tier,
                    "tier_reason": c.tier_reason,
                    "description": c.description,
                    "region": c.hq_region,
                    "underrated": c.underrated,
                    "stipend_inr_month": c.stipend_inr_month,
                    "ppo_lpa": c.ppo_lpa,
                    "funding_stage": c.funding_stage,
                    "funding_amount_usd_m": c.funding_amount_usd_m,
                    # --- hiring authenticity (jobhunter/hiring_verify.py)
                    "hiring_status": c.hiring_status,
                    "hiring_evidence": c.hiring_evidence,
                    "careers_url": c.careers_url,
                }
            )
        tier_rank = {"tier1": 0, "tier2": 1, "prospect": 2, "unknown": 3, "reject": 4}
        sorters = {
            "score": lambda c: (c["best_score"] is None, -(c["best_score"] or 0)),
            "tier": lambda c: (tier_rank.get(c["tier"] or "unknown", 9), -(c["ppo_lpa"] or 0), c["name"]),
            "pay": lambda c: (-(c["ppo_lpa"] or 0), c["name"]),
            "name": lambda c: c["name"].lower(),
        }
        return sorted(out, key=sorters.get(sort, sorters["score"]))


@app.get("/api/network")
def company_network(include_rejects: bool = False) -> dict:
    """The company atlas: layers, nodes, dependencies, chokepoints, bottlenecks."""
    from jobhunter import network

    return network.build(include_rejects=include_rejects)


@app.get("/api/companies/grouped")
def companies_grouped(include_rejects: bool = False) -> dict:
    """One row per company, with its roles grouped by family and its referrers attached.

    Distinct from /api/network, which is a map: there a company legitimately appears in
    every role family it hires for. Here the company is the unit.
    """
    from jobhunter import network

    return network.company_list(include_rejects=include_rejects)


@app.get("/api/companies/{company_id}/detail")
def company_detail(company_id: int) -> dict:
    """Everything known about one company, with the graph's verdict attached."""
    from jobhunter import network

    out = network.company_detail(company_id)
    if out.get("error"):
        raise HTTPException(status_code=404, detail=out["error"])
    return out


@app.get("/api/contacts")
def list_contacts(
    company_id: int | None = None,
    confidence: str | None = None,
    limit: int = Query(200, le=1000),
) -> list[dict]:
    with get_session() as session:
        stmt = select(Contact)
        if company_id:
            stmt = stmt.where(Contact.company_id == company_id)
        if confidence:
            stmt = stmt.where(Contact.confidence == confidence)
        rows = session.exec(stmt.limit(limit)).all()
        names = {c.id: c.name for c in session.exec(select(Company)).all()}
        out = [_contact_dict(c, names.get(c.company_id)) for c in rows]
        rank = {"verified": 0, "pattern-guessed": 1, "scraped": 2}
        return sorted(out, key=lambda c: (rank.get(c["confidence"], 3), c["name"] is None))


@app.post("/api/companies/{company_id}/find-contacts")
def find_contacts(company_id: int) -> dict:
    from jobhunter import contacts as contacts_mod

    return {"task_id": _run_task(f"find-contacts:{company_id}", contacts_mod.discover, company_id)}


@app.post("/api/companies/{company_id}/verify-hiring")
def verify_hiring(company_id: int, fresh: bool = False) -> dict:
    """Re-check the hiring claim against the company's own board / careers page."""
    from jobhunter import hiring_verify

    return {"task_id": _run_task(f"verify:{company_id}", hiring_verify.verify_company, company_id, fresh=fresh)}


@app.post("/api/companies/{company_id}/enrich")
def enrich_company(company_id: int, fresh: bool = False) -> dict:
    """Read the company's own site for a description and funding, then re-grade."""
    from jobhunter import enrich

    return {"task_id": _run_task(f"enrich:{company_id}", enrich.enrich_company, company_id, fresh=fresh)}


@app.post("/api/contacts/{contact_id}/research")
def research(contact_id: int, job_id: int | None = None) -> dict:
    import asyncio

    from jobhunter.outreach.researcher import research_contact

    return {
        "task_id": _run_task(
            f"research:{contact_id}", lambda: asyncio.run(research_contact(contact_id, job_id))
        )
    }


# ---------------------------------------------------------------- review queue & emails

@app.get("/api/emails")
def list_emails(
    status: str | None = None,
    kind: str | None = None,
    limit: int = Query(200, le=1000),
) -> list[dict]:
    with get_session() as session:
        stmt = select(Email)
        if status:
            stmt = stmt.where(Email.status == status)
        if kind:
            stmt = stmt.where(Email.kind == kind)
        rows = session.exec(stmt.order_by(col(Email.created_at).desc()).limit(limit)).all()
        return [
            _email_dict(
                e,
                contact=session.get(Contact, e.contact_id),
                company=session.get(Company, e.company_id),
            )
            for e in rows
        ]


@app.get("/api/emails/{email_id}")
def get_email(email_id: int) -> dict:
    with get_session() as session:
        email = session.get(Email, email_id)
        if email is None:
            raise HTTPException(404, "email not found")
        data = _email_dict(
            email,
            contact=session.get(Contact, email.contact_id),
            company=session.get(Company, email.company_id),
        )
        job = session.get(Job, email.job_id) if email.job_id else None
        data["job"] = _job_dict(job) if job else None
        data["replies"] = [
            {
                "id": r.id,
                "from_addr": r.from_addr,
                "body": r.body,
                "sentiment": r.sentiment,
                "sentiment_reason": r.sentiment_reason,
                "received_at": r.received_at.isoformat() if r.received_at else None,
            }
            for r in session.exec(select(Reply).where(Reply.email_id == email_id)).all()
        ]
        return data


class DraftIn(BaseModel):
    contact_id: int
    job_id: int | None = None


@app.post("/api/emails/draft")
def create_draft(payload: DraftIn) -> dict:
    from jobhunter.outreach import drafter

    return {
        "task_id": _run_task(
            f"draft:{payload.contact_id}", drafter.draft_for, payload.contact_id, payload.job_id
        )
    }


@app.post("/api/emails/draft-batch")
def draft_batch(limit: int = 5, per_company: int = 2) -> dict:
    from jobhunter.outreach import drafter

    return {
        "task_id": _run_task(
            "draft-batch", drafter.draft_for_high_matches, limit=limit, per_company=per_company
        )
    }


class ApproveIn(BaseModel):
    subject: str | None = None
    body: str | None = None


@app.post("/api/emails/{email_id}/approve")
def approve_email(email_id: int, payload: ApproveIn | None = None) -> dict:
    from jobhunter.outreach import sender

    payload = payload or ApproveIn()
    result = sender.approve(email_id, subject=payload.subject, body=payload.body)
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


@app.post("/api/emails/{email_id}/reject")
def reject_email(email_id: int) -> dict:
    from jobhunter.outreach import sender

    result = sender.reject(email_id)
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


@app.post("/api/emails/{email_id}/send")
def send_one(email_id: int, ignore_window: bool = False) -> dict:
    from jobhunter.outreach import sender

    result = sender.send_email(email_id, ignore_window=ignore_window)
    if "error" in result:
        raise HTTPException(400, result["error"])
    return result


@app.post("/api/emails/send-approved")
def send_all(limit: int | None = None, ignore_window: bool = False) -> dict:
    from jobhunter.outreach import sender

    return {
        "task_id": _run_task(
            "send-approved", sender.send_approved, limit=limit, ignore_window=ignore_window
        )
    }


@app.post("/api/emails/{email_id}/followup")
def followup(email_id: int) -> dict:
    from jobhunter.outreach import drafter

    return {"task_id": _run_task(f"followup:{email_id}", drafter.draft_followup, email_id)}


# ---------------------------------------------------------------- tracker & replies

@app.get("/api/tracker")
def tracker_matrix() -> list[dict]:
    """The sent matrix: company x contacted x when x status."""
    with get_session() as session:
        emails = session.exec(
            select(Email).where(col(Email.status).in_(["approved", "sent", "replied", "failed"]))
        ).all()
        rows = []
        for e in emails:
            contact = session.get(Contact, e.contact_id)
            company = session.get(Company, e.company_id)
            job = session.get(Job, e.job_id) if e.job_id else None
            replies = session.exec(select(Reply).where(Reply.email_id == e.id)).all()
            rows.append(
                {
                    "email_id": e.id,
                    "company_id": e.company_id,
                    "company_name": company.name if company else None,
                    "contact_name": contact.name if contact else None,
                    "contact_email": e.to_email,
                    "contact_confidence": contact.confidence if contact else None,
                    "job_title": job.title if job else None,
                    "job_id": e.job_id,
                    "match_score": job.match_score if job else None,
                    "kind": e.kind,
                    "status": e.status,
                    "sent_at": e.sent_at.isoformat() if e.sent_at else None,
                    "followup_sent": e.followup_sent,
                    "reply_sentiment": replies[-1].sentiment if replies else None,
                    "reply_count": len(replies),
                }
            )
        return sorted(rows, key=lambda r: (r["sent_at"] is None, r["sent_at"] or ""), reverse=True)


@app.get("/api/replies")
def list_replies(sentiment: str | None = None, limit: int = Query(200, le=1000)) -> list[dict]:
    with get_session() as session:
        stmt = select(Reply)
        if sentiment:
            stmt = stmt.where(Reply.sentiment == sentiment)
        rows = session.exec(stmt.order_by(col(Reply.received_at).desc()).limit(limit)).all()
        out = []
        for r in rows:
            email = session.get(Email, r.email_id)
            company = session.get(Company, email.company_id) if email else None
            contact = session.get(Contact, email.contact_id) if email else None
            out.append(
                {
                    "id": r.id,
                    "email_id": r.email_id,
                    "from_addr": r.from_addr,
                    "body": r.body,
                    "sentiment": r.sentiment,
                    "sentiment_reason": r.sentiment_reason,
                    "received_at": r.received_at.isoformat() if r.received_at else None,
                    "company_name": company.name if company else None,
                    "contact_name": contact.name if contact else None,
                    "subject": email.subject if email else None,
                }
            )
        return out


@app.post("/api/replies/poll")
def poll_replies() -> dict:
    from jobhunter.outreach import tracker

    return {"task_id": _run_task("poll-replies", tracker.poll)}


# ---------------------------------------------------------------- pipeline actions

@app.post("/api/pipeline/scrape")
def run_scrape(sources: str | None = None) -> dict:
    from jobhunter import pipeline

    names = [s.strip() for s in sources.split(",")] if sources else None
    return {"task_id": _run_task("scrape", lambda: pipeline.scrape(names).as_dict())}


@app.post("/api/pipeline/score")
def run_score(limit: int = 40) -> dict:
    from jobhunter import matcher

    def job() -> dict:
        embedded = matcher.prefilter(limit=1000)
        scored = matcher.score_pending(limit=limit)
        return {"embedded": embedded, **scored}

    return {"task_id": _run_task("score", job)}


@app.post("/api/pipeline/salaries")
def run_salaries(limit: int = 40) -> dict:
    from jobhunter import pipeline

    return {"task_id": _run_task("salaries", pipeline.extract_salaries, limit)}


@app.post("/api/pipeline/rescore")
def run_rescore() -> dict:
    from jobhunter import matcher

    return {"task_id": _run_task("rescore", matcher.rescore_all)}


@app.post("/api/pipeline/reparse-resume")
def reparse_resume() -> dict:
    from jobhunter import resume

    return {"task_id": _run_task("reparse-resume", lambda: {"name": resume.build_profile().get("name")})}


@app.post("/api/pipeline/daily")
def run_daily() -> dict:
    from jobhunter import scheduler

    return {"task_id": _run_task("daily-cycle", scheduler.daily_cycle)}


# ---------------------------------------------------------------- profile & settings

@app.get("/api/profile")
def get_profile() -> dict:
    from jobhunter import resume

    try:
        profile = resume.load_profile()
    except Exception as e:  # noqa: BLE001 — no resume yet is a normal first-run state
        raise HTTPException(404, f"no profile yet: {e}") from e
    return {k: v for k, v in profile.items() if k != "_resume_text"}


@app.get("/api/config")
def get_config() -> dict:
    cfg = json.loads(json.dumps(CONFIG, default=str))
    # never ship secrets to the browser
    cfg.get("contacts", {}).pop("github_token", None)
    cfg.get("contacts", {}).pop("hunter_api_key", None)
    return cfg


class ConfigPatch(BaseModel):
    search: dict[str, Any] | None = None
    matching: dict[str, Any] | None = None
    outreach: dict[str, Any] | None = None
    sources: dict[str, Any] | None = None
    notifications: dict[str, Any] | None = None


@app.patch("/api/config")
def patch_config(payload: ConfigPatch) -> dict:
    """Write settings back to config.yaml. Secrets are only editable in the file itself."""
    path = ROOT / "config.yaml"
    current = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    updates = {k: v for k, v in payload.model_dump(exclude_none=True).items()}

    for section, values in updates.items():
        if section not in current or not isinstance(current[section], dict):
            current[section] = {}
        for key, value in values.items():
            if key in ("github_token", "hunter_api_key"):
                continue  # secrets stay out of the API surface
            current[section][key] = value

    path.write_text(yaml.safe_dump(current, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return {"ok": True, "restart_required": True, "config": current}


@app.post("/api/gmail/authorize")
def gmail_authorize() -> dict:
    """Kick off the OAuth consent flow. Opens a browser on the machine running the API."""
    from jobhunter.outreach import gmail

    return {"task_id": _run_task("gmail-auth", gmail.authorize)}


@app.get("/api/gmail/status")
def gmail_status() -> dict:
    from jobhunter.outreach import gmail
    from jobhunter.outreach import sender

    return {**gmail.status(), "quota": sender.quota(), "address": gmail.my_address()}


# ---------------------------------------------------------------- knowledge graph

@app.get("/api/kg/stats")
def kg_stats() -> dict:
    from jobhunter.kg.store import Graph

    with Graph() as g:
        return g.stats()


@app.get("/api/kg/search")
def kg_search(q: str, kind: str | None = None, limit: int = Query(20, le=200), any: bool = False) -> list[dict]:
    from jobhunter.kg.store import Graph

    kinds = [k.strip() for k in kind.split(",")] if kind else None
    with Graph() as g:
        return g.search(q, kinds=kinds, limit=limit, mode="or" if any else "and")


@app.get("/api/kg/nodes/{node_id}")
def kg_node(node_id: str, depth: int = Query(1, le=3)) -> dict:
    from jobhunter.kg.store import Graph

    with Graph() as g:
        node = g.get(node_id)
        if node is None:
            raise HTTPException(404, "unknown node")
        return {"node": node, **g.neighbors(node_id, depth=depth)}


@app.get("/api/kg/path")
def kg_path(a: str, b: str, max_depth: int = Query(6, le=10)) -> list[dict]:
    from jobhunter.kg.store import Graph

    with Graph() as g:
        return g.path(a, b, max_depth=max_depth)


@app.get("/api/kg/graph")
def kg_graph(layer: str | None = None, all_jobs: bool = False) -> dict:
    """The whole graph for a viewer. Unscored jobs are excluded unless asked for."""
    from jobhunter.kg import export

    return export.to_dict(include_all_jobs=all_jobs, layer=layer)


@app.get("/api/kg/hubs")
def kg_hubs(layer: str = "context", kind: str | None = None, top: int = Query(15, le=100)) -> list[dict]:
    from jobhunter.kg import analyze

    kinds = [k.strip() for k in kind.split(",")] if kind else None
    return analyze.hubs(layer=None if layer == "all" else layer, kinds=kinds, top=top)


@app.get("/api/kg/brief", response_class=PlainTextResponse)
def kg_brief() -> str:
    from jobhunter.kg import brief

    return brief.render()


class ComposeIn(BaseModel):
    statement: str | None = None
    constraints: list[str] | None = None
    top: int = 2
    include_dropped: bool = False


@app.post("/api/kg/compose")
def kg_compose(payload: ComposeIn) -> dict:
    from jobhunter.kg import compose

    return compose.compose(
        payload.statement, constraints=payload.constraints, top=payload.top, include_dropped=payload.include_dropped
    )


class NoteIn(BaseModel):
    text: str
    about: list[str] = []
    tags: list[str] = []
    title: str | None = None


@app.post("/api/kg/notes")
def kg_note(payload: NoteIn) -> dict:
    from jobhunter.kg import brief
    from jobhunter.kg.store import Graph

    with Graph() as g:
        result = g.remember(payload.text, about=payload.about, tags=payload.tags, title=payload.title)
    result["brief"] = brief.write()
    return result


@app.post("/api/kg/build")
def kg_build() -> dict:
    """Reload context.yaml, re-sync every table, rewrite BRIEF.md and the viewer. Background task."""
    from jobhunter.kg import brief, context, export, sync
    from jobhunter.kg.store import Graph

    def job() -> dict:
        with Graph() as g:
            ctx = context.load(g)
        return {"context": ctx, "data": sync.sync_all(), "brief": brief.write(), "viewer": export.write_html()}

    return {"task_id": _run_task("kg-build", job)}


def serve(host: str | None = None, port: int | None = None, reload: bool = False) -> None:
    import uvicorn

    uvicorn.run(
        "jobhunter.api:app" if reload else app,
        host=host or _API.get("host", "127.0.0.1"),
        port=port or int(_API.get("port", 8000)),
        reload=reload,
    )
