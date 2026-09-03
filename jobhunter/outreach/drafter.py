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
What we know they do there: {contact_evidence}

THE COMPANY (their own words; use only these facts):
{company_facts}

THE JOB THE CANDIDATE WANTS:
{job_title}{job_where}
Why it fits (from the matcher): {match_reasons}

Every proper noun and number you write must come from the facts above or the candidate's
background. If a fact is not there, do not write it.

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


DRAFT_EXPIRY_DAYS = int(_O.get("draft_expiry_days", 7))
CONTACT_COOLDOWN_DAYS = int(_O.get("contact_cooldown_days", 30))
COMPANY_COOLDOWN_DAYS = int(_O.get("company_cooldown_days", 30))


def _company_facts(company: Company | None, job: Job | None) -> tuple[str, dict]:
    """The company's own words the draft may use, and the same as structured evidence."""
    if company is None:
        return "(nothing known)", {}
    facts: dict = {}
    lines = []
    if company.description:
        facts["description"] = {"text": company.description, "source": company.website or "their site"}
        lines.append(f"- What they build: {company.description}")
    if company.story and (company.story_evidence or "").startswith("http"):
        snippet = company.story[:400]
        facts["origin"] = {"text": snippet, "source": company.story_evidence}
        lines.append(f"- How they started (from {company.story_evidence}): {snippet}")
    if company.funding_stage or company.funding_amount_usd_m:
        f = " ".join(p for p in [company.funding_stage, f"${company.funding_amount_usd_m:g}M" if company.funding_amount_usd_m else None] if p)
        facts["funding"] = {"text": f, "source": company.funding_evidence or "their pages"}
        lines.append(f"- Funding: {f}")
    if company.hiring_status in ("verified", "role_missing") and company.hiring_evidence:
        facts["hiring"] = {"text": company.hiring_evidence, "source": company.careers_url or company.hiring_claim_url}
        lines.append(f"- Hiring, on their own page: {company.hiring_evidence[:200]}")
    if company.hiring_claim_by and company.hiring_claim_source in ("x", "hn"):
        facts["hiring_post"] = {"text": f"{company.hiring_claim_by} posted that they are hiring", "source": company.hiring_claim_url}
        lines.append(f"- {company.hiring_claim_by} posted on {company.hiring_claim_source.upper()} that they are hiring")
    if job:
        where = job.location or ("remote" if job.remote else None)
        facts["role"] = {"text": job.title + (f" ({where})" if where else ""), "source": job.url}
        if job.remote_anywhere:
            facts["remote_anywhere"] = {"text": "the posting says the role can be done from anywhere", "source": job.url}
            lines.append("- The posting says the role can be done from anywhere")
    return ("\n".join(lines) or "(nothing beyond the name)"), facts


def _cooldown(session, contact_id: int, company_id: int, candidate_id: int) -> str | None:
    """MOTIV §6: never both of us to one person in a month; one ask per company per month."""
    from datetime import timedelta

    since = utcnow() - timedelta(days=CONTACT_COOLDOWN_DAYS)
    recent = session.exec(
        select(Email).where(Email.contact_id == contact_id, col(Email.status).in_(["approved", "sent", "replied"]),
                            col(Email.created_at) >= since)
    ).first()
    if recent:
        who = "you" if recent.candidate_id == candidate_id else "your teammate"
        return f"{who} already wrote to this person on {recent.created_at:%d %b} — {CONTACT_COOLDOWN_DAYS}-day cooldown"
    since_c = utcnow() - timedelta(days=COMPANY_COOLDOWN_DAYS)
    recent_c = session.exec(
        select(Email).where(Email.company_id == company_id, Email.candidate_id == candidate_id, Email.kind == "cold",
                            col(Email.status).in_(["approved", "sent", "replied"]), col(Email.created_at) >= since_c)
    ).first()
    if recent_c:
        return f"you already asked someone at this company on {recent_c.created_at:%d %b} — one ask per company per {COMPANY_COOLDOWN_DAYS} days"
    return None


def draft_for(contact_id: int, job_id: int | None = None, *, research: bool = True,
              candidate_id: int = 1, force: bool = False) -> dict:
    """Create one draft email in the review queue, gated: the facts it may use are
    recorded, and anything in the body that is not among them is flagged for review."""
    init_db()
    from datetime import timedelta

    from jobhunter import llm, resume
    from jobhunter.outreach import gates

    with get_session() as session:
        contact = session.get(Contact, contact_id)
        if contact is None or not contact.email:
            return {"error": "contact not found or has no email"}
        company = session.get(Company, contact.company_id)
        job = session.get(Job, job_id) if job_id else None
        if job is None:
            # fresher roles first, then remote-from-anywhere, then the matcher's score
            job = session.exec(
                select(Job).where(Job.company_id == contact.company_id)
                .order_by(col(Job.is_senior), col(Job.remote_anywhere).desc(), col(Job.match_score).desc())
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
        if not force:
            why = _cooldown(session, contact_id, contact.company_id, candidate_id)
            if why:
                return {"error": why}

        contact_name, contact_role, contact_email = contact.name, contact.role, contact.email
        contact_evidence = contact.role_evidence or ""
        contact_conf = contact.confidence
        company_name = company.name if company else "your company"
        company_id = contact.company_id
        notes = contact.research_notes
        job_title = job.title if job else "engineering roles"
        job_where = ""
        if job and (job.location or job.remote):
            job_where = f" ({job.location or 'remote'})"
        job_reasons = ""
        if job and job.match_reasons:
            try:
                job_reasons = "; ".join(json.loads(job.match_reasons).get("reasons", [])[:2])
            except (json.JSONDecodeError, AttributeError):
                job_reasons = ""
        job_db_id = job.id if job else None
        company_facts_text, company_facts = _company_facts(company, job)

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
    profile_text = resume.profile_summary(max_chars=1400)

    data = llm.chat_json(
        DRAFT_PROMPT.format(
            user_name=USER_NAME,
            user_headline=USER_HEADLINE,
            profile=profile_text,
            contact_name=contact_name or "there",
            contact_role=contact_role or "engineer",
            company=company_name,
            hook=brief.get("hook") or "(none — do not pretend to know their work)",
            shared_ground=brief.get("shared_ground") or "(none)",
            repos=repos,
            contact_evidence=contact_evidence or "(nothing beyond their address)",
            company_facts=company_facts_text,
            job_title=job_title,
            job_where=job_where,
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

    # ---- the gate: what the draft was allowed to say, and whether it stayed inside it
    corpus = "\n".join(filter(None, [
        profile_text, USER_NAME, USER_HEADLINE, " ".join(USER_LINKS), USER_EMAIL,
        contact_name, contact_role, contact_evidence, company_name, job_title, job_where,
        brief.get("hook"), brief.get("shared_ground"), repos, job_reasons,
        company_facts_text, " ".join(f["text"] for f in company_facts.values()),
    ]))
    verdict = gates.check(body, corpus=corpus, signature=signature(), address_confidence=contact_conf)
    evidence = {
        "person": {"evidence": contact_evidence or None, "hook": brief.get("hook"), "shared_ground": brief.get("shared_ground"), "repos": repos},
        "company": company_facts,
        "role": {"title": job_title, "where": job_where.strip(" ()") or None, "why_it_fits": job_reasons or None},
    }

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
            review_flags=json.dumps(verdict.as_list()),
            evidence=json.dumps(evidence, default=str),
            expires_at=utcnow() + timedelta(days=DRAFT_EXPIRY_DAYS),
            address_confidence=contact_conf,
            candidate_id=candidate_id,
        )
        session.add(email)
        session.commit()
        session.refresh(email)
        log.info("drafted email %s -> %s (%s) flags=%d", email.id, contact_email, company_name, len(verdict.flags))
        return {"email_id": email.id, "subject": email.subject, "to": email.to_email,
                "flags": verdict.as_list(), "words": verdict.words}


def draft_for_bets(limit: int = 10, *, candidate_id: int = 1) -> list[dict]:
    """One draft per company that is ready to ask: hiring proven, a fresher role, remote from
    anywhere (or India), and a lead with a usable address — the best lead, the best role."""
    from jobhunter import network

    init_db()
    atlas = network.build()
    bets = [c for c in atlas["companies"] if c["bet"]]
    out: list[dict] = []
    with get_session() as session:
        for c in bets:
            if len(out) >= limit:
                break
            company = session.get(Company, c["id"])
            people = session.exec(select(Contact).where(Contact.company_id == c["id"])).all()
            leads = network._reachable(company, people)
            leads.sort(key=lambda p: ((p.referral_rank or 9), p.confidence != "verified", p.name is None))
            if not leads:
                continue
            job = session.exec(
                select(Job).where(Job.company_id == c["id"])
                .order_by(col(Job.is_senior), col(Job.remote_anywhere).desc(), col(Job.match_score).desc())
            ).first()
            lead = leads[0]
            out.append({"company": company.name, "lead": lead.name, **draft_for(lead.id, job.id if job else None, candidate_id=candidate_id)})
    return out


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
