"""The send ledger — 25 a day, spent irreversibly, inside the send.

A count "since midnight" can drift: two processes read 24, both send, 26 went out.
Here the day's row is locked with BEGIN IMMEDIATE, the slot is taken before the
mail leaves, and a failed send gives it back. Pattern-guessed addresses have their
own smaller cap: a bounce is what gets a personal Gmail flagged.
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


def _row(c: sqlite3.Connection, candidate_id: int, day: str) -> tuple:
    row = c.execute("select id, cap, used, guessed_cap, guessed_used from sendledger where candidate_id=? and day=?",
                    (candidate_id, day)).fetchone()
    if row is None:
        c.execute("insert into sendledger (candidate_id, day, cap, used, guessed_cap, guessed_used, updated_at) "
                  "values (?, ?, ?, 0, ?, 0, datetime('now'))", (candidate_id, day, DAILY_CAP, GUESSED_CAP))
        row = c.execute("select id, cap, used, guessed_cap, guessed_used from sendledger where candidate_id=? and day=?",
                        (candidate_id, day)).fetchone()
    return row


def status(candidate_id: int = 1) -> dict:
    with _conn() as c:
        _, cap, used, gcap, gused = _row(c, candidate_id, _today())
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


__all__ = ["status", "reserve", "release", "DAILY_CAP", "GUESSED_CAP"]
