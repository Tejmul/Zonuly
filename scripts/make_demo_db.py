#!/usr/bin/env python3
"""Build the read-only snapshot the public instance serves.

The working database is ~52 MB, and almost none of that is what a visitor came to see:
`research_cache` holds whole fetched pages, `llm_call` is a spend ledger, and `reply`
is real inbound mail from real people. None of it belongs on a public URL, and dropping
it takes the file to a size that can simply live in the image — which means the deployed
instance needs no volume, no upload step and no writable disk at all.

What survives is the exhibit: the companies, the roles, the leads and the graph.

    python scripts/make_demo_db.py            # -> demo.db
    python scripts/make_demo_db.py --out x.db

Re-run it and redeploy whenever you want the public numbers to catch up with yours.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: Emptied rather than dropped, so the schema still matches what the models expect and
#: the API keeps returning [] instead of raising.
CLEARED = (
    "research_cache",  # whole cached pages; megabytes, and none of it is the product
    "llm_call",        # what every model call cost — an operator's business, not a visitor's
    "reply",           # real inbound mail from real people
)


def build(src: Path, out: Path) -> dict:
    if not src.exists():
        sys.exit(f"no database at {src}")
    if out.exists():
        out.unlink()

    shutil.copy2(src, out)
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
        # VACUUM outside a transaction; without it the pages are freed but the file is not.
        con.isolation_level = None
        con.execute("VACUUM")

        kept = {}
        for table in ("company", "job", "contact", "email", "kg_node", "kg_edge"):
            if table in existing:
                kept[table] = con.execute(f'select count(*) from "{table}"').fetchone()[0]
    finally:
        con.close()

    return {
        "out": str(out),
        "mb": round(out.stat().st_size / 1_048_576, 1),
        "was_mb": round(src.stat().st_size / 1_048_576, 1),
        "cleared": cleared,
        "kept": kept,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", type=Path, default=ROOT / "jobhunter.db")
    ap.add_argument("--out", type=Path, default=ROOT / "demo.db")
    args = ap.parse_args()

    result = build(args.src, args.out)
    print(f"{result['out']}  {result['was_mb']} MB -> {result['mb']} MB")
    for table, n in result["cleared"].items():
        print(f"  cleared {table:16} {n:>6} rows")
    for table, n in result["kept"].items():
        print(f"  kept    {table:16} {n:>6} rows")


if __name__ == "__main__":
    main()
