"""Email drafting — referral asks and follow-ups.

Every draft lands in the review queue, never the outbox. The prompt is written to
keep a small local model honest: it is given the candidate's real background and
told, repeatedly, that inventing anything is worse than being generic.
"""

from __future__ import annotations

import json
import logging
import re

from sqlmodel import col, select

from jobhunter import CONFIG
from jobhunter.db import Company, Contact, Email, Job, get_session, init_db, utcnow

log = logging.getLogger(__name__)

_O = CONFIG["outreach"]
USER_NAME = _O.get("user_name") or "(set outreach.user_name in config.yaml)"
USER_HEADLINE = _O.get("user_headline") or ""
USER_LINKS = _O.get("user_links") or []
USER_EMAIL = _O.get("user_email") or ""

DRAFT_SYSTEM = """You write short, plain, human referral-request emails from a student/early-career
engineer to someone who works at a company they want to join.

Absolute rules:
- Never invent a project, number, employer, or shared connection. Only use facts you are given.
- No flattery ("I'm a huge fan", "your incredible work"), no buzzwords, no "I hope this finds you well".
- No em-dashes. Short sentences. Contractions are fine.
- 90-130 words in the body. A busy engineer should be able to read it in 20 seconds.
- Ask for exactly one thing, and make it easy to say yes or no to.
- Do not attach or promise attachments; the resume link goes in the signature.

Reply with JSON only."""

DRAFT_PROMPT = """WRITE AN EMAIL.

FROM (the candidate):
Name: {user_name}
Headline: {user_headline}
Background (only use what's here):
{profile}

TO:
Name: {contact_name}
Their role: {contact_role}
Company: {company}

RESEARCH ON THEM (may be empty — if so, write a good email without pretending to know their work):
Hook: {hook}
Shared ground: {shared_ground}
Their notable repos: {repos}

THE JOB THE CANDIDATE WANTS:
{job_title}
Why it fits (from the matcher): {match_reasons}

ASK: a referral for this role, or if they'd rather not, 10 minutes of advice.

Structure the body as:
1. One line saying who you are and why you're writing to THEM specifically.
2. One or two lines connecting your real, specific experience to what this team does.
3. The ask, phrased so "no" is easy.
4. Sign-off.

Reply with exactly:
{{"subject": "<6-9 words, specific, no clickbait, no 'Re:'>", "body": "<the email body, plain text, no signature block>"}}"""

FOLLOWUP_SYSTEM = """You write one short, polite, low-pressure follow-up to an unanswered email.
It must be 40-60 words, must not guilt the recipient, must not repeat the original pitch at
length, and must make clear this is the last time you'll write. No em-dashes. JSON only."""

FOLLOWUP_PROMPT = """The candidate {user_name} sent this {days} days ago to {contact_name} at {company},
and got no reply:

SUBJECT: {subject}
---
{body}
---

Write a brief follow-up that sits on the same thread. Add one small new piece of value or
context if you can do so truthfully from the original email; otherwise just be brief.

Reply with exactly:
{{"body": "<the follow-up body, plain text, no signature block>"}}"""


def signature() -> str:
    lines = [USER_NAME]
    if USER_HEADLINE:
        lines.append(USER_HEADLINE)
    if USER_LINKS:
        lines.append(" | ".join(USER_LINKS))
    if USER_EMAIL:
        lines.append(USER_EMAIL)
    return "\n".join(lines)


_EM_DASH = re.compile(r"\s*[—–]\s*")
_GREETING_DUP = re.compile(r"^(hi|hey|hello|dear)\b", re.I)


def _clean_body(body: str, contact_name: str | None) -> str:
    body = _EM_DASH.sub(", ", body or "").strip()
    # models like to append their own sign-off; we add a real one
    body = re.sub(
        r"\n+\s*(best|thanks|regards|cheers|sincerely|best regards|thank you)[,!.]?\s*\n.*$",
        "",
        body,
        flags=re.I | re.DOTALL,
    ).strip()
    if not _GREETING_DUP.match(body):
        first = (contact_name or "").split()[0] if contact_name else "there"
        body = f"Hi {first},\n\n{body}"
    return body


def _compose(body: str, contact_name: str | None) -> str:
    return f"{_clean_body(body, contact_name)}\n\nThanks,\n{signature()}"


def draft_for(contact_id: int, job_id: int | None = None, *, research: bool = True) -> dict:
    """Create one draft email in the review queue."""
    init_db()
    from jobhunter import llm, resume

    with get_session() as session:
        contact = session.get(Contact, contact_id)
        if contact is None or not contact.email:
            return {"error": "contact not found or has no email"}
        company = session.get(Company, contact.company_id)
        job = session.get(Job, job_id) if job_id else None
        if job is None:
            job = session.exec(
                select(Job)
                .where(Job.company_id == contact.company_id, col(Job.match_score).is_not(None))
                .order_by(col(Job.match_score).desc())
            ).first()

        dup = session.exec(
            select(Email).where(
                Email.contact_id == contact_id,
                Email.kind == "cold",
                col(Email.status).in_(["draft", "approved", "sent", "replied"]),
            )
        ).first()
        if dup:
            return {"error": "already drafted for this contact", "email_id": dup.id}

        contact_name, contact_role, contact_email = contact.name, contact.role, contact.email
        company_name = company.name if company else "your company"
        company_id = contact.company_id
        notes = contact.research_notes
        job_title = job.title if job else "engineering roles"
        job_reasons = ""
        if job and job.match_reasons:
            try:
                job_reasons = "; ".join(json.loads(job.match_reasons).get("reasons", [])[:2])
            except (json.JSONDecodeError, AttributeError):
                job_reasons = ""
        job_db_id = job.id if job else None

    brief: dict = {}
    if notes:
        try:
            brief = json.loads(notes)
        except json.JSONDecodeError:
            brief = {}
    if research and not brief.get("hook"):
        import asyncio

        from jobhunter.outreach.researcher import research_contact

        brief = asyncio.run(research_contact(contact_id, job_db_id)) or {}

    repos = ", ".join(r.get("name", "") for r in (brief.get("repos") or [])[:3]) or "(none found)"

    data = llm.chat_json(
        DRAFT_PROMPT.format(
            user_name=USER_NAME,
            user_headline=USER_HEADLINE,
            profile=resume.profile_summary(max_chars=1400),
            contact_name=contact_name or "there",
            contact_role=contact_role or "engineer",
            company=company_name,
            hook=brief.get("hook") or "(none — do not pretend to know their work)",
            shared_ground=brief.get("shared_ground") or "(none)",
            repos=repos,
            job_title=job_title,
            match_reasons=job_reasons or "(not scored)",
        ),
        DRAFT_SYSTEM,
        temperature=0.6,
        num_predict=600,
        alias="writer",         # a human reads this one; it is worth the better model
        purpose="draft",
        default=None,
    )
    if not isinstance(data, dict) or not data.get("body"):
        return {"error": "LLM produced no usable draft"}

    subject = _EM_DASH.sub(", ", str(data.get("subject") or f"Referral for {job_title} at {company_name}"))
    body = _compose(str(data["body"]), contact_name)

    with get_session() as session:
        email = Email(
            contact_id=contact_id,
            company_id=company_id,
            job_id=job_db_id,
            to_email=contact_email,
            subject=subject[:180],
            body=body,
            kind="cold",
            status="draft",
        )
        session.add(email)
        session.commit()
        session.refresh(email)
        log.info("drafted email %s -> %s (%s)", email.id, contact_email, company_name)
        return {"email_id": email.id, "subject": email.subject, "to": email.to_email}


def draft_followup(email_id: int) -> dict:
    """Queue a single polite follow-up on an unanswered thread."""
    init_db()
    from jobhunter import llm

    with get_session() as session:
        parent = session.get(Email, email_id)
        if parent is None or parent.status != "sent":
            return {"error": "parent email not found or not sent"}
        if parent.followup_sent:
            return {"error": "follow-up already queued for this thread"}
        contact = session.get(Contact, parent.contact_id)
        company = session.get(Company, parent.company_id)
        days = (utcnow() - (parent.sent_at or parent.created_at)).days
        contact_name = contact.name if contact else None
        payload = {
            "user_name": USER_NAME,
            "days": days,
            "contact_name": contact_name or "there",
            "company": company.name if company else "the company",
            "subject": parent.subject,
            "body": parent.body,
        }
        parent_to, parent_subject = parent.to_email, parent.subject
        contact_id, company_id, job_id = parent.contact_id, parent.company_id, parent.job_id
        thread_id = parent.gmail_thread_id

    data = llm.chat_json(
        FOLLOWUP_PROMPT.format(**payload), FOLLOWUP_SYSTEM, temperature=0.5, num_predict=300,
        alias="writer", purpose="followup", default=None,
    )
    if not isinstance(data, dict) or not data.get("body"):
        return {"error": "LLM produced no usable follow-up"}

    with get_session() as session:
        email = Email(
            contact_id=contact_id,
            company_id=company_id,
            job_id=job_id,
            to_email=parent_to,
            subject=parent_subject if parent_subject.lower().startswith("re:") else f"Re: {parent_subject}",
            body=_compose(str(data["body"]), contact_name),
            kind="followup",
            parent_email_id=email_id,
            status="draft",
            gmail_thread_id=thread_id,
        )
        session.add(email)
        parent = session.get(Email, email_id)
        parent.followup_sent = True   # mark now so we never queue a second one
        session.add(parent)
        session.commit()
        session.refresh(email)
        log.info("drafted follow-up %s for email %s", email.id, email_id)
        return {"email_id": email.id, "subject": email.subject}


def queue_followups(after_days: int | None = None, limit: int = 10) -> list[dict]:
    """Find sent emails that have gone quiet and queue one follow-up each."""
    init_db()
    from datetime import timedelta

    days = after_days if after_days is not None else int(_O.get("followup_after_days", 5))
    if int(_O.get("max_followups", 1)) < 1:
        return []
    cutoff = utcnow() - timedelta(days=days)

    with get_session() as session:
        candidates = session.exec(
            select(Email).where(
                Email.kind == "cold",
                Email.status == "sent",
                Email.followup_sent == False,  # noqa: E712
                col(Email.sent_at).is_not(None),
                col(Email.sent_at) < cutoff,
            ).limit(limit)
        ).all()
        ids = [e.id for e in candidates]

    return [draft_followup(eid) for eid in ids]


def draft_for_high_matches(limit: int = 5, *, per_company: int = 2) -> list[dict]:
    """Draft referral asks to the best contacts at companies with high-match jobs."""
    init_db()
    out: list[dict] = []
    with get_session() as session:
        company_ids = session.exec(
            select(Job.company_id).where(Job.status == "high_match", col(Job.company_id).is_not(None)).distinct()
        ).all()

        targets: list[tuple[int, int]] = []
        for cid in company_ids:
            contacts = session.exec(
                select(Contact).where(Contact.company_id == cid, col(Contact.email).is_not(None))
            ).all()
            # verified addresses first, then anyone we have a name for
            contacts.sort(key=lambda c: (c.confidence != "verified", c.name is None))
            already = {
                e.contact_id for e in session.exec(select(Email).where(Email.company_id == cid)).all()
            }
            picked = [c for c in contacts if c.id not in already][:per_company]
            best_job = session.exec(
                select(Job).where(Job.company_id == cid, Job.status == "high_match")
                .order_by(col(Job.match_score).desc())
            ).first()
            for c in picked:
                targets.append((c.id, best_job.id if best_job else None))

    for contact_id, job_id in targets[:limit]:
        out.append(draft_for(contact_id, job_id))
    return out
