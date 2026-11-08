"""Fold duplicate contacts together — one row per person per company.

2026-11-08: OpenHands showed 83 leads for a company of 27. The same person was scraped by the
GitHub pass, the site pass and the Apify pass, and each wrote a new row: Engel Nyst three times,
Graham Neubig three times. Across the database that is 1,479 phantom leads, which inflates every
count on the dashboard and, worse, offers the same person in a CSV more than once.

IDENTITY, in the order it is trusted:
    1. the GitHub handle    — a login is one person, and the scrapers all record it
    2. the address          — `email` or `email_unverified`, lowercased
    3. the name             — only within one company, and only when it is a real name

WHAT SURVIVES A MERGE, because losing it would be a real harm:
    * `mailed_by_tejmul_at` — the lock that keeps a person out of every future CSV. If ANY row in
      the group carries it, the survivor carries it. Losing it would mail someone twice.
    * `do_not_contact` and its reason — likewise, a refusal must outlive the row that recorded it.
    * a proved address (`email_status == "real"`) always beats an unproved one.
    * anything the survivor is missing is taken from a loser: github, linkedin, role, notes.

Rows in other tables (email, event, csv_batch_contact …) are repointed at the survivor before the
losers are deleted, so no sent mail loses its person.

    python scripts/dedupe_contacts.py --dry-run           # what it would do, company by company
    python scripts/dedupe_contacts.py --company 2409      # one company
    python scripts/dedupe_contacts.py                     # all of them
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import text  # noqa: E402
from sqlmodel import col, select  # noqa: E402

from jobhunter.db import Contact, get_session, init_db  # noqa: E402

#: every table that points at a contact, and the column that does it
REFERRERS = [("email", "contact_id"), ("event", "contact_id"), ("extraction_reject", "contact_id"),
             ("extraction_write", "contact_id"), ("email_proposal", "contact_id"),
             ("csv_batch_contact", "contact_id")]

#: fields a survivor inherits from a loser when the survivor has nothing there
INHERIT = ["github", "linkedin", "x_handle", "handle", "role", "role_evidence", "email_unverified",
           "research_notes", "employer_claim", "affiliation", "affiliation_evidence",
           "affiliation_source", "name", "source", "confidence", "email_kind", "name_source",
           "last_commit_at", "seniority", "role_class"]


def handle_of(c: Contact) -> str | None:
    g = (c.github or "").rstrip("/").strip()
    return g.rsplit("/", 1)[-1].lower() or None if g else None


def key_of(c: Contact) -> str | None:
    """What makes this row a person. None means 'cannot tell' — such a row is never merged."""
    h = handle_of(c)
    if h:
        return f"gh:{h}"
    addr = (c.email or c.email_unverified or "").strip().lower()
    if addr:
        return f"em:{addr}"
    name = (c.name or "").strip().lower()
    # a generic mailbox is not a person, and two blank names are not the same person
    if name and name not in ("general inbox", "inbox", "team", "careers", "support"):
        return f"nm:{name}"
    return None


def score(c: Contact) -> tuple:
    """Who should survive. Higher wins; the id breaks ties so a rerun is stable."""
    return (
        1 if c.mailed_by_tejmul_at else 0,          # never drop the row that carries the lock
        1 if (c.email_status or "") == "real" else 0,
        1 if c.email else 0,
        1 if (c.role or "").strip() else 0,
        1 if (c.affiliation or "") in ("verified-current", "likely-current") else 0,
        1 if (c.name or "").strip() else 0,
        -(c.id or 0),                               # otherwise the first one we ever saw
    )


def merge_group(session, rows: list[Contact], *, dry: bool) -> dict:
    rows = sorted(rows, key=score, reverse=True)
    keep, losers = rows[0], rows[1:]
    moved: list[str] = []
    for loser in losers:
        # the two facts that must never be lost
        if loser.mailed_by_tejmul_at and not keep.mailed_by_tejmul_at:
            keep.mailed_by_tejmul_at = loser.mailed_by_tejmul_at
            moved.append("mailed_by_tejmul_at")
        if loser.do_not_contact and not keep.do_not_contact:
            keep.do_not_contact = True
            keep.dnc_reason = loser.dnc_reason or keep.dnc_reason
            moved.append("do_not_contact")
        # a proved address beats whatever the survivor had
        if (loser.email_status or "") == "real" and (keep.email_status or "") != "real":
            keep.email, keep.email_status = loser.email, loser.email_status
            keep.email_verified_by = loser.email_verified_by
            moved.append("proved address")
        for f in INHERIT:
            if not getattr(keep, f, None) and getattr(loser, f, None):
                setattr(keep, f, getattr(loser, f))
                moved.append(f)
        if (loser.referral_rank or 9) < (keep.referral_rank or 9):
            keep.referral_rank = loser.referral_rank
    if dry:
        return {"kept": keep.id, "dropped": [x.id for x in losers], "moved": sorted(set(moved))}
    session.add(keep)
    session.flush()
    loser_ids = [x.id for x in losers]
    for table, column in REFERRERS:
        session.exec(text(f"UPDATE {table} SET {column} = :keep WHERE {column} IN "
                          f"({','.join(str(i) for i in loser_ids)})").bindparams(keep=keep.id))
    for loser in losers:
        session.delete(loser)
    return {"kept": keep.id, "dropped": loser_ids, "moved": sorted(set(moved))}


def run(*, company_id: int | None = None, dry: bool = False) -> dict:
    init_db()
    out = {"groups": 0, "dropped": 0, "locks_preserved": 0, "companies": 0}
    with get_session() as s:
        q = select(Contact)
        if company_id:
            q = q.where(Contact.company_id == company_id)
        rows = s.exec(q).all()
        by_company: dict[int, list[Contact]] = defaultdict(list)
        for c in rows:
            if c.company_id:
                by_company[c.company_id].append(c)
        for cid, members in by_company.items():
            groups: dict[str, list[Contact]] = defaultdict(list)
            for c in members:
                k = key_of(c)
                if k:
                    groups[k].append(c)
            touched = False
            for _k, g in groups.items():
                if len(g) < 2:
                    continue
                res = merge_group(s, g, dry=dry)
                out["groups"] += 1
                out["dropped"] += len(res["dropped"])
                if "mailed_by_tejmul_at" in res["moved"]:
                    out["locks_preserved"] += 1
                touched = True
            if touched:
                out["companies"] += 1
        if not dry:
            s.commit()
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--company", type=int, default=None, help="one company id, else all")
    ap.add_argument("--dry-run", action="store_true", help="report without writing")
    a = ap.parse_args()
    res = run(company_id=a.company, dry=a.dry_run)
    verb = "would fold" if a.dry_run else "folded"
    print(f"{verb} {res['dropped']} duplicate row(s) into {res['groups']} person(s) "
          f"across {res['companies']} compan(ies); "
          f"{res['locks_preserved']} mailed-lock(s) carried over")
    return 0


if __name__ == "__main__":
    sys.exit(main())
