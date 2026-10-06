#!/usr/bin/env python
"""Clean the company registry: names, stages, duplicates, junk, overrated, priority.

    python scripts/cleanup_companies.py              # dry run (the default): reads, prints the plan
    python scripts/cleanup_companies.py --apply      # carry it out
    python scripts/cleanup_companies.py --json       # the full summary as JSON

What it does is jobhunter/hygiene.py: re-clean headline names, clear websites that are a
job board / form / shortener or an apply-link host shared by strangers, normalise funding
stages ("Series A / YC" -> "Series A"), read card totals and drop revenue misread as a
round, merge duplicates (jobs, contacts, emails and events move to the survivor), and
quarantine — never delete — junk and bare rows nothing can rescue. Then overrated +
priority + city are regraded for every row. Rows on a hand-picked list (list_key) are
never touched.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobhunter import hygiene  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", default=True, help="report only (default)")
    mode.add_argument("--apply", action="store_true", help="write the changes")
    ap.add_argument("--json", action="store_true", help="print the full summary as JSON")
    ap.add_argument("--samples", type=int, default=6)
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    out = hygiene.run(dry_run=not args.apply)
    if args.json:
        print(json.dumps(out, indent=2, default=str))
        return 0
    plan = out["plan"]
    print(f"{'DRY RUN — nothing written' if out['dry_run'] else 'APPLIED'}  "
          f"({plan['companies']} companies; {plan['list_rows_untouched']} list rows untouched)")
    for key in ("renames", "merges", "quarantine", "websites_cleared", "stages_normalised",
                "funding_repaired", "bot_wall_descriptions", "overrated"):
        block = plan[key]
        extra = {k: v for k, v in block.items() if k not in ("count", "samples")}
        print(f"\n{key}: {block['count']}" + (f"  {json.dumps(extra, default=str)}" if extra else ""))
        for s in block["samples"][: args.samples]:
            print(f"    {s}")
    print(f"\nname_keys to write: {plan['name_keys_to_write']}")
    if not out["dry_run"]:
        print("\napplied:", json.dumps(out["applied"], default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
