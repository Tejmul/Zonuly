"""Gmail sender with the guardrails that keep the user's account alive.

Hard daily cap, a send window, randomized spacing, plain text only, and a
per-contact one-thread rule. Nothing is sent that a human hasn't approved.
"""

from __future__ import annotations

import base64
import logging
import random
import time
from datetime import date, datetime, timedelta, timezone
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


def recipient_tz(session, email: Email) -> str:
    """Where the recipient works, from their company: an India company (or one with no region
    on file) is the local zone; a US / UK / EU / remote-first company is that region's."""
    from jobhunter.db import Company

    c = session.get(Company, email.company_id)
    if c is None or (c.hq_region or "india") == "india" or (c.city or "").lower().startswith(("bengaluru", "hyderabad")):
        return _LOCAL_TZ
    return _TZ_BY_REGION.get(c.hq_region, _LOCAL_TZ)


def _local(now: datetime, tz: str) -> datetime:
    from zoneinfo import ZoneInfo

    base = now if now.tzinfo else now.replace(tzinfo=ZoneInfo(_LOCAL_TZ))
    return base.astimezone(ZoneInfo(tz))


def in_recipient_morning(session, email: Email, now: datetime) -> bool:
    """A weekday morning where the recipient is (outreach.recipient_hours, outreach.send_days)."""
    from jobhunter.outreach import warmup

    t = _local(now, recipient_tz(session, email))
    return int(RECIPIENT_HOURS[0]) <= t.hour < int(RECIPIENT_HOURS[1]) and warmup.is_send_day(t.date())


def _gap_ok(session, now: datetime) -> bool:
    """At least MIN_GAP_MINUTES (plus up to half again, fixed per last mail) since the last send."""
    last = session.exec(select(Email).where(col(Email.sent_at).is_not(None)).order_by(col(Email.sent_at).desc())).first()
    if last is None or last.sent_at is None:
        return True
    gap = MIN_GAP_MINUTES * (1 + 0.5 * ((last.id * 2654435761) % 1000) / 1000)
    sent = last.sent_at.replace(tzinfo=timezone.utc)
    return (_local(now, "UTC") - sent).total_seconds() >= gap * 60


def send_due(now: datetime | None = None, *, stagger: bool = True, max_per_run: int | None = None) -> dict:
    """The send agent's one run (every few minutes, launchd): send what today's jittered
    slots say is due — the warm-up quota spread across the window — and no more than
    max_per_run at once. Approved mail only; outside the window or on a non-sending day it
    does nothing. A guessed address waits while the guessed cap (or the bounce brake) is
    spent; a verified one behind it goes instead."""
    from jobhunter.outreach import suppress, warmup

    init_db()
    now = now or datetime.now()
    india_open = in_send_window(now)
    with get_session() as session:
        queue = _queue(session, 200)
        # who may be written to now: an India recipient inside the IST window; anyone abroad in
        # their own weekday morning — and then only with the gap since the last send kept
        abroad = {e.id for e in queue if recipient_tz(session, e) != _LOCAL_TZ and in_recipient_morning(session, e, now)}
        home = {e.id for e in queue if recipient_tz(session, e) == _LOCAL_TZ} if india_open else set()
        gap_ok = _gap_ok(session, now)
    if not home and not abroad:
        return {"sent": 0, "skipped": "outside the send window / not a sending day (here or where the recipients are)"}
    led = ledger.status()
    due = warmup.due(now) if india_open else 0
    want_home = max(0, due - led["used"])
    want = min(max(want_home, 1 if (abroad and gap_ok) else 0), led["left"], max_per_run or warmup.MAX_PER_RUN)
    out = {"sent": 0, "due_slots": due, "used": led["used"], "quota": led["cap"], "results": []}
    if want <= 0:
        out["skipped"] = ("nothing due yet" if led["left"] else "today's quota is spent") if not abroad else \
            f"waiting {MIN_GAP_MINUTES:g}+ min since the last send"
        return out
    with get_session() as session:
        guessed_paused = suppress.brake(session)
        allowed = (home if want_home > 0 else set()) | (abroad if gap_ok else set())
        ids = [(e.id, (e.address_confidence or "verified") != "verified" and not (
            e.kind != "cold" and _delivered(session.get(Email, e.parent_email_id) if e.parent_email_id else None)))
            for e in queue if e.id in allowed]
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
            if not why and e.kind == "cold" and per_domain.get(dom, 0) >= suppress.PER_DOMAIN_DAILY_CAP:
                why = f"{suppress.PER_DOMAIN_DAILY_CAP} mails into {dom} that day already — the next day"
            if not why and len(planned) + used >= cap:
                why = f"the day's {cap} are planned — the next send day"
            if why:
                waiting.append({**row, "why": why})
                continue
            tz = recipient_tz(session, e)
            def clear(x: datetime) -> bool:
                return all(abs((x - y).total_seconds()) >= MIN_GAP_MINUTES * 60 for y in taken)

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
