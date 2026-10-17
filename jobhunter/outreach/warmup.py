"""Warm-up and pacing — how many mails a day, and at what minutes, from a fresh sender.

A personal Gmail that has never sent cold mail and suddenly sends 25 a day looks like
what it is. So the first days send few and the quota climbs (config `outreach.warmup`):

    quota(day) = min(daily_send_cap, first + step * (n // every))

where n counts the sending days (outreach.send_days, Mon–Fri by default) from
`warmup.start` up to `day`. With first 5, step 5, every 2 and a start on Monday
2026-10-19: Mon 5, Tue 5, Wed 10, Thu 10, Fri 15, then 15, 20, 20, 25.

Inside a day the quota is spread across the send window at jittered times — `slots()`
— so the send agent sends one or two at a time, minutes apart, never a burst. The slots
are seeded by the date, so every run of the agent on one day agrees on them.

Pure functions over dates; the ledger and the sender call them. Nothing here can raise
the 25/day cap: the result is always ≤ daily_send_cap.
"""

from __future__ import annotations

import random
from datetime import date, datetime, timedelta

from jobhunter import CONFIG

_O = CONFIG.get("outreach") or {}
_W = _O.get("warmup") or {}
DAILY_CAP = int(_O.get("daily_send_cap", 25))
SEND_WINDOW = tuple(int(x) for x in (_O.get("send_window") or [10, 19]))
_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
SEND_DAYS = tuple(str(d).lower()[:3] for d in (_O.get("send_days") or _DAYS))
_P = _O.get("pacing") or {}
JITTER = float(_P.get("jitter_fraction", 0.35))
MAX_PER_RUN = int(_P.get("max_per_run", 2))
FRONT_LOAD = bool(_P.get("front_load", False))


def _as_date(d: date | datetime | str | None) -> date:
    if d is None:
        return datetime.now().date()
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    return datetime.strptime(str(d)[:10], "%Y-%m-%d").date()


def start() -> date | None:
    raw = _W.get("start")
    try:
        return _as_date(raw) if raw else None
    except ValueError:
        return None


def is_send_day(d: date | datetime | str | None = None) -> bool:
    return _DAYS[_as_date(d).weekday()] in SEND_DAYS


def sending_day_index(d: date | datetime | str | None = None) -> int:
    """How many sending days lie between the warm-up start and `d` (start = 0). A
    non-sending day counts as the sending day before it. Before the start: 0."""
    day, s = _as_date(d), start()
    if s is None or day <= s:
        return 0
    before = sum(1 for i in range((day - s).days) if is_send_day(s + timedelta(days=i)))
    return before if is_send_day(day) else max(0, before - 1)


def quota(d: date | datetime | str | None = None, *, cap: int | None = None) -> int:
    """Today's warm-up quota, never above the daily cap. Without a warm-up configured the
    quota IS the cap. Before the start date the first step applies (a test send on the
    Friday before launch is still a fresh sender's first mail)."""
    cap = DAILY_CAP if cap is None else int(cap)
    if not _W or start() is None:
        return cap
    first, step, every = int(_W.get("first", 5)), int(_W.get("step", 5)), max(1, int(_W.get("every", 1)))
    return max(0, min(cap, first + step * (sending_day_index(d) // every)))


def schedule(days: int = 10, *, from_day: date | datetime | str | None = None) -> list[dict]:
    """The next `days` calendar days: {day, weekday, send_day, quota}."""
    d0 = _as_date(from_day or start() or datetime.now().date())
    out = []
    for i in range(days):
        d = d0 + timedelta(days=i)
        out.append({"day": d.isoformat(), "weekday": d.strftime("%a"), "send_day": is_send_day(d),
                    "quota": quota(d) if is_send_day(d) else 0})
    return out


def slots(d: date | datetime | str | None, n: int, *, window: tuple[int, int] | None = None,
          jitter: float | None = None) -> list[datetime]:
    """`n` send times on day `d`, spread evenly across the window, each moved by up to
    ±jitter × the gap — deterministic per day (seeded by the date), sorted, inside the
    window. Local naive datetimes."""
    if n <= 0:
        return []
    day = _as_date(d)
    lo, hi = window or SEND_WINDOW
    jitter = JITTER if jitter is None else jitter
    begin = datetime(day.year, day.month, day.day, int(lo))
    end = datetime(day.year, day.month, day.day, int(hi))
    span = (end - begin).total_seconds()
    gap = span / n
    rng = random.Random(f"zonuly-send-{day.isoformat()}")
    out = []
    for i in range(n):
        centre = gap * (i + 0.5)
        t = centre + rng.uniform(-jitter, jitter) * gap
        t = min(max(t, 60.0), span - 60.0)          # never on the edge of the window
        out.append(begin + timedelta(seconds=round(t)))
    return sorted(out)


def due(now: datetime | None = None, *, cap: int | None = None) -> int:
    """How many of today's slots have come due by `now` (0 on a non-sending day)."""
    now = now or datetime.now()
    if not is_send_day(now):
        return 0
    if FRONT_LOAD:
        lo, hi = SEND_WINDOW
        return quota(now, cap=cap) if lo <= now.hour < hi else 0
    return sum(1 for t in slots(now, quota(now, cap=cap)) if t <= now)


def describe(d: date | datetime | str | None = None) -> dict:
    day = _as_date(d)
    q = quota(day) if is_send_day(day) else 0
    return {"day": day.isoformat(), "send_day": is_send_day(day), "quota": q, "cap": DAILY_CAP,
            "warmup_start": start().isoformat() if start() else None,
            "slots": [t.strftime("%H:%M") for t in slots(day, q)]}


__all__ = ["quota", "schedule", "slots", "due", "is_send_day", "sending_day_index", "describe", "start",
           "SEND_DAYS", "MAX_PER_RUN"]
