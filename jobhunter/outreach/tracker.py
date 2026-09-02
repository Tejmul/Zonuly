"""Reply tracking — poll Gmail threads for responses and classify them.

Classification drives the funnel board and the notification, so it errs toward
NEUTRAL rather than guessing POSITIVE: a false "they said yes!" is worse than a
reply the user reads themselves.
"""

from __future__ import annotations

import base64
import logging
import re
from typing import Any

from sqlmodel import col, func, select

from jobhunter.db import Email, Reply, get_session, init_db, utcnow
from jobhunter.outreach import gmail

log = logging.getLogger(__name__)

CLASSIFY_SYSTEM = """You classify replies to a job-seeker's cold referral request.
You are conservative: if the reply is ambiguous, automated, or you are unsure, choose NEUTRAL.
JSON only."""

CLASSIFY_PROMPT = """The candidate asked this person for a referral. Here is their reply:

FROM: {sender}
---
{body}
---

Classify it:
- "positive": they agreed to refer, offered a call, asked for a resume/details, or made an intro
- "negative": they declined, said they can't help, or said the candidate isn't a fit
- "closed": the role is filled, hiring is frozen, they've left the company, or it's an
  auto-reply saying they're unreachable
- "neutral": out-of-office, a bounce, a mailing-list bot, a holding reply, or anything unclear

Reply with exactly:
{{"sentiment": "positive"|"negative"|"closed"|"neutral", "reason": "<one short sentence>", "action": "<what the candidate should do next, one short sentence>"}}"""

_QUOTE = re.compile(
    r"\n\s*(On .{0,80}wrote:|-{2,}\s*Original Message|_{5,}|From:\s.+\nSent:)", re.I
)


def _decode(data: str | None) -> str:
    if not data:
        return ""
    try:
        return base64.urlsafe_b64decode(data.encode()).decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001 — malformed part
        return ""


def _extract_body(payload: dict[str, Any]) -> str:
    """Walk the MIME tree, preferring text/plain."""
    if not payload:
        return ""
    mime = payload.get("mimeType", "")
    body = payload.get("body") or {}

    if mime == "text/plain" and body.get("data"):
        return _decode(body["data"])

    parts = payload.get("parts") or []
    for part in parts:                                  # plain text first
        if part.get("mimeType") == "text/plain":
            text = _extract_body(part)
            if text:
                return text
    for part in parts:                                  # then anything nested
        text = _extract_body(part)
        if text:
            return text

    if mime == "text/html" and body.get("data"):
        from jobhunter.scrapers.base import html_to_text

        return html_to_text(_decode(body["data"]))
    return ""


def _strip_quoted(text: str) -> str:
    """Drop the quoted original so the classifier judges only what they wrote."""
    m = _QUOTE.search(text)
    if m:
        text = text[: m.start()]
    lines = [ln for ln in text.splitlines() if not ln.strip().startswith(">")]
    return "\n".join(lines).strip()


def classify(body: str, sender: str) -> dict:
    from jobhunter import llm

    data = llm.chat_json(
        CLASSIFY_PROMPT.format(sender=sender, body=body[:3000]),
        CLASSIFY_SYSTEM,
        temperature=0.0,
        num_predict=200,
        alias="cheap",
        purpose="reply-classify",
        default=None,
    )
    valid = {"positive", "negative", "closed", "neutral"}
    if not isinstance(data, dict) or data.get("sentiment") not in valid:
        return {"sentiment": "neutral", "reason": "could not classify automatically", "action": "read it yourself"}
    return data


def poll(limit: int = 50) -> dict:
    """Check every sent thread for new inbound messages."""
    init_db()
    stats = {"threads_checked": 0, "new_replies": 0, "positive": 0, "negative": 0, "closed": 0, "neutral": 0}

    try:
        svc = gmail.service()
        me = svc.users().getProfile(userId="me").execute().get("emailAddress", "").lower()
    except gmail.GmailNotConfigured as e:
        return {**stats, "error": str(e)}
    except Exception as e:  # noqa: BLE001
        return {**stats, "error": f"Gmail unavailable: {e}"}

    with get_session() as session:
        sent = session.exec(
            select(Email).where(
                col(Email.status).in_(["sent", "replied"]), col(Email.gmail_thread_id).is_not(None)
            ).limit(limit)
        ).all()
        threads = [(e.id, e.gmail_thread_id, e.to_email) for e in sent]
        known = {
            r.gmail_message_id for r in session.exec(
                select(Reply).where(col(Reply.gmail_message_id).is_not(None))
            ).all()
        }

    for email_id, thread_id, to_email in threads:
        stats["threads_checked"] += 1
        try:
            thread = svc.users().threads().get(userId="me", id=thread_id, format="full").execute()
        except Exception as e:  # noqa: BLE001 — deleted thread, transient API error
            log.debug("thread %s unreadable: %s", thread_id, e)
            continue

        for msg in thread.get("messages") or []:
            msg_id = msg.get("id")
            if not msg_id or msg_id in known:
                continue
            headers = {h["name"].lower(): h["value"] for h in (msg.get("payload") or {}).get("headers", [])}
            sender = (headers.get("from") or "").lower()
            if me and me in sender:
                continue  # our own message in the thread

            body = _strip_quoted(_extract_body(msg.get("payload") or {}))
            if not body:
                continue

            verdict = classify(body, sender)
            sentiment = verdict["sentiment"]

            with get_session() as session:
                session.add(
                    Reply(
                        email_id=email_id,
                        gmail_message_id=msg_id,
                        from_addr=headers.get("from") or to_email,
                        body=body[:8000],
                        sentiment=sentiment,
                        sentiment_reason=f"{verdict.get('reason', '')} | next: {verdict.get('action', '')}"[:400],
                        received_at=utcnow(),
                    )
                )
                email = session.get(Email, email_id)
                if email and email.status == "sent":
                    email.status = "replied"
                    session.add(email)
                session.commit()

            known.add(msg_id)
            stats["new_replies"] += 1
            stats[sentiment] += 1
            log.info("reply on thread %s from %s -> %s", thread_id, sender, sentiment)

    log.info("reply poll: %s", stats)
    return stats


def funnel() -> dict:
    """Counts for the Overview funnel: drafted -> approved -> sent -> replied -> positive."""
    init_db()
    with get_session() as session:
        def count_emails(*where) -> int:
            return session.exec(select(func.count()).select_from(Email).where(*where)).one()

        def count_replies(sentiment: str) -> int:
            return session.exec(
                select(func.count()).select_from(Reply).where(Reply.sentiment == sentiment)
            ).one()

        return {
            "drafted": count_emails(Email.status == "draft"),
            "approved": count_emails(Email.status == "approved"),
            "rejected": count_emails(Email.status == "rejected"),
            "failed": count_emails(Email.status == "failed"),
            "sent": count_emails(col(Email.status).in_(["sent", "replied"])),
            "replied": count_emails(Email.status == "replied"),
            "positive": count_replies("positive"),
            "negative": count_replies("negative"),
            "closed": count_replies("closed"),
            "neutral": count_replies("neutral"),
        }
