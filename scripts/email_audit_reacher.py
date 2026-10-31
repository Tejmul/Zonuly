"""Run check-if-email-exists (Reacher) over the audit's addresses: verdicts go to email_check
only — no lead is changed. Referrers first. Google-hosted addresses are skipped while no
SOCKS5 proxy is configured (Google never answers this machine's IP; see contacts/reacher.py).

Usage: python scripts/email_audit_reacher.py [--limit N] [--audit data/audit/<date>]
Progress: logs/reacher-audit.log
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jobhunter.contacts import reacher  # noqa: E402
from jobhunter.db import EmailCheck, get_session, init_db  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--audit", default=None)
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--all", action="store_true", help="also Google-hosted addresses")
    ap.add_argument("--timeout", type=int, default=None)
    args = ap.parse_args()
    audit = Path(args.audit) if args.audit else sorted((ROOT / "data" / "audit").iterdir())[-1]
    log_path = ROOT / "logs" / "reacher-audit.log"
    logging.basicConfig(filename=log_path, level=logging.INFO, format="%(asctime)s %(message)s")
    init_db()
    if args.timeout:
        reacher.TIMEOUT = args.timeout

    rows = list(csv.DictReader(open(audit / "all-addresses.csv")))
    rows.sort(key=lambda r: (r["referrer"] != "yes", r["group"] == "SUSPECT"))
    with get_session() as s:
        known = {k.address: k for k in s.exec(__import__("sqlmodel").select(EmailCheck)).all()}
    todo = [r["email"] for r in rows if r["group"] != "REAL" and r["email"] not in known]
    domains = {a.split("@")[1] for a in todo}

    def worth_probing(d: str) -> bool:
        # 2026-10-31: 8 lookups at a time, not 32 (32 overloaded the resolver), and a lookup
        # that never answers means "skip for now", never "no mail server"
        if args.all or reacher.checkable_here(d):
            return True
        try:
            return not reacher.mx_hosts(d)      # no MX at all: probe() records it as dead
        except reacher.MXUnknown:
            return False

    with ThreadPoolExecutor(8) as ex:
        ok = dict(zip(domains, ex.map(worth_probing, domains)))
    skipped = sum(1 for a in todo if not ok[a.split("@")[1]])
    todo = list(dict.fromkeys(a for a in todo if ok[a.split("@")[1]]))
    if args.limit:
        todo = todo[: args.limit]
    logging.info("start: %d addresses to check, %d skipped (Google-hosted, no proxy), %d already known",
                 len(todo), skipped, len(known))
    print(f"checking {len(todo)} · skipped {skipped} Google-hosted · log {log_path}", flush=True)
    tally: Counter = Counter()

    def progress(n, addr, row):
        tally[row.verdict] += 1
        logging.info("%d/%d %s %s — %s", n, len(todo), row.verdict, addr, row.detail or "")

    reacher.check_many(todo, workers=args.workers, known=known, progress=progress)
    logging.info("done: %s", dict(tally))
    print("done:", dict(tally))
    # every newly proved address goes back on its lead; the rest stay fake (internal only)
    res = reacher.segregate()
    logging.info("segregate: %s", res)
    print("segregate:", res)
    return 0


if __name__ == "__main__":
    sys.exit(main())
