"""The send ledger — the day's cap, spent irreversibly, inside the send.

A count "since midnight" can drift: two processes read 24, both send, 26 went out.
Here the day's row is locked with BEGIN IMMEDIATE, the slot is taken before the
mail leaves, and a failed send gives it back. Pattern-guessed addresses have their
own smaller cap: a bounce is what gets a personal Gmail flagged.

2026-10-30: the cap is the sum of the sending inboxes' quotas (Gmail 25 + each domain inbox's
warm-up ramp), never above outreach.total_daily_cap; each inbox's own cap is kept by the sender.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime

from jobhunter import CONFIG
from jobhunter.db import DB_PATH

log = logging.getLogger(__name__)

_O = CONFIG.get("outreach") or {}
DAILY_CAP = int(_O.get("daily_send_cap", 25))
GUESSED_CAP = int(_O.get("guessed_daily_cap", 5))


def _today() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d")


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(str(DB_PATH), timeout=30, isolation_level=None)
    c.execute("PRAGMA busy_timeout=30000")
    return c


def effective_cap(day: str) -> int:
    """The day's cap: the sum of the sending inboxes' warm-up quotas (outreach/inboxes.py) —
    the Gmail's is outreach.warmup under daily_send_cap — never above total_daily_cap."""
    from jobhunter.db import get_session
    from jobhunter.outreach import inboxes

    with get_session() as session:
        return inboxes.total_quota(session, day)


def _row(c: sqlite3.Connection, candidate_id: int, day: str) -> tuple:
    """(id, cap, used, guessed_cap, guessed_used) for the day. The cap is the lower of the
    stored one and today's warm-up quota, and the guessed cap never exceeds the cap — a
    row written before the warm-up existed is read through the same ceiling."""
    row = c.execute("select id, cap, used, guessed_cap, guessed_used from sendledger where candidate_id=? and day=?",
                    (candidate_id, day)).fetchone()
    cap = effective_cap(day)
    if row is None:
        c.execute("insert into sendledger (candidate_id, day, cap, used, guessed_cap, guessed_used, updated_at) "
                  "values (?, ?, ?, 0, ?, 0, datetime('now'))", (candidate_id, day, cap, min(GUESSED_CAP, cap)))
        row = c.execute("select id, cap, used, guessed_cap, guessed_used from sendledger where candidate_id=? and day=?",
                        (candidate_id, day)).fetchone()
    rid, stored, used, gcap, gused = row
    if int(stored) != cap:
        # an inbox became ready (or ran out of ramp) since the row was written: today's cap follows
        c.execute("update sendledger set cap=? where id=?", (cap, rid))
    return rid, cap, used, min(int(gcap), GUESSED_CAP, cap), gused


def status(candidate_id: int = 1) -> dict:
    try:
        with _conn() as c:
            _, cap, used, gcap, gused = _row(c, candidate_id, _today())
    except sqlite3.Error as e:
        # 2026-10-24: on the deployed copy the first read of a new day must write that day's row;
        # when it cannot, report the day's cap, nothing used, and why — never take the page down.
        # Only there: on the working machine the ledger is the send cap, and a broken one stops loudly.
        from jobhunter import access

        if not access.PUBLIC:
            raise
        log.warning("send ledger unreadable: %s", e)
        cap = effective_cap(_today())
        return {"day": _today(), "cap": cap, "used": 0, "left": cap, "guessed_cap": min(GUESSED_CAP, cap),
                "guessed_used": 0, "guessed_left": min(GUESSED_CAP, cap), "error": f"{type(e).__name__}: {e}"[:200]}
    return {"day": _today(), "cap": cap, "used": used, "left": max(0, cap - used),
            "guessed_cap": gcap, "guessed_used": gused, "guessed_left": max(0, gcap - gused)}


def reserve(candidate_id: int = 1, *, guessed: bool = False) -> tuple[bool, str]:
    """Take one slot for today, atomically. (ok, reason)."""
    day = _today()
    with _conn() as c:
        c.execute("BEGIN IMMEDIATE")
        try:
            rid, cap, used, gcap, gused = _row(c, candidate_id, day)
            if used >= cap:
                c.execute("ROLLBACK")
                return False, f"daily cap reached ({used}/{cap})"
            if guessed and gused >= gcap:
                c.execute("ROLLBACK")
                return False, f"guessed-address cap reached ({gused}/{gcap}) — verified addresses only for the rest of today"
            c.execute("update sendledger set used=used+1, guessed_used=guessed_used+?, updated_at=datetime('now') where id=?",
                      (1 if guessed else 0, rid))
            c.execute("COMMIT")
        except Exception:
            c.execute("ROLLBACK")
            raise
    return True, "reserved"


def release(candidate_id: int = 1, *, guessed: bool = False) -> None:
    """A send that failed after reserving gives its slot back."""
    with _conn() as c:
        c.execute("BEGIN IMMEDIATE")
        rid, cap, used, gcap, gused = _row(c, candidate_id, _today())
        c.execute("update sendledger set used=max(0, used-1), guessed_used=max(0, guessed_used-?), updated_at=datetime('now') where id=?",
                  (1 if guessed else 0, rid))
        c.execute("COMMIT")


__all__ = ["status", "reserve", "release", "effective_cap", "DAILY_CAP", "GUESSED_CAP"]
