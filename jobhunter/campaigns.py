"""Campaigns — `/api/campaigns/*`.

A campaign is an outreach run that n8n executes; Zonuly's part is to announce that
it has started. There is no campaign table yet: the id is whatever the caller uses
to track it (or one minted here), and the record of the run lives in n8n.

Sync `def` handlers, like the research router: the call underneath is a blocking
HTTP post, and FastAPI's threadpool keeps it off the event loop.
"""

from __future__ import annotations

import json
import logging
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from jobhunter import n8n
from jobhunter.db import Company, Contact, Email, get_session, init_db, utcnow
from sqlmodel import col, select

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/campaigns", tags=["campaigns"])


class CampaignStartIn(BaseModel):
    campaign_id: str | None = Field(None, min_length=1, max_length=120,
                                    description="Your id for the campaign; minted when omitted")
    campaign_name: str | None = Field(None, max_length=200)
    # anything else the workflow wants (list key, segment, sender...) rides along untouched
    metadata: dict | None = None


@router.get("/status")
def campaigns_status() -> dict:
    """Whether the n8n side is wired up. The URL itself is never returned."""
    return {"n8n": n8n.status()}


@router.post("/start")
def start_campaign(body: CampaignStartIn) -> dict:
    campaign_id = body.campaign_id or uuid.uuid4().hex[:12]
    if not n8n.configured():
        raise HTTPException(503, n8n.status()["hint"])
    try:
        result = n8n.trigger_campaign(campaign_id, body.campaign_name, action="start", extra=body.metadata)
    except n8n.N8nError as e:
        # 502: the failure is upstream of us, and the caller may simply retry
        raise HTTPException(502, f"Campaign {campaign_id} was not started: {e}") from e
    return {
        "ok": True,
        "campaign_id": campaign_id,
        "campaign_name": body.campaign_name,
        "action": "start",
        "n8n": result,
    }


# ---------------------------------------------------------------- events from n8n

class CampaignEventIn(BaseModel):
    """What the n8n workflow tells us after it acts — 2026-09-22, so the dashboard shows
    n8n's sends and replies live instead of a sheet. Add an HTTP Request node after
    "Send a message" (event=sent) and in the InboxPlus reply hook (event=replied)."""
    event: str = Field(..., pattern="^(sent|replied|bounced|opened)$")
    to_email: str = Field(..., min_length=3, max_length=200)
    subject: str | None = Field(None, max_length=300)
    body: str | None = Field(None, max_length=20000)
    message_id: str | None = Field(None, max_length=200)
    thread_id: str | None = Field(None, max_length=200)
    campaign_id: str | None = Field(None, max_length=120)
    contact_name: str | None = Field(None, max_length=200)
    company_name: str | None = Field(None, max_length=200)
    from_addr: str | None = Field(None, max_length=200)   # replied: who wrote back


def _contact_for(session, ev: CampaignEventIn) -> Contact:
    addr = ev.to_email.strip().lower()
    found = session.exec(select(Contact).where(col(Contact.email) == addr)).first()
    if found:
        return found
    # n8n reached someone the pipeline never met: keep the record, mark where it came from
    company = None
    if ev.company_name:
        company = session.exec(select(Company).where(col(Company.name) == ev.company_name.strip())).first()
    if company is None:
        name = ev.company_name.strip() if ev.company_name else addr.split("@")[-1]
        company = session.exec(select(Company).where(col(Company.name) == name)).first() or Company(name=name)
        session.add(company)
        session.commit()
        session.refresh(company)
    contact = Contact(company_id=company.id, name=ev.contact_name, email=addr, source="n8n",
                      confidence="scraped", is_recruiter=False)
    session.add(contact)
    session.commit()
    session.refresh(contact)
    return contact


def _email_for(session, ev: CampaignEventIn) -> Email | None:
    addr = ev.to_email.strip().lower()
    q = select(Email).where(col(Email.to_email) == addr)
    if ev.thread_id:
        hit = session.exec(select(Email).where(Email.gmail_thread_id == ev.thread_id)).first()
        if hit:
            return hit
    return session.exec(q.where(col(Email.status).in_(["sent", "replied"])).order_by(col(Email.sent_at).desc())).first()


@router.post("/events")
def campaign_event(ev: CampaignEventIn) -> dict:
    init_db()
    if ev.event == "opened":
        return {"ok": True, "recorded": None, "note": "opens are not tracked here; sends, replies and bounces are"}

    if ev.event == "sent":
        with get_session() as session:
            contact = _contact_for(session, ev)
            row = Email(
                contact_id=contact.id, company_id=contact.company_id, to_email=ev.to_email.strip().lower(),
                subject=(ev.subject or "(sent by n8n)")[:180], body=ev.body or "", kind="cold", status="sent",
                sent_at=utcnow(), approved_at=utcnow(), gmail_message_id=ev.message_id, gmail_thread_id=ev.thread_id,
                address_confidence=contact.confidence, error=None,
            )
            if ev.campaign_id:
                row.review_flags = json.dumps([{"kind": "n8n", "detail": f"campaign {ev.campaign_id}"}])
            session.add(row)
            session.commit()
            session.refresh(row)
            log.info("n8n sent %s -> email %s", ev.to_email, row.id)
            return {"ok": True, "recorded": "sent", "email_id": row.id}

    with get_session() as session:
        email = _email_for(session, ev)
        if email is None:
            raise HTTPException(404, f"no sent email to {ev.to_email} is on record; send the 'sent' event first")
        email_id = email.id

    if ev.event == "bounced":
        with get_session() as session:
            email = session.get(Email, email_id)
            email.status = "failed"
            email.error = f"bounced (n8n): {(ev.body or '')[:200]}"
            session.add(email)
            session.commit()
        return {"ok": True, "recorded": "bounced", "email_id": email_id}

    from jobhunter.outreach import tracker

    result = tracker.record_reply(email_id, ev.body or "", sender=ev.from_addr or ev.to_email,
                                  message_id=ev.message_id)
    return {"ok": True, "recorded": "replied", "email_id": email_id, **result}
