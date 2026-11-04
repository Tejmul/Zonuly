"""Can this network verify e-mail at all? If yes, verify as much as it can, free.

Tejmul, 2026-11-05: "I have no money to spend right now" + a need for thousands of verified
addresses. His home ISP blocks outbound port 25, so check-if-email-exists can only ever answer
"unknown" (1 address proved in the project's lifetime against 18,104 unknowns).

BUT THE BLOCK IS PER-NETWORK, NOT PER-MACHINE. Mobile carriers often leave port 25 open where
home broadband does not. So: tether the Mac to the phone's hotspot and run this. It costs nothing,
it takes a minute to find out, and if the port is open every one of the 34,134 held addresses can
be proved for free, at his own pace.

    python scripts/try_verify.py              # just test the network
    python scripts/try_verify.py --run 2000   # test, then verify that many, best first

It refuses to run the verification if the port is shut, rather than spending an hour writing
"unknown" into the database.
"""

from __future__ import annotations

import argparse
import socket
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PROBE_HOSTS = ["aspmx.l.google.com", "mx1.hotmail.com", "mx.yandex.net"]


def port25_open(timeout: float = 8.0) -> tuple[bool, str]:
    """Can we open an SMTP conversation from this network? One honest yes/no."""
    for host in PROBE_HOSTS:
        try:
            s = socket.create_connection((host, 25), timeout=timeout)
            banner = s.recv(120).decode("utf-8", "replace").strip()
            s.close()
            if banner.startswith("220"):
                return True, f"{host} answered: {banner[:70]}"
        except Exception as e:                       # noqa: BLE001
            last = f"{host}: {type(e).__name__}"
            continue
    return False, last


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run", type=int, default=0,
                    help="if the port is open, verify this many addresses (best first)")
    args = ap.parse_args()

    ok, detail = port25_open()
    print(("OPEN  — this network can verify e-mail. " if ok else
           "BLOCKED — this network cannot verify e-mail. ") + detail)
    if not ok:
        print("\nTry again on the phone's hotspot: Settings > Personal Hotspot on the phone,")
        print("then connect the Mac to it and run this again. Carriers often allow port 25.")
        return 1
    if not args.run:
        print("\nPort is open. Run:  python scripts/try_verify.py --run 2000")
        return 0

    from jobhunter.contacts import reacher
    from jobhunter.db import Contact, get_session, init_db
    from scripts.verify_queue import rank
    from sqlmodel import select

    init_db()
    with get_session() as s:
        queue = [addr for addr, *_ in rank(s)][:args.run]
    print(f"\nverifying {len(queue)} addresses, best first…")
    t0, done = time.time(), {"safe": 0, "invalid": 0, "risky": 0, "unknown": 0}
    for i in range(0, len(queue), 100):
        chunk = queue[i:i + 100]
        results = reacher.check_many(chunk)
        with get_session() as s:
            for addr, row in results.items():
                done[row.verdict if row.verdict in done else "unknown"] += 1
                for c in s.exec(select(Contact).where(Contact.email_unverified == addr)).all():
                    reacher._apply(s, c, row)
            s.commit()
        print(f"  {i + len(chunk)}/{len(queue)} · {done} · {round((time.time()-t0)/60,1)} min",
              flush=True)
    print(f"\ndone: {done['safe']} addresses proved and now mailable")
    return 0


if __name__ == "__main__":
    sys.exit(main())
