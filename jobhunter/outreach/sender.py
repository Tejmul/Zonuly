"""Gmail sender with the guardrails that keep the user's account alive.

Hard daily cap, a send window, randomized spacing, plain text only, and a
per-contact one-thread rule. Nothing is sent that a human hasn't approved.
"""

from __future__ import annotations

import base64
import logging
import random
import time
from datetime import datetime, timezone
from email.message import EmailMessage

from sqlmodel import col, func, select

from jobhunter import CONFIG, ROOT
from jobhunter.db import Email, get_session, init_db, utcnow
from jobhunter.outreach import gmail, ledger

log = logging.getLogger(__name__)

_O = CONFIG["outreach"]
DAILY_CAP = int(_O.get("daily_send_cap", 25))
SEND_WINDOW = _O.get("send_window", [10, 19])
STAGGER_SECONDS = _O.get("stagger_seconds", [45, 210])
# dryrun: the whole loop runs (ledger, cap, window, statuses) but the mail is written to
# outbox/ instead of leaving the machine. gmail: the real thing, after gmail-auth.
SEND_MODE = str(_O.get("send_mode", "dryrun")).lower()
OUTBOX = ROOT / "outbox"


def sent_today() -> int:
    """Sends since local midnight, counted from the DB rather than a counter that can drift."""
    midnight_local = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    start = midnight_local.astimezone(timezone.utc).replace(tzinfo=None)  # DB stores naive UTC
    with get_session() as session:
        return session.exec(
            select(func.count()).select_from(Email).where(
                Email.status == "sent", col(Email.sent_at) >= start
            )
        ).one()


def remaining_today(candidate_id: int = 1) -> int:
    # the ledger is the truth (a slot is taken inside the send); the DB count is the cross-check
    return min(ledger.status(candidate_id)["left"], max(0, DAILY_CAP - sent_today()))


def _dryrun_send(email_id: int, to: str, subject: str, body: str) -> dict:
    """Write the mail to outbox/ and return a fake Gmail result, so the loop completes."""
    OUTBOX.mkdir(exist_ok=True)
    path = OUTBOX / f"{email_id:05d}.eml"
    msg = EmailMessage()
    msg["To"] = to
    msg["Subject"] = subject
    msg["X-ZoNuLy-Mode"] = "dryrun"
    msg.set_content(body)
    path.write_bytes(msg.as_bytes())
    return {"id": f"dryrun:{email_id}", "threadId": f"dryrun:{email_id}", "path": str(path)}


def in_send_window(now: datetime | None = None) -> bool:
    now = now or datetime.now()
    lo, hi = int(SEND_WINDOW[0]), int(SEND_WINDOW[1])
    return lo <= now.hour < hi


def _build_message(to: str, subject: str, body: str, thread_id: str | None) -> dict:
    msg = EmailMessage()
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)   # plain text only: HTML mail from a personal account reads as bulk
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    payload = {"raw": raw}
    if thread_id:
        payload["threadId"] = thread_id
    return payload


def send_email(email_id: int, *, ignore_window: bool = False) -> dict:
    """Send one approved email. Refuses if the cap is hit or we're outside the send window."""
    init_db()

    with get_session() as session:
        email = session.get(Email, email_id)
        if email is None:
            return {"error": f"no email {email_id}"}
        if email.status == "sent":
            return {"error": "already sent", "email_id": email_id}
        if email.status != "approved":
            return {"error": f"email is '{email.status}', only approved emails are sent"}
        to, subject, body = email.to_email, email.subject, email.body
        thread_id = email.gmail_thread_id
        candidate_id = email.candidate_id
        guessed = (email.address_confidence or "verified") != "verified"

    if not ignore_window and not in_send_window():
        return {"error": f"outside send window {SEND_WINDOW[0]}:00-{SEND_WINDOW[1]}:00 local"}

    # the slot is taken BEFORE the mail leaves — 25 a day, spent irreversibly (MOTIV §6)
    ok, why = ledger.reserve(candidate_id, guessed=guessed)
    if not ok:
        return {"error": why}

    try:
        if SEND_MODE == "gmail":
            svc = gmail.service()
            result = svc.users().messages().send(userId="me", body=_build_message(to, subject, body, thread_id)).execute()
        else:
            result = _dryrun_send(email_id, to, subject, body)
    except gmail.GmailNotConfigured as e:
        ledger.release(candidate_id, guessed=guessed)
        return {"error": str(e)}
    except Exception as e:  # noqa: BLE001 — API errors are reported, not raised, so the queue keeps moving
        log.exception("send failed for email %s", email_id)
        ledger.release(candidate_id, guessed=guessed)
        with get_session() as session:
            email = session.get(Email, email_id)
            email.status = "failed"
            email.error = str(e)[:500]
            session.add(email)
            session.commit()
        return {"error": str(e)[:300], "email_id": email_id}

    with get_session() as session:
        email = session.get(Email, email_id)
        email.status = "sent"
        email.sent_at = utcnow()
        email.gmail_message_id = result.get("id")
        email.gmail_thread_id = result.get("threadId") or thread_id
        email.error = None
        session.add(email)
        session.commit()

    log.info("sent email %s to %s (thread %s, mode %s)", email_id, to, result.get("threadId"), SEND_MODE)
    return {"sent": True, "email_id": email_id, "to": to, "thread_id": result.get("threadId"),
            "mode": SEND_MODE, **({"path": result["path"]} if result.get("path") else {})}


def send_approved(limit: int | None = None, *, ignore_window: bool = False, stagger: bool = True) -> dict:
    """Drain the approved queue up to the daily cap, spacing sends out."""
    init_db()
    budget = remaining_today()
    if budget <= 0:
        return {"sent": 0, "skipped": "daily cap reached", "cap": DAILY_CAP}
    if not ignore_window and not in_send_window():
        return {"sent": 0, "skipped": f"outside send window {SEND_WINDOW[0]}:00-{SEND_WINDOW[1]}:00"}

    n = min(budget, limit or budget)
    with get_session() as session:
        ids = [
            e.id for e in session.exec(
                select(Email).where(Email.status == "approved").order_by(col(Email.approved_at)).limit(n)
            ).all()
        ]

    results, sent = [], 0
    for i, eid in enumerate(ids):
        r = send_email(eid, ignore_window=ignore_window)
        results.append(r)
        if r.get("sent"):
            sent += 1
        if stagger and i < len(ids) - 1 and r.get("sent"):
            # human-ish spacing; a burst of identical-looking mail is what gets flagged
            time.sleep(random.uniform(float(STAGGER_SECONDS[0]), float(STAGGER_SECONDS[1])))

    return {"sent": sent, "attempted": len(ids), "remaining_today": remaining_today(), "results": results}


def approve(email_id: int, *, subject: str | None = None, body: str | None = None) -> dict:
    """Approve a draft, optionally with the user's edits from the review queue."""
    init_db()
    with get_session() as session:
        email = session.get(Email, email_id)
        if email is None:
            return {"error": f"no email {email_id}"}
        if email.status not in ("draft", "rejected"):
            return {"error": f"cannot approve an email that is '{email.status}'"}
        if email.expires_at and email.expires_at < utcnow():
            return {"error": "this draft is stale (older than the expiry window) — re-draft it; the facts may have changed"}
        if subject is not None:
            email.subject = subject[:180]
        if body is not None:
            email.body = body
        email.status = "approved"
        email.approved_at = utcnow()
        email.error = None
        session.add(email)
        session.commit()
    return {"approved": True, "email_id": email_id}


def reject(email_id: int) -> dict:
    init_db()
    with get_session() as session:
        email = session.get(Email, email_id)
        if email is None:
            return {"error": f"no email {email_id}"}
        if email.status == "sent":
            return {"error": "cannot reject an email that was already sent"}
        email.status = "rejected"
        session.add(email)
        session.commit()
    return {"rejected": True, "email_id": email_id}


def quota() -> dict:
    return {
        "daily_cap": DAILY_CAP,
        "sent_today": sent_today(),
        "remaining_today": remaining_today(),
        "send_window": SEND_WINDOW,
        "in_window": in_send_window(),
        "send_mode": SEND_MODE,
        "ledger": ledger.status(),
        "gmail": gmail.status(),
    }
