"""Gmail sender with the guardrails that keep the user's account alive.

Hard daily cap, a send window, randomized spacing, plain text only, and a
per-contact one-thread rule. Nothing is sent that a human hasn't approved.
"""

from __future__ import annotations

import base64
import logging
import random
import re
import time
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage

from sqlmodel import col, func, select

from jobhunter import CONFIG, ROOT, guardrails
from jobhunter.db import Contact, Email, Reply, get_session, init_db, utcnow
from jobhunter.outreach import gmail, inboxes, ledger

log = logging.getLogger(__name__)

_O = CONFIG["outreach"]
DAILY_CAP = int(_O.get("daily_send_cap", 25))
SEND_WINDOW = _O.get("send_window", [10, 19])
STAGGER_SECONDS = _O.get("stagger_seconds", [45, 210])
# 2026-10-24, Tejmul: "send with a time gap so it feels like a real person". A person writes
# to someone abroad in THAT person's working morning, not at 1 a.m. their time; India keeps the
# 10–19 IST window and its jittered slots. And never two mails close together.
RECIPIENT_HOURS = _O.get("recipient_hours", [9, 12])
MIN_GAP_MINUTES = float(_O.get("min_gap_minutes", 15))
_LOCAL_TZ = str(_O.get("timezone", "Asia/Kolkata"))
_TZ_BY_REGION = {"us": "America/New_York", "remote": "America/New_York", "uk": "Europe/London",
                 "de": "Europe/Berlin", "eu": "Europe/Berlin", "nl": "Europe/Amsterdam"}
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
    return min(ledger.status(candidate_id)["left"], max(0, ledger.effective_cap(ledger._today()) - sent_today()))


def _dryrun_send(email_id: int, to: str, subject: str, body: str, *, in_reply_to: str | None = None,
                 thread_id: str | None = None, box: inboxes.Inbox | None = None) -> dict:
    """Write the mail to outbox/ and return a fake Gmail result, so the loop completes.
    The .eml carries a real-looking Message-ID and, for a follow-up, In-Reply-To /
    References — so the rehearsal exercises the threading the live send uses."""
    import uuid

    OUTBOX.mkdir(exist_ok=True)
    path = OUTBOX / f"{email_id:05d}.eml"
    msg = EmailMessage()
    # the rehearsal shows the same identity the real send will
    msg["From"] = gmail.build_message(to, subject, "", sender=(box.name, box.address) if box else None)["From"]
    msg["To"] = to
    msg["Subject"] = subject
    msg["Message-ID"] = f"<dryrun-{email_id}-{uuid.uuid4().hex[:10]}@zonuly.local>"
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    msg["X-ZoNuLy-Mode"] = "dryrun"
    if box is not None:
        msg["X-ZoNuLy-Inbox"] = box.key
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
    # the same message the SMTP path sends (gmail.build_message): one build, one fingerprint
    msg = gmail.build_message(to, subject, body)
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
    why = suppress.cooldown(session, email) or suppress.spam_hold(session, email)
    if why:
        return why
    if contact is None:
        return "the contact this draft was written to no longer exists"
    if not guardrails.is_ready(contact):
        return (f"{contact.name or email.to_email} is not a confirmed-current referrer "
                f"({contact.affiliation or 'unchecked'}: {contact.affiliation_evidence or 'no evidence on record'}) "
                "— re-check them or reject the draft")
    return None


def _inbox_for(session, email: Email, parent: Email | None, want: str | None, guessed: bool) -> inboxes.Inbox | str:
    """The inbox this mail leaves from, or why none can send it now. A follow-up goes from the
    inbox its first mail went from; a cold mail from the one asked for, else the ready inbox
    with the most left today that takes its address."""
    live = SEND_MODE == "gmail"
    pinned = email.sender_inbox or ((parent.sender_inbox or inboxes.GMAIL) if parent is not None else None)
    if pinned or want:
        box = inboxes.get(pinned or want)
        if box is None:
            return f"inbox {pinned or want} is not configured (outreach.inboxes)"
        if live and not box.ready():
            return f"inbox {box.address} is not ready: set {box.password_env} in .env"
        if not pinned and not inboxes.takes(box, guessed=guessed):
            return f"{box.address} writes to verified addresses only — this one is a guess"
        if inboxes.left_today(session, box) <= 0:
            return f"{box.address} has sent today's {inboxes.quota(session, box)}"
        rest = inboxes.brake(session, box)
        if rest:
            return rest
        return box
    pool = [b for b in inboxes.sending(live)
            if inboxes.takes(b, guessed=guessed) and inboxes.left_today(session, b) > 0
            and not inboxes.brake(session, b)]
    if not pool:
        return "no inbox has mail left today that can take this address"
    return max(pool, key=lambda b: inboxes.left_today(session, b))


def send_email(email_id: int, *, ignore_window: bool = False, inbox: str | None = None) -> dict:
    """Send one approved email. Refuses if the cap is hit or we're outside the send window.
    `inbox` names the sending inbox (outreach/inboxes.py); without it one is chosen."""
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

            # spread one company's mail over days, not into one morning (2026-10-23, Sarvam)
            why = suppress.domain_cap(session, email)
            if why:
                return {"error": why, "email_id": email_id}
            # the opt-out line rides on every cold mail (added at approval; this is the net)
            if suppress.OPT_OUT_LINE and not suppress.has_opt_out(email.body) and _wants_opt_out(email):
                email.body = suppress.with_opt_out(email.body)
                session.add(email)
                session.commit()
        to, subject, body = email.to_email, email.subject, email.body
        retried = retries(email.error) > 0
        thread_id = email.gmail_thread_id
        candidate_id = email.candidate_id
        guessed = (email.address_confidence or "verified") != "verified"
        # a follow-up or reschedule threads under the mail it answers
        parent = session.get(Email, email.parent_email_id) if email.parent_email_id else None
        in_reply_to = parent.gmail_message_id if parent and (parent.gmail_message_id or "").startswith("<") else None
        if guessed and email.kind != "cold" and _delivered(parent):
            guessed = False   # the address took a mail days ago without bouncing: it is not a guess any more
        if guessed and email.kind == "cold":
            # checked before it leaves (2026-10-30): a bounce costs the inbox, not just the mail
            from jobhunter.outreach import verify_address

            contact = session.get(Contact, email.contact_id)
            st = verify_address.state(contact, to, email.address_confidence)
            if contact is not None:
                session.add(contact)
            if st == "dead":
                email.status = "failed"
                email.error = f"address check: {(contact.email_status if contact else None) or 'no mail server'} — not sent"
                session.add(email)
                session.commit()
                return {"error": email.error, "email_id": email_id}
            guessed = st != "verified"
            session.commit()
        box = _inbox_for(session, email, parent, inbox, guessed)
        if isinstance(box, str):
            return {"error": box, "email_id": email_id}

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
        if SEND_MODE == "gmail" and not box.is_gmail:
            from jobhunter.outreach import mailbox

            result = (mailbox.sent_already(box, to, subject, utcnow() - timedelta(days=3)) if retried else None) \
                or mailbox.send(box, to, subject, body, in_reply_to=in_reply_to)
        elif SEND_MODE == "gmail" and gmail.AUTH == "app_password":
            # a retry first asks Sent Mail: the drop may have come after Gmail took the mail
            result = (gmail.sent_already(to, subject, utcnow() - timedelta(days=3)) if retried else None) \
                or gmail.send(to, subject, body, in_reply_to=in_reply_to)
        elif SEND_MODE == "gmail":
            svc = gmail.service()
            result = svc.users().messages().send(userId="me", body=_build_message(to, subject, body, thread_id)).execute()
        else:
            result = _dryrun_send(email_id, to, subject, body, in_reply_to=in_reply_to, thread_id=thread_id, box=box)
    except gmail.GmailNotConfigured as e:
        ledger.release(candidate_id, guessed=guessed)
        _unclaim(email_id)
        return {"error": str(e)}
    except Exception as e:  # noqa: BLE001 — API errors are reported, not raised, so the queue keeps moving
        log.exception("send failed for email %s", email_id)
        ledger.release(candidate_id, guessed=guessed)
        with get_session() as session:
            email = session.get(Email, email_id)
            tries = retries(email.error)
            # 2026-10-26, Tejmul: "if an email gets missed it should wire up next" — a dropped
            # network or a timeout puts it back at the front of the queue for the next run
            if transient(e) and tries < MAX_RETRIES:
                email.status, email.error = "approved", f"retry {tries + 1}: {str(e)[:450]}"
            else:
                email.status, email.error = "failed", str(e)[:500]
                email.sender_inbox = box.key     # a refusal rests the inbox that got it (inboxes.brake)
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
        email.sender_inbox = box.key
        email.error = None
        session.add(email)
        session.commit()

    log.info("sent email %s to %s from %s (thread %s, mode %s)", email_id, to, box.address, result.get("threadId"), SEND_MODE)
    return {"sent": True, "email_id": email_id, "to": to, "thread_id": result.get("threadId"),
            "mode": SEND_MODE, "inbox": box.key, **({"path": result["path"]} if result.get("path") else {})}


MAX_RETRIES = 3
_TRANSIENT = re.compile(r"ssl|eof occurred|timed? ?out|nodename|name or service|temporar|try again|"
                        r"connection (reset|refused|aborted)|network is unreachable|errno (8|50|51|54|60|61|64|65)|"
                        r"\b421\b|\b4\.\d\.\d", re.I)
_PERMANENT = re.compile(r"\b5\.\d\.\d|\b55\d\b|limit exceeded|spam|blocked|not found|auth", re.I)


def transient(e: BaseException | str) -> bool:
    """A failure that says nothing about the mail or the account: a dropped network, DNS,
    a TLS hiccup, a timeout, a 4xx try-later. A refusal (5xx, limit, spam, auth) never is."""
    text = f"{type(e).__name__}: {e}" if isinstance(e, BaseException) else str(e)
    return bool(_TRANSIENT.search(text)) and not _PERMANENT.search(text)


def retries(error: str | None) -> int:
    m = re.match(r"retry (\d+):", error or "")
    return int(m.group(1)) if m else 0


def requeue_failed(days: int = 3) -> list[int]:
    """Failed sends of the last `days` whose failure was transient: back to approved, in the
    queue's front, under the same cap and gaps as everything else."""
    from datetime import timedelta

    init_db()
    out = []
    with get_session() as session:
        for e in session.exec(select(Email).where(Email.status == "failed",
                                                  col(Email.approved_at) >= utcnow() - timedelta(days=days))).all():
            if transient(e.error or "") and retries(e.error) < MAX_RETRIES:
                e.status, e.error = "approved", f"retry {retries(e.error) + 1}: {(e.error or '')[:450]}"
                session.add(e)
                out.append(e.id)
        session.commit()
    return out


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


def recipient_tz(session, email: Email) -> str:
    """Where the recipient works, from their company: an India company (or one with no region
    on file) is the local zone; a US / UK / EU / remote-first company is that region's."""
    from jobhunter.db import Company

    return company_tz(session.get(Company, email.company_id))


def company_tz(c) -> str:
    if c is None or (c.hq_region or "india") == "india" or (c.city or "").lower().startswith(("bengaluru", "hyderabad")):
        return _LOCAL_TZ
    return _TZ_BY_REGION.get(c.hq_region, _LOCAL_TZ)


def abroad_per_day() -> int:
    """Sends one foreign time zone's morning holds (recipient_hours, min_gap_minutes apart,
    the gap jittered up to 50%): the most mail a day that should be planned into it."""
    span = (int(RECIPIENT_HOURS[1]) - int(RECIPIENT_HOURS[0])) * 60
    return max(1, int(span / (MIN_GAP_MINUTES * 1.25)) - 1)


def _local(now: datetime, tz: str) -> datetime:
    from zoneinfo import ZoneInfo

    base = now if now.tzinfo else now.replace(tzinfo=ZoneInfo(_LOCAL_TZ))
    return base.astimezone(ZoneInfo(tz))


def in_recipient_morning(session, email: Email, now: datetime) -> bool:
    """A weekday morning where the recipient is (outreach.recipient_hours, outreach.send_days)."""
    from jobhunter.outreach import warmup

    t = _local(now, recipient_tz(session, email))
    return int(RECIPIENT_HOURS[0]) <= t.hour < int(RECIPIENT_HOURS[1]) and warmup.is_send_day(t.date())


def send_due(now: datetime | None = None, *, stagger: bool = True, max_per_run: int | None = None) -> dict:
    """The send agent's one run (every few minutes, launchd): send what today's jittered
    slots say is due — the warm-up quota spread across the window — and no more than
    max_per_run at once. Approved mail only; outside the window or on a non-sending day it
    does nothing. A guessed address waits while the guessed cap (or the bounce brake) is
    spent; a verified one behind it goes instead. Each sending inbox (outreach/inboxes.py)
    is paced on its own, so N inboxes send up to N mails a run, each at a person's pace."""
    from jobhunter.outreach import suppress, warmup

    init_db()
    requeue_failed()      # a send that failed on the network goes again, first (2026-10-26)
    now = now or datetime.now()
    india_open = in_send_window(now)
    with get_session() as session:
        queue = _queue(session, 200)
        # who may be written to now: an India recipient inside the IST window; anyone abroad in
        # their own weekday morning — and then only with the gap since the last send kept
        abroad = {e.id for e in queue if recipient_tz(session, e) != _LOCAL_TZ and in_recipient_morning(session, e, now)}
        home = {e.id for e in queue if recipient_tz(session, e) == _LOCAL_TZ} if india_open else set()
        pinned = {}
        guessed_of = {}
        for e in queue:
            parent = session.get(Email, e.parent_email_id) if e.parent_email_id else None
            pinned[e.id] = e.sender_inbox or ((parent.sender_inbox or inboxes.GMAIL) if parent is not None else None)
            guessed_of[e.id] = (e.address_confidence or "verified") != "verified" and not (
                e.kind != "cold" and _delivered(parent))
    if not home and not abroad:
        return {"sent": 0, "skipped": "outside the send window / not a sending day (here or where the recipients are)"}
    led = ledger.status()
    per_run = max_per_run or warmup.MAX_PER_RUN
    out = {"sent": 0, "used": led["used"], "quota": led["cap"], "results": [], "inboxes": {}}
    if led["left"] <= 0:
        out["skipped"] = "today's quota is spent"
        return out
    # 2026-10-30: the pace is per inbox — each one its own jittered slots in the India window,
    # its own gap before a mail abroad, at most max_per_run a run. Domain inboxes go first
    # (they take verified addresses only); the Gmail after them takes what is left.
    with get_session() as session:
        guessed_left = 0 if suppress.brake(session) else led["guessed_left"]
        live = SEND_MODE == "gmail"
        boxes = []
        for b in sorted(inboxes.sending(live), key=lambda b: b.is_gmail):
            if inboxes.left_today(session, b, now) <= 0:
                out["inboxes"][b.key] = "today's quota is spent"
                continue
            rest = inboxes.brake(session, b, now)
            if rest:
                out["inboxes"][b.key] = rest
                continue
            home_due = india_open and inboxes.due(session, b, now) > inboxes.used_today(session, b, now)
            gap = inboxes.gap_ok(session, b, now)
            allowed = (home if home_due else set()) | (abroad if gap else set())
            if allowed:
                boxes.append((b, allowed))
            else:
                out["inboxes"][b.key] = "nothing due yet" if not abroad else f"waiting {MIN_GAP_MINUTES:g}+ min since its last send"
    if not boxes:
        out["skipped"] = "nothing due yet"
        return out
    taken: set[int] = set()
    pause = False
    checks = int(_O.get("address_checks_per_run", 6))
    for b, allowed in boxes:
        sent_here = 0
        for e in queue:
            if sent_here >= per_run or out["sent"] >= led["left"]:
                break
            if e.id in taken or e.id not in allowed or pinned[e.id] not in (None, b.key):
                continue
            guessed = guessed_of[e.id]
            if guessed and not inboxes.takes(b, guessed=True):
                if e.kind != "cold" or checks <= 0:
                    continue
                from jobhunter.outreach import verify_address

                checks -= 1
                with get_session() as session:
                    contact = session.get(Contact, e.contact_id)
                    st = verify_address.state(contact, e.to_email, e.address_confidence)
                    if contact is not None:
                        session.add(contact)
                        session.commit()
                if st != "verified":
                    continue        # a guess stays for the Gmail (or, dead, is failed when it is tried)
                guessed = guessed_of[e.id] = False
            if guessed and guessed_left <= 0:
                continue
            taken.add(e.id)
            if stagger and pause:
                time.sleep(random.uniform(float(STAGGER_SECONDS[0]), float(STAGGER_SECONDS[1])))
            r = send_email(e.id, inbox=b.key)
            out["results"].append(r)
            pause = bool(r.get("sent"))
            if r.get("sent"):
                out["sent"] += 1
                sent_here += 1
                guessed_left -= 1 if guessed else 0
        out["inboxes"][b.key] = f"sent {sent_here}"
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
        "inboxes": _inbox_rows(),
    }


def _inbox_rows() -> list[dict]:
    with get_session() as session:
        return inboxes.describe(session)


def _wants_opt_out(email) -> bool:
    """The user's own email (direct_intro) carries no opt-out line unless
    outreach.compose.direct_opt_out says so; every other cold structure does."""
    from jobhunter.outreach import compose

    return not (getattr(email, "structure", None) == "direct_intro" and not compose.DIRECT_OPT_OUT)


def next_send_day(now: datetime | None = None) -> date:
    """The day the next batch goes: today while the window is still ahead, else the next send day."""
    from jobhunter.outreach import warmup

    now = now or datetime.now()
    d = now.date()
    if warmup.is_send_day(d) and now.hour < SEND_WINDOW[1] - 1:
        return d
    d += timedelta(days=1)
    while not warmup.is_send_day(d):
        d += timedelta(days=1)
    return d


def plan(day: date | str | None = None) -> dict:
    """What goes out on `day` (default: the next send day) and roughly when, worked out now
    from the approved queue with the same rules send_due applies on the day: the cap, the
    jittered India slots, the recipient's own morning abroad, the gap between sends, three a
    day per domain, the bounce brake on guessed addresses. 2026-10-25, Tejmul: "whatever
    should go the next day should be scheduled the day before itself". A preview, not a
    promise: an approval, a bounce or a reply before then changes it."""
    from zoneinfo import ZoneInfo

    from jobhunter.db import Company
    from jobhunter.outreach import suppress, warmup

    init_db()
    day = date.fromisoformat(day) if isinstance(day, str) else (day or next_send_day())
    cap = ledger.effective_cap(day.isoformat())
    used = ledger.status()["used"] if day == datetime.now().date() else 0
    india = [t.replace(tzinfo=None) for t in warmup.slots(day, cap)]
    if day == datetime.now().date():
        india = [t for t in india if t > datetime.now()]
    planned, waiting, taken = [], [], []
    per_domain: dict[str, int] = {}
    with get_session() as session:
        brake = suppress.brake(session)
        guessed_left = 0 if brake else ledger.GUESSED_CAP
        # the gap is kept per inbox: with N inboxes, N mails can share a stretch of the morning
        gap_s = MIN_GAP_MINUTES * 60 / max(1, len(inboxes.ready()))
        for e in _queue(session, 200):
            c = session.get(Company, e.company_id)
            contact = session.get(Contact, e.contact_id)
            row = {"email_id": e.id, "company": c.name if c else None, "to": contact.name if contact else e.to_email,
                   "kind": e.kind, "address": e.address_confidence or "verified", "subject": e.subject}
            why = _referrer_problem(session, e)
            guessed = (e.address_confidence or "verified") != "verified"
            dom = suppress._domain(e.to_email)
            if not why and guessed and guessed_left <= 0:
                why = brake or f"today's {ledger.GUESSED_CAP} guessed-address sends are planned already"
            dcap = suppress.domain_cap_for(session, e.company_id, day.isoformat())
            if not why and e.kind == "cold" and dom not in suppress.FREEMAIL and per_domain.get(dom, 0) >= dcap:
                why = f"{dcap} mails into {dom} that day already — the next day"
            if not why and len(planned) + used >= cap:
                why = f"the day's {cap} are planned — the next send day"
            if why:
                waiting.append({**row, "why": why})
                continue
            tz = recipient_tz(session, e)
            def clear(x: datetime) -> bool:
                return all(abs((x - y).total_seconds()) >= gap_s for y in taken)

            if tz == _LOCAL_TZ:
                at = next((x for x in india if clear(x)), None)
                if at is not None:
                    india.remove(at)
            else:  # their own morning, in IST, the gap kept from everything already planned
                start = datetime.combine(day, datetime.min.time()).replace(hour=int(RECIPIENT_HOURS[0]), tzinfo=ZoneInfo(tz))
                end = start.replace(hour=int(RECIPIENT_HOURS[1]))
                at, t = None, start
                while t < end:
                    local = t.astimezone(ZoneInfo(_LOCAL_TZ)).replace(tzinfo=None)
                    if clear(local):
                        at = local
                        break
                    t += timedelta(minutes=5)
            if at is None:
                waiting.append({**row, "why": "no free time left in the recipient's morning that day"})
                continue
            taken.append(at)
            per_domain[dom] = per_domain.get(dom, 0) + 1
            guessed_left -= 1 if guessed else 0
            planned.append({**row, "at": at.strftime("%Y-%m-%d %H:%M"), "tz": tz,
                            "their_time": at.replace(tzinfo=ZoneInfo(_LOCAL_TZ)).astimezone(ZoneInfo(tz)).strftime("%H:%M")})
        drafts = session.exec(select(Email).where(Email.status == "draft")).all()
        to_review = len(drafts)
        # the ones that could go if approved: a real address, and nothing stopping the referrer
        sendable = sum(1 for d in drafts if (d.address_confidence or "verified") != "pattern-guessed"
                       and not (d.expires_at and d.expires_at < utcnow()) and not _referrer_problem(session, d))
    planned.sort(key=lambda r: r["at"])
    return {"day": day.isoformat(), "weekday": day.strftime("%A"), "cap": cap, "planned": planned,
            "open_slots": max(0, cap - used - len(planned)), "waiting": waiting, "drafts_to_review": to_review,
            "drafts_sendable": sendable,
            "bounce_brake": brake}
