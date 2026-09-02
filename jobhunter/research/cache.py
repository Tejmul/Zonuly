"""A TTL cache for acquired pages and search results, in the same SQLite file.

Web research is the slow, rate-limited, occasionally-paid part of the pipeline, and
the same company gets researched again every time a new posting from it shows up.
One table keeps a day's worth of answers so a re-run costs nothing.

Raw sqlite3 with CREATE TABLE IF NOT EXISTS, exactly like `kg.store` — the research
layer owns its own table and `db.py` stays untouched.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from jobhunter import CONFIG
from jobhunter.db import DB_PATH

log = logging.getLogger(__name__)

TTL_HOURS = int((CONFIG.get("research") or {}).get("cache_ttl_hours", 24))

SCHEMA = """
CREATE TABLE IF NOT EXISTS research_cache (
    key        TEXT PRIMARY KEY,
    kind       TEXT NOT NULL,          -- search | page | github | reddit | youtube | company
    subject    TEXT NOT NULL,          -- the query or URL, for eyeballing the table
    backend    TEXT,                   -- which backend actually answered
    payload    TEXT NOT NULL,          -- JSON
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_research_cache_kind ON research_cache(kind, created_at);
"""

_READY = False


def _conn() -> sqlite3.Connection:
    global _READY
    conn = sqlite3.connect(DB_PATH, timeout=15)
    if not _READY:
        conn.executescript(SCHEMA)
        conn.commit()
        _READY = True
    return conn


def _key(kind: str, subject: str, **params: Any) -> str:
    raw = json.dumps([kind, subject.strip().lower(), params], sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def get(kind: str, subject: str, *, ttl_hours: int | None = None, **params: Any) -> dict | None:
    """A fresh cached payload, or None. A cache miss is never an error."""
    ttl = TTL_HOURS if ttl_hours is None else ttl_hours
    if ttl <= 0:
        return None
    try:
        with _conn() as conn:
            row = conn.execute(
                "SELECT payload, created_at, backend FROM research_cache WHERE key = ?",
                (_key(kind, subject, **params),),
            ).fetchone()
    except sqlite3.Error as e:  # noqa: BLE001 — the cache must never break a run
        log.debug("cache read failed: %s", e)
        return None
    if not row:
        return None
    payload, created_at, backend = row
    try:
        age = datetime.now(timezone.utc).replace(tzinfo=None) - datetime.fromisoformat(created_at)
    except ValueError:
        return None
    if age > timedelta(hours=ttl):
        return None
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None
    if isinstance(data, dict):
        data.setdefault("cached", True)
        data.setdefault("backend", backend)
    return data


def put(kind: str, subject: str, payload: dict, *, backend: str = "", **params: Any) -> None:
    if TTL_HOURS <= 0:
        return
    try:
        with _conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO research_cache (key, kind, subject, backend, payload, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    _key(kind, subject, **params),
                    kind,
                    subject[:500],
                    backend,
                    json.dumps(payload, default=str),
                    datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                ),
            )
            conn.commit()
    except sqlite3.Error as e:  # noqa: BLE001
        log.debug("cache write failed: %s", e)


def purge(older_than_hours: int | None = None) -> int:
    """Drop stale rows. Returns how many went."""
    ttl = TTL_HOURS if older_than_hours is None else older_than_hours
    cutoff = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=ttl)).isoformat()
    try:
        with _conn() as conn:
            cur = conn.execute("DELETE FROM research_cache WHERE created_at < ?", (cutoff,))
            conn.commit()
            return cur.rowcount or 0
    except sqlite3.Error as e:  # noqa: BLE001
        log.debug("cache purge failed: %s", e)
        return 0


def stats() -> dict:
    try:
        with _conn() as conn:
            rows = conn.execute(
                "SELECT kind, COUNT(*) FROM research_cache GROUP BY kind ORDER BY 2 DESC"
            ).fetchall()
            total = conn.execute("SELECT COUNT(*) FROM research_cache").fetchone()[0]
    except sqlite3.Error as e:  # noqa: BLE001
        return {"error": str(e)}
    return {"total": total, "by_kind": dict(rows), "ttl_hours": TTL_HOURS}
