"""Google Calendar — the same OAuth client and token as Gmail.

Exists so a confirmed event lands on the calendar the interviews arrive at. Until the
OAuth client JSON is in place this module reports `configured: false` and every push
returns that reason instead of failing; nothing upstream depends on it succeeding.
"""

from __future__ import annotations

import logging
from datetime import timedelta, timezone

from jobhunter import CONFIG
from jobhunter.db import Company, Contact, Event, get_session, init_db
from jobhunter.outreach import gmail

log = logging.getLogger(__name__)

_C = CONFIG.get("calendar") or {}
ENABLED = bool(_C.get("enabled", True))
CALENDAR_ID = str(_C.get("calendar_id", "primary"))
LOCAL_TZ = str(_C.get("timezone", "Asia/Kolkata"))


def status() -> dict:
    g = gmail.status()
    return {
        "enabled": ENABLED, "configured": g["configured"], "authorized": g["authorized"],
        "calendar_id": CALENDAR_ID, "timezone": LOCAL_TZ,
        "hint": None if g["authorized"] else
        "Same client as Gmail: drop the OAuth JSON into secrets/ and run `python scripts/run.py gmail-auth` — the consent screen now asks for calendar.events too.",
    }


def _service():
    from googleapiclient.discovery import build

    creds = gmail._load_credentials(interactive=False)
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def push(event_id: int) -> dict:
    """Create (or update) the calendar entry for one Event. Never raises."""
    if not ENABLED:
        return {"skipped": "calendar disabled in config"}
    init_db()
    with get_session() as session:
        ev = session.get(Event, event_id)
        if ev is None:
            return {"error": f"no event {event_id}"}
        if not ev.starts_at:
            return {"skipped": "event has no time yet"}
        company = session.get(Company, ev.company_id)
        contact = session.get(Contact, ev.contact_id)
        start = ev.starts_at.replace(tzinfo=timezone.utc)
        end = (ev.ends_at or (ev.starts_at + timedelta(minutes=30))).replace(tzinfo=timezone.utc)
        body = {
            "summary": f"{company.name if company else 'Company'} — {ev.kind}" + (f" with {contact.name}" if contact and contact.name else ""),
            "description": "\n".join(filter(None, [
                f"From ZoNuLy · {ev.kind}", ev.link and f"Link: {ev.link}", ev.quote and f"They wrote: “{ev.quote}”",
                ev.needs_action and f"To do: {ev.needs_action}",
            ])),
            "start": {"dateTime": start.isoformat(), "timeZone": "UTC"},
            "end": {"dateTime": end.isoformat(), "timeZone": "UTC"},
            "reminders": {"useDefault": False, "overrides": [{"method": "popup", "minutes": 60}, {"method": "popup", "minutes": 10}]},
        }
        existing = ev.calendar_event_id
    try:
        svc = _service()
        if existing:
            created = svc.events().update(calendarId=CALENDAR_ID, eventId=existing, body=body).execute()
        else:
            created = svc.events().insert(calendarId=CALENDAR_ID, body=body).execute()
    except gmail.GmailNotConfigured as e:
        return {"skipped": str(e)}
    except Exception as e:  # noqa: BLE001
        log.warning("calendar push failed for event %s: %s", event_id, e)
        return {"error": str(e)[:200]}
    with get_session() as session:
        ev = session.get(Event, event_id)
        ev.calendar_event_id = created.get("id")
        session.add(ev)
        session.commit()
    return {"calendar_event_id": created.get("id"), "html_link": created.get("htmlLink")}


__all__ = ["status", "push", "ENABLED"]
