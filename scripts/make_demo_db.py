#!/usr/bin/env python3
"""Build the read-only snapshot the public instance serves.

The working database is ~52 MB, and almost none of that is what a visitor came to see:
`research_cache` holds whole fetched pages, `llm_call` is a spend ledger, and `reply`
is real inbound mail from real people. None of it belongs on a public URL, and dropping
it takes the file to a size that can simply live in the image — which means the deployed
instance needs no volume, no upload step and no writable disk at all.

What survives is the exhibit: the companies, the roles, the leads and the graph.

    python scripts/make_demo_db.py            # -> demo.db and demo.db.gz (the one committed)
    python scripts/make_demo_db.py --out x.db

Re-run it and redeploy whenever you want the public numbers to catch up with yours.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: Emptied rather than dropped, so the schema still matches what the models expect and
#: the API keeps returning [] instead of raising.
CLEARED = (
    "research_cache",  # whole cached pages; megabytes, and none of it is the product
    "llm_call",        # what every model call cost — an operator's business, not a visitor's
    "reply",           # real inbound mail from real people
    "upskillitem",     # your own learning notes and proof links (upskill.py)
    "decisionoutcome", # what really happened at a company: interviews, offers, rejections
    "mixerscrape",     # what each paid Apify lookup cost — the operator's ledger, like llm_call
    "extraction_reject",  # the scrape audit trail: ~16 MB the public page never shows
    "extraction_write",   # (2026-10-23: the snapshot outgrew AppSail's 512 MB with them in)
    "shortlistfill",      # the shortlist job's work log, with its costs — the operator's
)


_ADDRESS = re.compile(r"([A-Za-z0-9._%+\-]+)@([A-Za-z0-9.\-]+\.[A-Za-z]{2,})")


def _stand_in(m: re.Match) -> str:
    """A real address → a stand-in on the same domain: first letter + 7 hex of its hash. The
    domain is the company's (public, and half the evidence); the local part is what makes a
    row mailable, so it never leaves this machine. Deterministic, so one address is one
    stand-in everywhere it appears, and ASCII, so the address checks still pass and the
    public site (which shows p•••@domain anyway) looks exactly as before."""
    local, domain = m.group(1), m.group(2)
    return f"{local[:1].lower()}{hashlib.sha1(m.group(0).lower().encode()).hexdigest()[:7]}@{domain.lower()}"


def mask_addresses(con: sqlite3.Connection) -> dict:
    """2026-10-23: demo.db.gz sits in a public repository, so masking at the API was not
    enough — the file itself carried 25,060 contact addresses. Every text column of every
    table (the knowledge graph's search index included) has its addresses replaced."""
    con.create_function("mask_addresses", 1, lambda v: _ADDRESS.sub(_stand_in, v) if isinstance(v, str) else v,
                        deterministic=True)
    out = {}
    tables = [r[0] for r in con.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%' "
                                        "and name not like 'kg_fts_%'")]
    for table in tables:
        cols = [r[1] for r in con.execute(f'pragma table_info("{table}")')
                if (r[2] or "").upper() in ("", "TEXT", "VARCHAR", "CHAR", "JSON") or "CHAR" in (r[2] or "").upper()]
        for col in cols:
            n = con.execute(f'update "{table}" set "{col}" = mask_addresses("{col}") '
                            f'where "{col}" like \'%@%.%\' and "{col}" != mask_addresses("{col}")').rowcount
            if n:
                out[f"{table}.{col}"] = n
    con.commit()
    return out


def build(src: Path, out: Path) -> dict:
    if not src.exists():
        sys.exit(f"no database at {src}")
    if out.exists():
        out.unlink()

    # The backup API, not a file copy: the hourly tick may be mid-write on the source,
    # and a copied file can catch half a transaction.
    with sqlite3.connect(f"file:{src}?mode=ro", uri=True) as live, sqlite3.connect(out) as copy:
        live.backup(copy)
    con = sqlite3.connect(out)
    try:
        existing = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
        cleared = {}
        for table in CLEARED:
            if table not in existing:
                continue
            before = con.execute(f'select count(*) from "{table}"').fetchone()[0]
            con.execute(f'delete from "{table}"')
            cleared[table] = before
        con.commit()
        masked = mask_addresses(con)
        # VACUUM outside a transaction; without it the pages are freed but the file is not.
        con.isolation_level = None
        con.execute("VACUUM")

        kept = {}
        for table in ("company", "job", "contact", "email", "kg_node", "kg_edge"):
            if table in existing:
                kept[table] = con.execute(f'select count(*) from "{table}"').fetchone()[0]
    finally:
        con.close()

    stamp = _finish(out)
    # the app's engine runs the working file in WAL; the baked image wants one self-contained file
    with sqlite3.connect(out) as plain:
        plain.execute("PRAGMA journal_mode=DELETE")

    return {
        "stamp": stamp,
        "out": str(out),
        "mb": round(out.stat().st_size / 1_048_576, 1),
        "was_mb": round(src.stat().st_size / 1_048_576, 1),
        "cleared": cleared,
        "kept": kept,
        "masked": masked,
    }


#: Run against the snapshot in a child process: the engine binds to ZONULY_DB_PATH at
#: import, so this process (already bound to nothing in particular) cannot retarget it.
_FINISH = r"""
import json, sys
from datetime import datetime, timezone
from jobhunter import lists, snapshot
from jobhunter.db import get_session, init_db, set_setting

init_db()                      # every column the code declares, so the API never has to
expected = {}
for row in lists.available():  # re-attach every hand-picked list to this copy
    lists.sync(row["key"], verify_sites=False)
    expected[row["key"]] = row["companies"]
problems = [f"list {r['key']}: {r['attached']} of {expected[r['key']]} attached"
            for r in lists.available() if r["attached"] < expected[r["key"]]]
stamp = {
    "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "git_sha": snapshot.git_sha(),
    "schema": snapshot.schema_signature(),
    "lists": expected,
}
with get_session() as s:
    set_setting(s, snapshot.META_KEY, json.dumps(stamp))
    s.commit()
print(json.dumps({"stamp": stamp, "problems": problems}))
"""


def _finish(out: Path) -> dict:
    """Bring the copy up to the code, stamp it, and refuse to keep it if a list is short."""
    env = {**os.environ, "ZONULY_DB_PATH": str(out)}
    proc = subprocess.run([sys.executable, "-c", _FINISH], cwd=ROOT, env=env,
                          capture_output=True, text=True)
    if proc.returncode != 0:
        out.unlink(missing_ok=True)
        sys.exit(f"snapshot not written — finishing failed:\n{proc.stderr[-2000:]}")
    result = json.loads(proc.stdout.strip().splitlines()[-1])
    if result["problems"]:
        out.unlink(missing_ok=True)
        sys.exit("snapshot not written:\n  " + "\n  ".join(result["problems"]))
    return result["stamp"]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", type=Path, default=ROOT / "jobhunter.db")
    ap.add_argument("--out", type=Path, default=ROOT / "demo.db")
    args = ap.parse_args()

    result = build(args.src, args.out)
    # What git and the image carry: GitHub refuses files over 100 MB and the snapshot has
    # outgrown that; compressed it is about a fifth. AppSail's start.sh unpacks it.
    gz = args.out.with_name(args.out.name + ".gz")
    with open(args.out, "rb") as raw, gzip.open(gz, "wb", compresslevel=9) as packed:
        shutil.copyfileobj(raw, packed)
    print(f"{gz}  {round(gz.stat().st_size / 1_048_576, 1)} MB (commit this one)")
    print(f"{result['out']}  {result['was_mb']} MB -> {result['mb']} MB")
    st = result["stamp"]
    print(f"  stamped {st['built_at']}  commit {st['git_sha']}  schema {st['schema']}")
    for key, n in st["lists"].items():
        print(f"  list    {key:16} {n:>6} companies attached")
    for table, n in result["cleared"].items():
        print(f"  cleared {table:16} {n:>6} rows")
    for table, n in result["kept"].items():
        print(f"  kept    {table:16} {n:>6} rows")
    for column, n in result["masked"].items():
        print(f"  masked  {column:28} {n:>6} rows")


if __name__ == "__main__":
    main()
