"""What the public instance is serving, and whether the code still agrees with it.

The deployed API reads `demo.db`, a snapshot baked into the image. Twice now the code has
moved on (new columns, a new hand-picked list) while the snapshot stayed behind, and the
public page went quietly empty. So the snapshot carries a stamp — when it was cut, from
which commit, and a signature of the schema the code expected at that moment — and
/api/health compares that stamp with the running code. A mismatch is reported, never
guessed around.
"""

from __future__ import annotations

import hashlib
import json
import subprocess

from sqlmodel import SQLModel

from jobhunter import ROOT

META_KEY = "snapshot:meta"


def schema_signature() -> str:
    """A short hash of every table.column the models declare."""
    import jobhunter.db  # noqa: F401 — registers every table on SQLModel.metadata

    cols = sorted(f"{t}.{c.name}" for t, table in SQLModel.metadata.tables.items() for c in table.columns)
    return hashlib.sha256("\n".join(cols).encode()).hexdigest()[:16]


def git_sha() -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                             capture_output=True, text=True, timeout=5)
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def read_meta() -> dict | None:
    """The stamp written by scripts/make_demo_db.py, or None on a working database."""
    from jobhunter.db import get_session, get_setting

    with get_session() as session:
        raw = get_setting(session, META_KEY)
    try:
        return json.loads(raw) if raw else None
    except json.JSONDecodeError:
        return None


def status() -> dict | None:
    """For /api/health. None when this database was never cut as a snapshot."""
    meta = read_meta()
    if meta is None:
        return None
    from jobhunter import lists

    current = schema_signature()
    return {
        "built_at": meta.get("built_at"),
        "git_sha": meta.get("git_sha"),
        "schema": meta.get("schema"),
        "schema_now": current,
        "schema_ok": meta.get("schema") == current,
        "lists": {row["key"]: row.get("attached", 0) for row in lists.available()},
        "lists_expected": meta.get("lists") or {},
    }
