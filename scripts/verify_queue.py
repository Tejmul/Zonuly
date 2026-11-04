"""The verification queue — and the way back in, for a verifier that is not Reacher.

Tejmul, 2026-11-05: "currently, I have no money to spend right now", against a need for 2,000
verified addresses. Local probing cannot help: outbound port 25 is blocked on his connection, so
check-if-email-exists returns `unknown` for everything (1 address proved in the project's lifetime
against 18,104 unknowns). The addresses are not the problem — 34,134 sit on file unproved.

So this is the free route. Every bulk verifier has a free tier that takes a CSV in the browser with
no card: upload the list, download the result, and this script reads the result back in. It is
vendor-agnostic on purpose — it looks for an address column and a verdict column whatever they are
called, because NeverBounce says "valid", Emailable says "deliverable" and ZeroBounce says "valid"
in a differently-named column.

    export   python scripts/verify_queue.py --out data/verify/queue-1.csv --limit 1000
    import   python scripts/verify_queue.py --load ~/Downloads/neverbounce-result.csv

WHAT IS AND IS NOT PROOF. A verdict of valid / deliverable / safe is written to `email_check` as
`safe`, which is the same bar Reacher has to clear, and `db._only_verified_addresses` then promotes
the address onto `contact.email` on the next write. Anything else — catch-all, accept-all, risky,
unknown, disposable — is NOT proof and is recorded as what it is. Nothing here marks an address
verified because we would like it to be; that is what bounced and got the Gmail blocked.

ORDER. Free credits are few, so they are spent on the addresses most likely to turn into a sent
mail: the company's pay is proved, the person is confirmed to still work there, they are an
engineer, and nobody has written to them. Everything else waits.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlmodel import col, select  # noqa: E402

from jobhunter.contacts import reacher  # noqa: E402
from jobhunter.db import Company, Contact, get_session, init_db  # noqa: E402

#: the verdicts a vendor uses for "this mailbox exists and accepts mail"
_GOOD = {"valid", "deliverable", "ok", "safe", "good", "verified", "true"}
#: …and the ones that are explicitly NOT proof, however hopeful they sound
_NOT_PROOF = {"catch-all", "catchall", "accept-all", "acceptall", "unknown", "risky", "do_not_mail",
              "disposable", "role", "abuse", "spamtrap", "greylisted", "timeout"}
_BAD = {"invalid", "undeliverable", "bounce", "hard_bounce", "false", "dead"}

_ADDR_COL = ("email", "e-mail", "address", "email address", "emailaddress", "to", "mail")
_VERDICT_COL = ("result", "verdict", "status", "state", "deliverability", "validation result",
                "neverbounce_result", "zb status", "sub_status", "reason")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def rank(session) -> list[tuple[str, str, str, str]]:
    """(address, person, company, why it is high value) — best first.

    The order is the whole point: a free tier gives a few hundred checks, and they should land on
    addresses that can become a sent mail today."""
    rows = session.exec(
        select(Contact, Company)
        .join(Company, Company.id == Contact.company_id)
        .where(col(Contact.email_unverified).is_not(None))
        .where(col(Contact.do_not_contact).is_not(True))
        .where(col(Contact.mailed_by_tejmul_at).is_(None))
    ).all()
    out = []
    for c, co in rows:
        addr = (c.email_unverified or "").strip().lower()
        if not addr or not _EMAIL_RE.fullmatch(addr):
            continue
        pay_ok = (co.ppo_lpa or 0) >= 20
        current = (c.affiliation or "") in ("likely-current", "verified-current")
        in_city = any(x in (co.city or "").lower() for x in ("bengal", "bangal", "hyder"))
        score = (4 if pay_ok else 0) + (3 if current else 0) + (2 if in_city else 0) \
            + (1 if (c.referral_rank or 9) <= 3 else 0)
        why = ", ".join(w for w, on in (
            (f"pays ₹{co.ppo_lpa:g}L" if co.ppo_lpa else "pay proved", pay_ok),
            ("still there", current), ("Bengaluru/Hyderabad", in_city),
            ("top referrer", (c.referral_rank or 9) <= 3)) if on) or "no strong signal"
        out.append((score, addr, c.name or "", co.name or "", why))
    out.sort(key=lambda r: -r[0])
    seen, uniq = set(), []
    for _s, addr, person, company, why in out:
        if addr in seen:
            continue
        seen.add(addr)
        uniq.append((addr, person, company, why))
    return uniq


def export(path: Path, limit: int) -> dict:
    init_db()
    with get_session() as s:
        rows = rank(s)[:limit]
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["email", "name", "company", "why"])
        w.writerows(rows)
    return {"written": len(rows), "file": str(path)}


def _col(fieldnames: list[str], wanted: tuple[str, ...]) -> str | None:
    low = {(f or "").strip().lower(): f for f in fieldnames}
    for w in wanted:
        if w in low:
            return low[w]
    for k, f in low.items():
        if any(w in k for w in wanted):
            return f
    return None


def load(path: Path) -> dict:
    """Read a verifier's result file back in. Only an explicit good verdict becomes proof."""
    init_db()
    out = {"rows": 0, "safe": 0, "invalid": 0, "not_proof": 0, "unreadable": 0}
    with open(path, encoding="utf-8", errors="replace", newline="") as fh:
        reader = csv.DictReader(fh)
        acol = _col(reader.fieldnames or [], _ADDR_COL)
        vcol = _col(reader.fieldnames or [], _VERDICT_COL)
        if not acol:
            return {"error": f"no address column in {list(reader.fieldnames or [])}"}
        for row in reader:
            out["rows"] += 1
            addr = (row.get(acol) or "").strip().lower()
            if not _EMAIL_RE.fullmatch(addr):
                out["unreadable"] += 1
                continue
            verdict = (row.get(vcol) or "").strip().lower() if vcol else ""
            if verdict in _GOOD:
                reacher.record(addr, "safe", source=f"bulk:{path.stem[:24]}")
                out["safe"] += 1
            elif verdict in _BAD:
                reacher.record(addr, "invalid", source=f"bulk:{path.stem[:24]}")
                out["invalid"] += 1
            else:
                # catch-all, risky, unknown — recorded honestly, never promoted
                reacher.record(addr, "unknown", source=f"bulk:{path.stem[:24]}")
                out["not_proof"] += 1
    # promote: a contact whose held address now has a safe check moves onto contact.email
    with get_session() as s:
        promoted = 0
        for c in s.exec(select(Contact).where(col(Contact.email_unverified).is_not(None))).all():
            a = (c.email_unverified or "").strip().lower()
            row = reacher.verdict(a)
            if row is not None and row.verdict == "safe":
                c.email, c.email_unverified = a, None
                c.email_status, c.email_verified_by = "real", row.source
                s.add(c)
                promoted += 1
        s.commit()
    out["now_mailable"] = promoted
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="data/verify/queue.csv", help="where to write the queue")
    ap.add_argument("--limit", type=int, default=1000, help="how many addresses (match the free tier)")
    ap.add_argument("--load", default="", metavar="RESULT.csv",
                    help="read a verifier's result file back in instead of exporting")
    args = ap.parse_args()
    if args.load:
        res = load(Path(args.load).expanduser())
        print(res)
        return 0 if "error" not in res else 2
    res = export(ROOT / args.out if not args.out.startswith("/") else Path(args.out), args.limit)
    print(f"{res['written']} addresses -> {res['file']}")
    print("Upload that file to any verifier's free tier, then: "
          "python scripts/verify_queue.py --load <their result.csv>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
