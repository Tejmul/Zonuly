"""Failure markers with backoff, per company and per step.

Before 2026-10-06 a step that failed on a company left no trace: enrich re-read the same
bot-walled site every night, facts re-searched the same unfindable name, and because the
queues sorted by name the same failures came first every time. Now each failure is written
to `company.retry_state` (JSON: {step: {"tries", "next", "error"}}) and the queues skip a
company until its `next` time — 1, 3, 7, 21, then 60 days. A success clears the step.

Layering: db only.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from jobhunter.db import utcnow

DELAYS_DAYS = (1, 3, 7, 21, 60)


def _state(company) -> dict:
    try:
        data = json.loads(company.retry_state or "{}")
        return data if isinstance(data, dict) else {}
    except (TypeError, ValueError):
        return {}


def due(company, step: str, now: datetime | None = None) -> bool:
    """May this step run on this company now?"""
    entry = _state(company).get(step)
    if not entry or not entry.get("next"):
        return True
    try:
        return datetime.fromisoformat(entry["next"]) <= (now or utcnow())
    except ValueError:
        return True


def tries(company, step: str) -> int:
    return int((_state(company).get(step) or {}).get("tries") or 0)


def fail(company, step: str, error: str, *, retryable: bool = True) -> None:
    """Record a failure. A non-retryable one waits the longest delay straight away."""
    state = _state(company)
    entry = state.get(step) or {}
    n = int(entry.get("tries") or 0) + 1
    days = DELAYS_DAYS[min(n, len(DELAYS_DAYS)) - 1] if retryable else DELAYS_DAYS[-1]
    state[step] = {"tries": n, "next": (utcnow() + timedelta(days=days)).isoformat(timespec="seconds"),
                   "error": (error or "")[:200], "at": utcnow().isoformat(timespec="seconds")}
    company.retry_state = json.dumps(state)


def ok(company, step: str) -> None:
    """Clear a step after it worked."""
    state = _state(company)
    if step in state:
        state.pop(step)
        company.retry_state = json.dumps(state) if state else None


def last_error(company, step: str) -> str | None:
    return (_state(company).get(step) or {}).get("error")


__all__ = ["due", "fail", "ok", "tries", "last_error", "DELAYS_DAYS"]
