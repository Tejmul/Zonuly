"""A "yes" must not get lost — MOTIV §4 step 11.

When a reply proposes a time, sends a link, or sets a deadline, this module reads it
into an Event: what kind of thing it is, when, in which timezone, the link, and the
sentence it was read from. The event goes on our calendar (outreach/calendar.py) and
we are notified. Two events too close together are a conflict, and the reschedule
email is drafted into the review queue — the machine never confirms a time to the
other side on its own.

Extraction is the `cheap` model reading the reply with today's date injected, plus a
regex pass for links and explicit times as a fallback. A time the reply does not
state stays null: an event with no time is still worth a notification.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone

from sqlmodel import col, select

from jobhunter import CONFIG
from jobhunter.db import Company, Contact, Email, Event, Reply, get_session, init_db, utcnow

log = logging.getLogger(__name__)

_C = CONFIG.get("calendar") or {}
LOCAL_TZ = str(_C.get("timezone", "Asia/Kolkata"))
DEFAULT_MINUTES = int(_C.get("default_minutes", 30))
CONFLICT_MINUTES = int(_C.get("conflict_window_minutes", 45))

_LINK = re.compile(r"https?://(?:meet\.google\.com|[\w.-]*zoom\.us|calendly\.com|cal\.com|teams\.microsoft\.com|"
                   r"[\w.-]*hackerrank\.com|[\w.-]*codility\.com|[\w.-]*coderpad\.io|[\w.-]*greenhouse\.io|"
                   r"[\w.-]*ashbyhq\.com|[\w.-]*lever\.co)[^\s>\")]*", re.I)

_SYSTEM = """You read one reply to a job-seeker's referral request and extract any scheduling in it.
You use only what the reply says. If it names no time, times is empty. If it names no link,
link is null. Never invent. JSON only."""

_PROMPT = """Today is {today} ({weekday}). The job-seeker's timezone is {tz}.

Reply from {sender} at {company}:
---
{body}
---

Reply with exactly:
{{"intent": "call|assessment|interview|referral_done|other|none",
  "times": [{{"start": "<ISO 8601 with offset, e.g. 2026-09-10T16:00:00+05:30>", "minutes": <int or null>,
             "quote": "<the words the time was read from>"}}],
  "timezone": "<IANA zone or offset the sender wrote in, or null>",
  "link": "<a meeting/assessment URL in the reply, or null>",
  "deadline": "<ISO 8601 or null — e.g. 'finish the assessment by Friday'>",
  "needs_action": "<what the job-seeker must do next, in the reply's own words, or null>"}}

Rules: relative dates ("Thursday", "tomorrow", "next week") are resolved from today's date.
If the sender gives options, list each as its own time. A vague "sometime next week" is
intent with an empty times list, not an invented time."""


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        try:
            from zoneinfo import ZoneInfo

            d = d.replace(tzinfo=ZoneInfo(LOCAL_TZ))
        except Exception:  # noqa: BLE001
            d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc).replace(tzinfo=None)


_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_DAY_WORD = re.compile(r"\b(today|tomorrow|mon(?:day)?|tue(?:s|sday)?|wed(?:nesday)?|thu(?:rs|rsday)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?)\b", re.I)
_CLOCK = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm|AM|PM)?\s*(IST|PT|PST|PDT|ET|EST|EDT|GMT|UTC|BST|CET|CEST)?\b")
_TZ_OFFSET = {"ist": "+05:30", "pt": "-07:00", "pst": "-08:00", "pdt": "-07:00", "et": "-04:00", "est": "-05:00",
              "edt": "-04:00", "gmt": "+00:00", "utc": "+00:00", "bst": "+01:00", "cet": "+01:00", "cest": "+02:00"}
_INTENT_WORDS = [("interview", re.compile(r"\binterview\b", re.I)), ("assessment", re.compile(r"\b(assessment|take[- ]home|coding (?:test|challenge)|hackerrank|codility)\b", re.I)),
                 ("call", re.compile(r"\b(call|chat|talk|meet|meeting|catch up|zoom|google meet|hop on)\b", re.I)),
                 ("referral_done", re.compile(r"\b(referred you|submitted your|put you forward|passed (?:your|it) (?:on|along))\b", re.I))]


def _regex_times(body: str, now: datetime) -> tuple[list[dict], str | None]:
    """Plain phrases — "Thursday 4pm IST", "tomorrow at 10:30" — resolved without a model.
    A day word without a clock, or a clock without a day, is not a time."""
    found: list[dict] = []
    tz_seen = None
    for sentence in re.split(r"(?<=[.!?\n])\s+", body or ""):
        dm = _DAY_WORD.search(sentence)
        if not dm:
            continue
        for cm in _CLOCK.finditer(sentence):
            hour = int(cm.group(1))
            if hour > 23 or (cm.group(3) is None and cm.group(2) is None):
                continue   # a bare number is not a time
            minute = int(cm.group(2) or 0)
            ampm = (cm.group(3) or "").lower()
            if ampm == "pm" and hour < 12:
                hour += 12
            if ampm == "am" and hour == 12:
                hour = 0
            word = dm.group(1).lower()
            if word == "today":
                day = now.date()
            elif word == "tomorrow":
                day = (now + timedelta(days=1)).date()
            else:
                idx = next(i for i, w in enumerate(_WEEKDAYS) if w.startswith(word[:3]))
                ahead = (idx - now.weekday()) % 7 or 7     # "Thursday" said on a Thursday means next week
                day = (now + timedelta(days=ahead)).date()
            tz = (cm.group(4) or "").lower()
            offset = _TZ_OFFSET.get(tz)
            iso = f"{day.isoformat()}T{hour:02d}:{minute:02d}:00" + (offset or "")
            start = _parse_iso(iso)
            if start:
                tz_seen = tz_seen or (cm.group(4) or None)
                found.append({"start": start, "minutes": DEFAULT_MINUTES, "quote": sentence.strip()[:300]})
            break
    return found, tz_seen


def _regex_intent(body: str) -> str:
    for kind, rx in _INTENT_WORDS:
        if rx.search(body or ""):
            return kind
    return "none"


def extract(body: str, *, sender: str, company: str, now: datetime | None = None) -> dict:
    """Read scheduling out of one reply. Always returns a dict; empty means 'nothing to schedule'.
    The model reads it first; plain regex stands in when the model is unavailable, and
    its explicit times are trusted over a model that returned none."""
    from jobhunter import llm

    now = now or datetime.now()
    out: dict = {"intent": "none", "times": [], "timezone": None, "link": None, "deadline": None, "needs_action": None}
    m = _LINK.search(body or "")
    if m:
        out["link"] = m.group(0)
    rx_times, rx_tz = _regex_times(body, now)
    rx_intent = _regex_intent(body)
    try:
        data = llm.chat_json(
            _PROMPT.format(today=now.strftime("%Y-%m-%d"), weekday=now.strftime("%A"), tz=LOCAL_TZ,
                           sender=sender, company=company, body=(body or "")[:3000]),
            _SYSTEM, temperature=0.0, alias="cheap", purpose="schedule-extract", default=None,
        )
    except Exception as e:  # noqa: BLE001 — no key / 429: the link regex still stands
        log.debug("schedule extract failed: %s", e)
        data = None
    if isinstance(data, dict):
        if data.get("intent") in ("call", "assessment", "interview", "referral_done", "other", "none"):
            out["intent"] = data["intent"]
        times = []
        for t in data.get("times") or []:
            if not isinstance(t, dict):
                continue
            start = _parse_iso(t.get("start"))
            if start:
                times.append({"start": start, "minutes": t.get("minutes") or DEFAULT_MINUTES,
                              "quote": str(t.get("quote") or "")[:300]})
        out["times"] = times
        out["timezone"] = data.get("timezone") or None
        out["link"] = out["link"] or (data.get("link") or None)
        out["deadline"] = _parse_iso(data.get("deadline"))
        out["needs_action"] = (data.get("needs_action") or None)
    # the regex pass fills what the model missed (or all of it, when the model was down)
    if not out["times"] and rx_times:
        out["times"] = rx_times
        out["timezone"] = out["timezone"] or rx_tz
    if out["intent"] == "none" and (out["times"] or out["link"]):
        out["intent"] = rx_intent if rx_intent != "none" else "call"
    return out


def _conflicts_with(session, candidate_id: int, start: datetime, minutes: int, exclude: int | None) -> Event | None:
    window = timedelta(minutes=CONFLICT_MINUTES)
    end = start + timedelta(minutes=minutes)
    rows = session.exec(
        select(Event).where(Event.candidate_id == candidate_id, col(Event.starts_at).is_not(None),
                            col(Event.status).in_(["proposed", "confirmed", "conflict"]))
    ).all()
    for e in rows:
        if exclude and e.id == exclude:
            continue
        e_end = e.ends_at or (e.starts_at + timedelta(minutes=DEFAULT_MINUTES))
        if e.starts_at < end + window and e_end > start - window:
            return e
    return None


def events_from_reply(reply_id: int) -> list[dict]:
    """Turn one reply into Event rows (one per proposed time; one with no time if only a
    link/deadline/intent was found). Detects conflicts and drafts the reschedule."""
    init_db()
    with get_session() as session:
        reply = session.get(Reply, reply_id)
        if reply is None:
            return [{"error": f"no reply {reply_id}"}]
        email = session.get(Email, reply.email_id)
        if email is None:
            return [{"error": "reply has no email"}]
        if session.exec(select(Event).where(Event.reply_id == reply_id)).first():
            return [{"skipped": "already scheduled from this reply"}]
        company = session.get(Company, email.company_id)
        contact = session.get(Contact, email.contact_id)
        body, sender = reply.body, reply.from_addr
        company_name = company.name if company else "the company"
        contact_name = contact.name if contact else sender
        email_id, company_id, contact_id, candidate_id = email.id, email.company_id, email.contact_id, email.candidate_id

    found = extract(body, sender=sender, company=company_name)
    if found["intent"] == "none" and not found["times"] and not found["link"] and not found["deadline"]:
        return [{"skipped": "nothing to schedule", "intent": "none"}]

    out: list[dict] = []
    with get_session() as session:
        slots = found["times"] or [None]
        for slot in slots:
            ev = Event(
                reply_id=reply_id, email_id=email_id, company_id=company_id, contact_id=contact_id,
                candidate_id=candidate_id, kind=found["intent"] if found["intent"] != "none" else "other",
                starts_at=slot["start"] if slot else None,
                ends_at=(slot["start"] + timedelta(minutes=int(slot["minutes"]))) if slot else None,
                timezone=found["timezone"], link=found["link"], deadline=found["deadline"],
                quote=(slot["quote"] if slot else None) or (found["needs_action"] or None),
                needs_action=found["needs_action"], status="proposed",
            )
            if ev.starts_at:
                clash = _conflicts_with(session, candidate_id, ev.starts_at, int(slot["minutes"]), None)
                if clash:
                    ev.status = "conflict"
                    ev.conflict_with = clash.id
            session.add(ev)
            session.commit()
            session.refresh(ev)
            out.append({"event_id": ev.id, "kind": ev.kind, "starts_at": ev.starts_at, "status": ev.status,
                        "link": ev.link, "conflict_with": ev.conflict_with})
            if ev.status == "conflict":
                out[-1]["reschedule"] = draft_reschedule(ev.id)

    _notify(out, company_name, contact_name)
    return out


def draft_reschedule(event_id: int) -> dict:
    """The reschedule email, into the review queue — never sent by the machine."""
    from jobhunter.outreach.drafter import _compose, signature  # noqa: F401  (signature used by _compose)

    init_db()
    with get_session() as session:
        ev = session.get(Event, event_id)
        if ev is None:
            return {"error": f"no event {event_id}"}
        other = session.get(Event, ev.conflict_with) if ev.conflict_with else None
        email = session.get(Email, ev.email_id)
        contact = session.get(Contact, ev.contact_id)
        company = session.get(Company, ev.company_id)
        existing = session.exec(select(Email).where(Email.kind == "reschedule", Email.parent_email_id == ev.email_id,
                                                    col(Email.status).in_(["draft", "approved", "sent"]))).first()
        if existing:
            return {"email_id": existing.id, "already": True}
        when = _fmt(ev.starts_at, ev.timezone)
        # never name the other company to this one — "another interview" is the truth and enough
        first = (contact.name or "there").split()[0] if contact and contact.name else "there"
        body = (
            f"Thank you for offering {when}. I have another interview at that hour that I cannot move. "
            f"Could we do 30 minutes later the same day, or the same time the next working day? "
            f"I will keep whichever you prefer."
        )
        other_when = _fmt(other.starts_at, other.timezone) if other and other.starts_at else "another event"
        draft = Email(
            contact_id=ev.contact_id, company_id=ev.company_id, job_id=email.job_id if email else None,
            to_email=email.to_email if email else (contact.email or ""),
            subject=(email.subject if email and email.subject.lower().startswith("re:") else f"Re: {email.subject}") if email else "Re: scheduling",
            body=_compose(body, first), kind="reschedule", parent_email_id=ev.email_id, status="draft",
            gmail_thread_id=email.gmail_thread_id if email else None, candidate_id=ev.candidate_id,
            review_flags=json.dumps([{"kind": "conflict", "detail": f"clashes with {other_when}; check the calendar before approving"}]),
            expires_at=utcnow() + timedelta(days=2),
        )
        session.add(draft)
        session.commit()
        session.refresh(draft)
        return {"email_id": draft.id, "subject": draft.subject}


def _fmt(dt: datetime | None, tz: str | None) -> str:
    if not dt:
        return "the proposed time"
    try:
        from zoneinfo import ZoneInfo

        local = dt.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(LOCAL_TZ))
        return local.strftime("%A %d %B, %H:%M") + f" {LOCAL_TZ.split('/')[-1]}"
    except Exception:  # noqa: BLE001
        return dt.strftime("%Y-%m-%d %H:%M UTC")


def _notify(events: list[dict], company: str, contact: str) -> None:
    try:
        from jobhunter import notify

        for e in events:
            if "event_id" not in e:
                continue
            when = _fmt(e.get("starts_at"), None) if e.get("starts_at") else "time not stated"
            title = f"{company}: {e['kind']}" + (" — CONFLICT" if e["status"] == "conflict" else "")
            notify.send(title, f"{contact} · {when}" + (f" · {e['link']}" if e.get("link") else ""))
            with get_session() as session:
                ev = session.get(Event, e["event_id"])
                if ev:
                    ev.notified_at = utcnow()
                    session.add(ev)
                    session.commit()
    except Exception as ex:  # noqa: BLE001 — a notification must never break scheduling
        log.debug("notify failed: %s", ex)


def confirm(event_id: int, *, push: bool = True) -> dict:
    """The human confirms a proposed time; it goes on the calendar if one is wired."""
    init_db()
    with get_session() as session:
        ev = session.get(Event, event_id)
        if ev is None:
            return {"error": f"no event {event_id}"}
        ev.status = "confirmed"
        session.add(ev)
        session.commit()
    result: dict = {"event_id": event_id, "status": "confirmed"}
    if push:
        from jobhunter.outreach import calendar as cal

        result["calendar"] = cal.push(event_id)
    return result


def list_events(*, upcoming_only: bool = False, limit: int = 100) -> list[dict]:
    init_db()
    with get_session() as session:
        q = select(Event).order_by(col(Event.starts_at).is_(None), col(Event.starts_at))
        rows = session.exec(q).all()
        companies = {c.id: c.name for c in session.exec(select(Company)).all()}
        contacts = {c.id: c for c in session.exec(select(Contact)).all()}
        out = []
        now = utcnow()
        for e in rows:
            if upcoming_only and e.starts_at and e.starts_at < now - timedelta(hours=2):
                continue
            c = contacts.get(e.contact_id)
            out.append({
                "id": e.id, "kind": e.kind, "status": e.status, "company": companies.get(e.company_id),
                "company_id": e.company_id, "contact": c.name if c else None, "email_id": e.email_id,
                "starts_at": e.starts_at.isoformat() if e.starts_at else None,
                "ends_at": e.ends_at.isoformat() if e.ends_at else None,
                "local": _fmt(e.starts_at, e.timezone) if e.starts_at else None,
                "timezone": e.timezone, "link": e.link, "deadline": e.deadline.isoformat() if e.deadline else None,
                "quote": e.quote, "needs_action": e.needs_action, "conflict_with": e.conflict_with,
                "calendar_event_id": e.calendar_event_id, "created_at": e.created_at.isoformat(),
            })
        return out[:limit]


__all__ = ["extract", "events_from_reply", "draft_reschedule", "confirm", "list_events"]
