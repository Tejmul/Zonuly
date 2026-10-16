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

from jobhunter import CONFIG, ROOT, guardrails
from jobhunter.db import Contact, Email, Reply, get_session, init_db, utcnow
from jobhunter.outreach import gmail, ledger

log = logging.getLogger(__name__)

_O = CONFIG["outreach"]
DAILY_CAP = int(_O.get("daily_send_cap", 25))
SEND_WINDOW = _O.get("send_window", [10, 19])
STAGGER_SECONDS = _O.get("stagger_seconds", [45, 210])
# dryrun: the whole loop runs (ledger, cap, window, statuses) but the mail is written to
# outbox/ instead of leaving the machine. gmail: the real thing, after gmail-auth.
# "live" is accepted as another name for gmail (2026-10-14).
SEND_MODE = {"live": "gmail"}.get(str(_O.get("send_mode", "dryrun")).lower(), str(_O.get("send_mode", "dryrun")).lower())
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
    # the ledger is the truth (a slot is taken inside the send, its cap is the warm-up
    # quota); the DB count is the cross-check
    return min(ledger.status(candidate_id)["left"], max(0, DAILY_CAP - sent_today()))


def _dryrun_send(email_id: int, to: str, subject: str, body: str, *, in_reply_to: str | None = None,
                 thread_id: str | None = None) -> dict:
    """Write the mail to outbox/ and return a fake Gmail result, so the loop completes.
    The .eml carries a real-looking Message-ID and, for a follow-up, In-Reply-To /
    References — so the rehearsal exercises the threading the live send uses."""
    import uuid

    OUTBOX.mkdir(exist_ok=True)
    path = OUTBOX / f"{email_id:05d}.eml"
    msg = EmailMessage()
    msg["From"] = gmail.from_header()   # the rehearsal shows the same identity the real send will
    msg["To"] = to
    msg["Subject"] = subject
    msg["Message-ID"] = f"<dryrun-{email_id}-{uuid.uuid4().hex[:10]}@zonuly.local>"
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    msg["X-ZoNuLy-Mode"] = "dryrun"
    msg.set_content(body)
    path.write_bytes(msg.as_bytes())
    return {"id": msg["Message-ID"], "threadId": thread_id or f"dryrun:{email_id}", "path": str(path)}


def in_send_window(now: datetime | None = None) -> bool:
    """Inside the hours AND on a sending day (outreach.send_days)."""
    from jobhunter.outreach import warmup

    now = now or datetime.now()
    lo, hi = int(SEND_WINDOW[0]), int(SEND_WINDOW[1])
    return lo <= now.hour < hi and warmup.is_send_day(now)


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


def _referrer_problem(session, email: Email) -> str | None:
    """A cold ask may only go to a confirmed-current referrer (guardrails.affiliation), to
    someone not suppressed (bounced, opted out, do-not-contact), and one company at a time
    (suppress.cooldown). Follow-ups answer a thread that already exists: they are not
    re-judged as referrers, but a suppression stops them too."""
    from jobhunter.db import Company
    from jobhunter.outreach import suppress

    contact = session.get(Contact, email.contact_id)
    company = session.get(Company, email.company_id)
    why = suppress.blocked(session, contact=contact, company=company, to_email=email.to_email)
    if why:
        return why
    if email.kind != "cold":
        parent = session.get(Email, email.parent_email_id) if email.parent_email_id else None
        if parent is not None and parent.status not in ("sent",):
            return f"the mail this follows up is '{parent.status}' — a follow-up goes only on a silent thread"
        if parent is not None and session.exec(select(func.count()).select_from(Reply).where(
                Reply.email_id == parent.id)).one():
            return "they already replied on this thread — no follow-up"
        if SEND_MODE == "gmail" and parent is not None and (
                str(parent.gmail_thread_id or "").startswith("dryrun:") or "@zonuly.local" in str(parent.gmail_message_id or "")):
            return "the mail this follows up only went out as a dry run — nothing to follow up"
        return None
    why = suppress.cooldown(session, email)
    if why:
        return why
    if contact is None:
        return "the contact this draft was written to no longer exists"
    if not guardrails.is_ready(contact):
        return (f"{contact.name or email.to_email} is not a confirmed-current referrer "
                f"({contact.affiliation or 'unchecked'}: {contact.affiliation_evidence or 'no evidence on record'}) "
                "— re-check them or reject the draft")
    return None


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
        if not (email.body or "").strip() or not (email.subject or "").strip():
            return {"error": "empty subject or body — nothing is sent", "email_id": email_id}
        why = _referrer_problem(session, email)
        if why:
            return {"error": why, "email_id": email_id}
        if email.kind == "cold":
            from jobhunter.outreach import suppress

            # the opt-out line rides on every cold mail (added at approval; this is the net)
            if suppress.OPT_OUT_LINE and not suppress.has_opt_out(email.body) and _wants_opt_out(email):
                email.body = suppress.with_opt_out(email.body)
                session.add(email)
                session.commit()
        to, subject, body = email.to_email, email.subject, email.body
        thread_id = email.gmail_thread_id
        candidate_id = email.candidate_id
        guessed = (email.address_confidence or "verified") != "verified"
        # a follow-up or reschedule threads under the mail it answers
        parent = session.get(Email, email.parent_email_id) if email.parent_email_id else None
        in_reply_to = parent.gmail_message_id if parent and (parent.gmail_message_id or "").startswith("<") else None
        if guessed and email.kind != "cold" and _delivered(parent):
            guessed = False   # the address took a mail days ago without bouncing: it is not a guess any more

    if not ignore_window and not in_send_window():
        return {"error": f"outside send window {SEND_WINDOW[0]}:00-{SEND_WINDOW[1]}:00 local on a sending day"}

    if guessed:
        from jobhunter.outreach import suppress

        with get_session() as session:
            why = suppress.brake(session)
        if why:
            return {"error": why, "email_id": email_id}

    # claim it: two senders (the send agent, a click in the dashboard) never send one mail twice
    if not _claim(email_id):
        return {"error": "another sender is already sending this email", "email_id": email_id}

    # the slot is taken BEFORE the mail leaves — 25 a day, spent irreversibly (MOTIV §6)
    ok, why = ledger.reserve(candidate_id, guessed=guessed)
    if not ok:
        _unclaim(email_id)
        return {"error": why}

    try:
        if SEND_MODE == "gmail" and gmail.AUTH == "app_password":
            result = gmail.send(to, subject, body, in_reply_to=in_reply_to)
        elif SEND_MODE == "gmail":
            svc = gmail.service()
            result = svc.users().messages().send(userId="me", body=_build_message(to, subject, body, thread_id)).execute()
        else:
            result = _dryrun_send(email_id, to, subject, body, in_reply_to=in_reply_to, thread_id=thread_id)
    except gmail.GmailNotConfigured as e:
        ledger.release(candidate_id, guessed=guessed)
        _unclaim(email_id)
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
        local = datetime.now()
        email.send_hour, email.send_weekday = local.hour, local.strftime("%a")   # for learn.py
        email.gmail_message_id = result.get("id")
        email.gmail_thread_id = result.get("threadId") or thread_id
        email.error = None
        session.add(email)
        session.commit()

    log.info("sent email %s to %s (thread %s, mode %s)", email_id, to, result.get("threadId"), SEND_MODE)
    return {"sent": True, "email_id": email_id, "to": to, "thread_id": result.get("threadId"),
            "mode": SEND_MODE, **({"path": result["path"]} if result.get("path") else {})}


def _delivered(parent: Email | None, days: int = 2) -> bool:
    """The parent mail went out at least `days` ago and did not bounce."""
    from datetime import timedelta

    return bool(parent and parent.status in ("sent", "replied") and parent.sent_at
                and utcnow() - parent.sent_at >= timedelta(days=days))


def _claim(email_id: int) -> bool:
    """approved -> sending, atomically; False when someone else got there first."""
    from sqlalchemy import text

    with get_session() as session:
        r = session.exec(text("update email set status='sending' where id=:i and status='approved'").bindparams(i=email_id))
        session.commit()
        return (r.rowcount or 0) == 1


def _unclaim(email_id: int) -> None:
    from sqlalchemy import text

    with get_session() as session:
        session.exec(text("update email set status='approved' where id=:i and status='sending'").bindparams(i=email_id))
        session.commit()


def _queue(session, limit: int) -> list[Email]:
    """Approved mail in send order: follow-ups first (they are due on a day), then cold
    asks in the order they were approved."""
    rows = session.exec(select(Email).where(Email.status == "approved")
                        .order_by(col(Email.approved_at), col(Email.id))).all()
    rows.sort(key=lambda e: (e.kind == "cold",))
    return rows[:limit]


def send_due(now: datetime | None = None, *, stagger: bool = True, max_per_run: int | None = None) -> dict:
    """The send agent's one run (every few minutes, launchd): send what today's jittered
    slots say is due — the warm-up quota spread across the window — and no more than
    max_per_run at once. Approved mail only; outside the window or on a non-sending day it
    does nothing. A guessed address waits while the guessed cap (or the bounce brake) is
    spent; a verified one behind it goes instead."""
    from jobhunter.outreach import suppress, warmup

    init_db()
    now = now or datetime.now()
    if not in_send_window(now):
        return {"sent": 0, "skipped": "outside the send window / not a sending day"}
    led = ledger.status()
    due = warmup.due(now)
    want = min(max(0, due - led["used"]), led["left"], max_per_run or warmup.MAX_PER_RUN)
    out = {"sent": 0, "due_slots": due, "used": led["used"], "quota": led["cap"], "results": []}
    if want <= 0:
        out["skipped"] = "nothing due yet" if led["left"] else "today's quota is spent"
        return out
    with get_session() as session:
        queue = _queue(session, 200)
        guessed_paused = suppress.brake(session)
        ids = [(e.id, (e.address_confidence or "verified") != "verified" and not (
            e.kind != "cold" and _delivered(session.get(Email, e.parent_email_id) if e.parent_email_id else None)))
            for e in queue]
    guessed_left = 0 if guessed_paused else led["guessed_left"]
    for eid, guessed in ids:
        if out["sent"] >= want:
            break
        if guessed and guessed_left <= 0:
            continue
        r = send_email(eid)
        out["results"].append(r)
        if r.get("sent"):
            out["sent"] += 1
            guessed_left -= 1 if guessed else 0
            if stagger and out["sent"] < want:
                time.sleep(random.uniform(float(STAGGER_SECONDS[0]), float(STAGGER_SECONDS[1])))
    out["remaining_today"] = remaining_today()
    return out


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
        ids = [e.id for e in _queue(session, n)]

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
        if not (body if body is not None else email.body or "").strip() or not (subject if subject is not None else email.subject or "").strip():
            return {"error": "this draft has no subject or body — the personalisation contract was not filled; re-draft it"}
        if email.expires_at and email.expires_at < utcnow():
            return {"error": "this draft is stale (older than the expiry window) — re-draft it; the facts may have changed"}
        why = _referrer_problem(session, email)
        if why:
            return {"error": why}
        if subject is not None:
            email.subject = subject[:180]
        if body is not None:
            email.body = body
        if email.kind == "cold":
            from jobhunter.outreach import suppress

            if _wants_opt_out(email):
                email.body = suppress.with_opt_out(email.body)
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
    from jobhunter.outreach import warmup

    return {
        "daily_cap": DAILY_CAP,
        "warmup": warmup.describe(),
        "sent_today": sent_today(),
        "remaining_today": remaining_today(),
        "send_window": SEND_WINDOW,
        "in_window": in_send_window(),
        "send_mode": SEND_MODE,
        "sender_name": gmail.DISPLAY_NAME or None,
        "sender_address": gmail.ADDRESS or None,
        "ledger": ledger.status(),
        "gmail": gmail.status(),
    }


def _wants_opt_out(email) -> bool:
    """The user's own email (direct_intro) carries no opt-out line unless
    outreach.compose.direct_opt_out says so; every other cold structure does."""
    from jobhunter.outreach import compose

    return not (getattr(email, "structure", None) == "direct_intro" and not compose.DIRECT_OPT_OUT)
