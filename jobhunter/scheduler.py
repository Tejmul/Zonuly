"""APScheduler wiring — the daily loop that runs while the API server is up.

Jobs are deliberately sequential within a run: on 8 GB RAM, scraping while the
LLM is scoring is how you get a swap storm.
"""

from __future__ import annotations

import logging
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from jobhunter import CONFIG

log = logging.getLogger(__name__)

_S = CONFIG.get("scheduler") or {}
SCRAPE_HOUR = int(_S.get("scrape_hour", 8))
REPLY_POLL_MINUTES = int(_S.get("reply_poll_minutes", 60))
SCORE_BATCH = int(_S.get("score_batch", 120))

_scheduler: BackgroundScheduler | None = None
_last_runs: dict[str, dict] = {}


def _record(name: str, result: object, error: str | None = None) -> None:
    _last_runs[name] = {
        "at": datetime.now().isoformat(timespec="seconds"),
        "result": result if error is None else None,
        "error": error,
    }


def daily_cycle() -> dict:
    """Scrape -> salary backfill -> embed -> score -> notify. The whole ingestion loop."""
    from jobhunter import matcher, notify, pipeline

    summary: dict = {}
    try:
        # The launchd tick now owns the daily board refresh, because it survives sleep and
        # this scheduler does not. If it has already scraped today, do not do it again -
        # the scoring half below is what this job still uniquely provides.
        from jobhunter.db import get_session, get_setting
        from jobhunter.tick import FULL_SCRAPE_KEY

        with get_session() as session:
            done_today = get_setting(session, FULL_SCRAPE_KEY) == datetime.now().strftime("%Y-%m-%d")
        summary["scrape"] = {"skipped": "tick already scraped today"} if done_today else pipeline.scrape().as_dict()
        summary["salaries"] = pipeline.extract_salaries(limit=40)
        summary["embedded"] = matcher.prefilter(limit=2000)
        summary["scored"] = matcher.score_pending(limit=SCORE_BATCH)
        summary["notified"] = notify.notify_high_matches()
        _record("daily_cycle", summary)
    except Exception as e:  # noqa: BLE001 — a failed run must not kill the scheduler thread
        log.exception("daily cycle failed")
        _record("daily_cycle", summary, str(e)[:300])
        summary["error"] = str(e)[:300]
    summary["kg"] = _sync_graph()
    return summary


def _sync_graph() -> dict:
    """Mirror the tables into the knowledge graph. Never fails a cycle — the graph is derived."""
    from jobhunter.kg import brief, sync

    try:
        out = sync.sync_all()
        brief.write()
        return out
    except Exception as e:  # noqa: BLE001
        log.exception("knowledge graph sync failed")
        return {"error": str(e)[:300]}


def reply_cycle() -> dict:
    """Poll Gmail, classify replies, queue follow-ups, notify."""
    from jobhunter import notify
    from jobhunter.outreach import followup, tracker

    summary: dict = {}
    try:
        summary["poll"] = tracker.poll()
        # model first, the fixed template when the free models are out; drafts, never approved
        summary["followups"] = followup.queue().get("drafted", 0)
        summary["notified"] = notify.notify_replies()
        summary["mailed"] = _mail_positive()
        _record("reply_cycle", summary)
    except Exception as e:  # noqa: BLE001
        log.exception("reply cycle failed")
        _record("reply_cycle", summary, str(e)[:300])
        summary["error"] = str(e)[:300]
    summary["kg"] = _sync_graph()
    return summary


def _mail_positive() -> dict:
    """A positive reply found by this poll is mailed now (notify_bot), not at the next send
    tick. Deduplicated with the tick's runs through data/notify_state.json."""
    try:
        from jobhunter import notify_bot, tick

        if not notify_bot.ENABLED:
            return {"skipped": "notify.email is off"}
        with tick._Lock(tick.NOTIFY_LOCK, "notification run"):
            return notify_bot.email_positive_replies()
    except Exception as e:  # noqa: BLE001 — a notification must never fail the reply cycle
        log.warning("positive-reply mail failed: %s", e)
        return {"error": str(e)[:200]}


def start() -> BackgroundScheduler:
    global _scheduler
    if _scheduler and _scheduler.running:
        return _scheduler

    # No explicit timezone: tzlocal resolves the real IANA zone. Passing
    # str(tzinfo) yields an abbreviation like "IST", which zoneinfo rejects.
    _scheduler = BackgroundScheduler(
        job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 3600},
    )
    # Ten hours of grace, not one. This process freezes while the laptop sleeps; with
    # the lid shut at 08:00 and opened at 10:00 the job was two hours late, past the
    # default grace, and skipped - seven days in a row. Now a lid opened any time
    # before 18:00 still runs today's scoring. coalesce=True keeps it to one run.
    _scheduler.add_job(
        daily_cycle, CronTrigger(hour=SCRAPE_HOUR, minute=0), id="daily_cycle", name="Daily scrape + score",
        misfire_grace_time=10 * 3600,
    )
    _scheduler.add_job(
        reply_cycle,
        IntervalTrigger(minutes=REPLY_POLL_MINUTES),
        id="reply_cycle",
        name="Poll Gmail for replies",
    )
    _scheduler.start()
    log.info("scheduler started: scrape daily at %02d:00, replies every %dm", SCRAPE_HOUR, REPLY_POLL_MINUTES)
    return _scheduler


def stop() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        log.info("scheduler stopped")
    _scheduler = None


def status() -> dict:
    if not _scheduler or not _scheduler.running:
        return {"running": False, "jobs": [], "last_runs": _last_runs}
    return {
        "running": True,
        "jobs": [
            {
                "id": j.id,
                "name": j.name,
                "next_run": j.next_run_time.isoformat(timespec="seconds") if j.next_run_time else None,
            }
            for j in _scheduler.get_jobs()
        ],
        "last_runs": _last_runs,
    }
