"""macOS notifications for the things worth interrupting the user about.

Mail (positive replies, URGENT alerts, the morning digest, reply-to-act commands) is
jobhunter/notify_bot.py; `urgent()` below does both.
"""

from __future__ import annotations

import logging
import shutil
import subprocess

from sqlmodel import col, select

from jobhunter import CONFIG
from jobhunter.db import Company, Email, Job, Reply, get_session, init_db

log = logging.getLogger(__name__)

ENABLED = bool((CONFIG.get("notifications") or {}).get("macos", True))


def _escape(text: str) -> str:
    return (text or "").replace("\\", "\\\\").replace('"', '\\"')[:200]


def send(title: str, message: str, subtitle: str = "") -> bool:
    """Fire a native notification. Silently no-ops off macOS or when disabled."""
    if not ENABLED or not shutil.which("osascript"):
        log.debug("notification suppressed: %s / %s", title, message)
        return False
    script = f'display notification "{_escape(message)}" with title "{_escape(title)}"'
    if subtitle:
        script += f' subtitle "{_escape(subtitle)}"'
    script += " sound name \"Ping\""
    try:
        subprocess.run(["osascript", "-e", script], check=True, capture_output=True, timeout=10)
        return True
    except Exception as e:  # noqa: BLE001 — notifications must never break the pipeline
        log.debug("osascript failed: %s", e)
        return False


def notify_high_matches(limit: int = 5) -> int:
    """Announce newly-scored high matches, once each."""
    init_db()
    with get_session() as session:
        jobs = session.exec(
            select(Job).where(Job.status == "high_match", Job.notified == False)  # noqa: E712
            .order_by(col(Job.match_score).desc()).limit(limit)
        ).all()
        if not jobs:
            return 0

        if len(jobs) == 1:
            j = jobs[0]
            pay = f" · {j.salary_min_lpa:.0f}-{j.salary_max_lpa:.0f} LPA" if j.salary_min_lpa else ""
            send("New high match", f"{j.title} at {j.company_name}{pay}", f"{j.match_score}% shortlist odds")
        else:
            top = jobs[0]
            send(
                f"{len(jobs)} new high matches",
                f"Top: {top.title} at {top.company_name} ({top.match_score}%)",
                "Open the dashboard to review",
            )

        for j in jobs:
            j.notified = True
            session.add(j)
        session.commit()
        return len(jobs)


def notify_replies(limit: int = 5) -> int:
    """Announce new replies, leading with the positive ones."""
    init_db()
    with get_session() as session:
        replies = session.exec(
            select(Reply).where(Reply.notified == False).order_by(col(Reply.received_at).desc()).limit(limit)  # noqa: E712
        ).all()
        if not replies:
            return 0

        for r in replies:
            email = session.get(Email, r.email_id)
            company = session.get(Company, email.company_id) if email else None
            who = company.name if company else (email.to_email if email else "someone")
            icon = {"positive": "Positive reply", "negative": "Declined", "closed": "Closed"}.get(
                r.sentiment or "neutral", "New reply"
            )
            send(f"{icon} from {who}", (r.sentiment_reason or r.body[:120]), r.from_addr[:60])
            r.notified = True
            session.add(r)
        session.commit()
        return len(replies)


def notify_queue(threshold: int = 1) -> int:
    """Remind the user that drafts are waiting for review."""
    init_db()
    from sqlmodel import func

    with get_session() as session:
        n = session.exec(
            select(func.count()).select_from(Email).where(Email.status == "draft")
        ).one()
    if n >= threshold:
        send("Review queue", f"{n} draft{'s' if n != 1 else ''} waiting for your approval")
    return n


def urgent(key: str, title: str, detail: str) -> dict:
    """An emergency: this macOS notification plus an URGENT mail (notify_bot), at most one
    per `key` per notify.urgent_dedup_hours. Mail only when notify.email is on."""
    from jobhunter import notify_bot

    if not notify_bot.ENABLED:
        send("ZoNuLy: urgent", title)
        return {"skipped": "notify.email is off"}
    return notify_bot.urgent(key, title, detail)
