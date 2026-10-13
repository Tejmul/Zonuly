"""Follow-ups — one, after five silent days, on the same thread, into the review queue.

drafter.draft_followup writes it with the model. The free models hit their daily ceiling
most days, so when the model gives nothing the follow-up is written from a short fixed
template instead (plain, 40–60 words, says it is the last note) — a silent thread should
not wait on a model budget. Either way it is a DRAFT: it waits for the user's approval
like every other mail, threads under the original (Re: subject, In-Reply-To/References
set by the sender from the parent's Message-ID) and is never sent to a thread that was
answered, bounced, suppressed or only ever sent as a dry run.

Layering: db + outreach (drafter, suppress).
"""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlmodel import col, func, select

from jobhunter import CONFIG
from jobhunter.db import Company, Contact, Email, Reply, get_session, init_db, utcnow

log = logging.getLogger(__name__)

_O = CONFIG.get("outreach") or {}
AFTER_DAYS = int(_O.get("followup_after_days", 5))
MAX_FOLLOWUPS = int(_O.get("max_followups", 1))

TEMPLATE = (
    "Just following up on my note from {when} about {what}. I know inboxes get busy, so this is the "
    "last time I'll write. If a referral or a quick pointer to the right person isn't possible, no "
    "problem at all, and thank you for reading."
)


def _when(days: int) -> str:
    return "last week" if days >= 5 else "a few days ago"


def template_body(parent: Email, *, company: str | None, days: int) -> str:
    what = "a referral at " + company if company else "a referral"
    return TEMPLATE.format(when=_when(days), what=what)


def due(session, *, after_days: int | None = None, now=None) -> list[Email]:
    """Cold mail sent more than `after_days` ago with no reply, no follow-up yet, not
    bounced, not suppressed — and really sent (a dry-run thread is skipped when live)."""
    from jobhunter.outreach import sender, suppress

    if MAX_FOLLOWUPS < 1:
        return []
    days = AFTER_DAYS if after_days is None else after_days
    cutoff = (now or utcnow()) - timedelta(days=days)
    rows = session.exec(select(Email).where(
        Email.kind == "cold", Email.status == "sent", Email.followup_sent == False,  # noqa: E712
        col(Email.sent_at).is_not(None), col(Email.sent_at) < cutoff)).all()
    out = []
    for e in rows:
        if session.exec(select(func.count()).select_from(Reply).where(Reply.email_id == e.id)).one():
            continue
        if session.exec(select(Email.id).where(Email.parent_email_id == e.id)).first():
            continue
        if sender.SEND_MODE == "gmail" and str(e.gmail_thread_id or "").startswith("dryrun:"):
            continue
        contact = session.get(Contact, e.contact_id)
        company = session.get(Company, e.company_id)
        if suppress.blocked(session, contact=contact, company=company, to_email=e.to_email):
            continue
        out.append(e)
    return out


def _template_followup(email_id: int, *, now=None) -> dict:
    from jobhunter.outreach import drafter

    with get_session() as session:
        parent = session.get(Email, email_id)
        if parent is None or parent.status != "sent" or parent.followup_sent:
            return {"error": "parent not sent, or already followed up"}
        contact = session.get(Contact, parent.contact_id)
        company = session.get(Company, parent.company_id)
        days = ((now or utcnow()) - (parent.sent_at or parent.created_at)).days
        name = drafter.greeting_name(contact.name if contact else None, parent.to_email)
        body = drafter._compose(template_body(parent, company=company.name if company else None, days=days), name)
        subject = parent.subject if parent.subject.lower().startswith("re:") else f"Re: {parent.subject}"
        email = Email(contact_id=parent.contact_id, company_id=parent.company_id, job_id=parent.job_id,
                      to_email=parent.to_email, subject=subject[:180], body=body, kind="followup",
                      parent_email_id=parent.id, status="draft", gmail_thread_id=parent.gmail_thread_id,
                      candidate_id=parent.candidate_id,
                      address_confidence=parent.address_confidence,
                      review_flags='[{"kind": "template", "detail": "written from the fixed follow-up template (the model was unavailable)"}]',
                      expires_at=(now or utcnow()) + timedelta(days=int(_O.get("draft_expiry_days", 7))))
        if now is not None:
            email.created_at = now
        session.add(email)
        parent.followup_sent = True
        session.add(parent)
        session.commit()
        session.refresh(email)
        return {"email_id": email.id, "subject": email.subject, "template": True}


def queue(*, after_days: int | None = None, limit: int = 25, use_model: bool = True, now=None) -> dict:
    """Queue one follow-up per silent thread (into the review queue, never approved)."""
    from jobhunter.outreach import drafter

    init_db()
    with get_session() as session:
        ids = [e.id for e in due(session, after_days=after_days, now=now)][:limit]
    out = {"due": len(ids), "drafted": 0, "template": 0, "errors": [], "ids": []}
    for eid in ids:
        r = {"error": "model skipped"}
        if use_model:
            try:
                r = drafter.draft_followup(eid)
            except Exception as e:  # noqa: BLE001 — the model path failing falls to the template
                r = {"error": f"{type(e).__name__}: {str(e)[:120]}"}
        if r.get("error"):
            r = _template_followup(eid, now=now)
            if r.get("template"):
                out["template"] += 1
        if r.get("email_id"):
            out["drafted"] += 1
            out["ids"].append(r["email_id"])
        else:
            out["errors"].append({"email_id": eid, "error": r.get("error")})
    if out["due"]:
        log.info("follow-ups: %s", {k: v for k, v in out.items() if k != "ids"})
    return out


__all__ = ["queue", "due", "template_body", "AFTER_DAYS"]
