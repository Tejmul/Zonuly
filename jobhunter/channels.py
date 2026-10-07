"""Channel health — is each research channel actually answering, hour by hour?

The Agent Reach channels (hosted Exa MCP, Jina Reader, GitHub, scrape.do, DuckDuckGo)
and OpenRouter fail in ways a run summary hides: the MCP cools down for five minutes and
the pass quietly falls back to domain guesses; Jina answers a Cloudflare page; GitHub runs
out of its hourly budget. Each call site records one line here — ok or fail, the error,
a cooldown — into `channel_hour` (one row per channel per local hour, an atomic upsert,
so the tick, the API and a script can all write it at once). The hourly audit
(jobhunter/audit.py) reads it back as "last success, last failure, failure rate".

Recording must never break a fetch: every write swallows its own errors.

Layering: db only.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import text

from jobhunter.db import engine

log = logging.getLogger(__name__)

_SQL = text(
    "INSERT INTO channel_hour (channel, hour, ok, fail, cooldowns, last_ok_at, last_fail_at, last_error) "
    "VALUES (:ch, :hour, :ok, :fail, :cool, :ok_at, :fail_at, :err) "
    "ON CONFLICT(channel, hour) DO UPDATE SET ok = ok + :ok, fail = fail + :fail, "
    "cooldowns = cooldowns + :cool, "
    "last_ok_at = COALESCE(:ok_at, last_ok_at), last_fail_at = COALESCE(:fail_at, last_fail_at), "
    "last_error = COALESCE(:err, last_error)"
)
_READY = False


def _hour(now: datetime | None = None) -> str:
    return (now or datetime.now()).strftime("%Y-%m-%dT%H")


def record(channel: str, ok: bool, error: str | None = None, *, cooldown: bool = False) -> None:
    """One call on one channel. Never raises."""
    global _READY
    now = datetime.now()
    stamp = now.isoformat(sep=" ", timespec="seconds")
    try:
        if not _READY:
            from jobhunter.db import ChannelHour

            ChannelHour.__table__.create(engine, checkfirst=True)
            _READY = True
        with engine.begin() as conn:
            conn.execute(_SQL, {
                "ch": channel, "hour": _hour(now), "ok": 1 if ok else 0, "fail": 0 if ok else 1,
                "cool": 1 if cooldown else 0, "ok_at": stamp if ok else None,
                "fail_at": None if ok else stamp, "err": None if ok else (error or "failed")[:300],
            })
    except Exception as e:  # noqa: BLE001 — a ledger write must never cost the fetch
        log.debug("channel record failed for %s: %s", channel, e)


def summary(hours: int = 1, *, now: datetime | None = None) -> dict[str, dict]:
    """{channel: {ok, fail, cooldowns, fail_rate, last_ok_at, last_fail_at, last_error}}
    over the last `hours` local hours (the current hour included)."""
    now = now or datetime.now()
    since = _hour(now - timedelta(hours=max(1, hours) - 1))
    out: dict[str, dict] = {}
    try:
        with engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT channel, SUM(ok), SUM(fail), SUM(cooldowns), MAX(last_ok_at), MAX(last_fail_at) "
                "FROM channel_hour WHERE hour >= :since GROUP BY channel"), {"since": since}).all()
            last = dict(conn.execute(text(
                "SELECT channel, last_error FROM channel_hour WHERE last_error IS NOT NULL "
                "AND hour = (SELECT MAX(hour) FROM channel_hour c2 WHERE c2.channel = channel_hour.channel "
                "AND c2.last_error IS NOT NULL)")).all())
            ever = {r[0]: (r[1], r[2]) for r in conn.execute(text(
                "SELECT channel, MAX(last_ok_at), MAX(last_fail_at) FROM channel_hour GROUP BY channel")).all()}
    except Exception as e:  # noqa: BLE001 — no table yet is "no data", not an error
        log.debug("channel summary unreadable: %s", e)
        return out
    for ch, ok, fail, cool, _ok_at, _fail_at in rows:
        ok, fail = int(ok or 0), int(fail or 0)
        out[ch] = {"ok": ok, "fail": fail, "cooldowns": int(cool or 0),
                   "fail_rate": round(fail / (ok + fail), 3) if ok + fail else None}
    for ch, (ok_at, fail_at) in ever.items():
        out.setdefault(ch, {"ok": 0, "fail": 0, "cooldowns": 0, "fail_rate": None})
        out[ch].update({"last_ok_at": str(ok_at)[:19] if ok_at else None,
                        "last_fail_at": str(fail_at)[:19] if fail_at else None,
                        "last_error": last.get(ch)})
    return out


__all__ = ["record", "summary"]
