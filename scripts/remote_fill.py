"""Make the remote-from-India targets buyable (2026-11-13).

Apify's People tab needs a company's LinkedIn page, and the LinkedIn page comes from the company's own
homepage. Many remote targets arrive from a job board with a name only (Himalayas' company pages are
behind a bot check), so: find the website (enrich.find_website — keyless search, then the site itself
must confirm), then read the homepage for the LinkedIn page (fill.backfill_linkedin). Free; repeatable.

Usage: python scripts/remote_fill.py [--limit N]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jobhunter import backoff, enrich, remote  # noqa: E402
from jobhunter.db import Company, get_session, init_db  # noqa: E402
from jobhunter.fill import backfill_linkedin  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    init_db()
    with get_session() as s:
        ts = [t for t in remote.targets(s) if not t.linkedin]
        no_site = [t.company_id for t in ts if not (s.get(Company, t.company_id).website)
                   and backoff.due(s.get(Company, t.company_id), "website")]
    if args.limit:
        no_site = no_site[:args.limit]
    out = {"targets_without_linkedin": len(ts), "websites_tried": len(no_site), "websites_found": 0}
    for i, cid in enumerate(no_site, 1):
        try:
            r = enrich.find_website(cid)
        except Exception as e:  # noqa: BLE001 — one company must not end the pass
            r = {"error": f"{type(e).__name__}: {e}"}
        if r.get("website"):
            out["websites_found"] += 1
        print(f"[{i}/{len(no_site)}] {r.get('company', cid)}: {r.get('website') or r.get('error', 'not found')}", flush=True)
    with get_session() as s:
        ids = [t.company_id for t in remote.targets(s) if not t.linkedin and s.get(Company, t.company_id).website]
    out["linkedin"] = backfill_linkedin(ids=ids) if ids else {}
    with get_session() as s:
        ts = remote.targets(s)
    out["after"] = {"targets": len(ts), "with_linkedin": sum(t.linkedin for t in ts)}
    print(json.dumps(out, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
