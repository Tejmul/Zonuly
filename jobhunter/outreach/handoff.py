"""The numbered hand-off CSVs — CSV1, CSV2, … (Tejmul, 2026-11-01).

Leads stay in the database; nothing leaves on its own. When Tejmul asks for a batch,
`scripts/export_referrals_csv.py --batch` writes data/csv/CSV<n>.csv and records here which
leads went out in it. When he has mailed it from his own merge sheet he says
`--done CSV<n>`: the batch is stamped done and every lead in it is marked mailed by him
forever (contact.mailed_by_tejmul_at), so no later batch can offer them again. A lead in a
batch that is not done yet is held back too — it is already on his sheet.

Nothing here sends mail, and nothing here writes an address: a batch only ever sees leads
whose address check-if-email-exists already proved (contact.email, email_status "real").

Layering: db only.
"""

from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy import func
from sqlmodel import Session, col, select

from jobhunter import ROOT
from jobhunter.db import Contact, CsvBatch, CsvBatchContact, utcnow

CSV_DIR = ROOT / "data" / "csv"
_NAME = re.compile(r"(?:csv)?\s*(\d+)", re.I)


def parse_name(name: str) -> int:
    """"CSV7", "csv7" or "7" -> 7. ValueError on anything else."""
    m = _NAME.fullmatch((name or "").strip())
    if not m:
        raise ValueError(f"not a batch name: {name!r} — it looks like CSV7")
    return int(m.group(1))


def path(n: int) -> Path:
    return CSV_DIR / f"CSV{n}.csv"


def next_number(session: Session) -> int:
    """One past the highest batch recorded AND the highest CSV<n>.csv already in data/csv,
    so a file Tejmul may still be mailing from is never overwritten."""
    recorded = session.exec(select(func.max(CsvBatch.n))).one() or 0
    on_disk = 0
    for p in CSV_DIR.glob("CSV*.csv"):
        try:
            on_disk = max(on_disk, parse_name(p.stem))
        except ValueError:
            continue
    return max(recorded, on_disk) + 1


def proved_real(contact: Contact) -> bool:
    """Is this address one a batch may carry? Only an address on contact.email (which
    db._only_verified_addresses lets hold nothing unproved) whose status is "real"."""
    return bool(contact.email) and (contact.email_status or "") == "real"


def spent_contact_ids(session: Session) -> set[int]:
    """Leads a new batch must not offer: already in a batch (done or not), or mailed by him."""
    ids = set(session.exec(select(CsvBatchContact.contact_id)).all())
    ids |= set(session.exec(select(Contact.id).where(col(Contact.mailed_by_tejmul_at).is_not(None))).all())
    return ids


def record(session: Session, n: int, contact_ids: list[int]) -> CsvBatch:
    """Record the batch that was just written: the row and one link per lead."""
    ids = list(dict.fromkeys(contact_ids))
    batch = CsvBatch(n=n, count=len(ids))
    session.add(batch)
    for cid in ids:
        session.add(CsvBatchContact(batch_n=n, contact_id=cid))
    session.commit()
    return batch


def mark_done(session: Session, name: str) -> dict:
    """`--done CSV<n>`: he mailed that batch. Stamps done_at and marks every lead in it
    mailed by him, so no later batch can include them. Idempotent — running it twice marks
    nothing new and keeps the first done_at. ValueError on a batch we never wrote."""
    n = parse_name(name)
    batch = session.get(CsvBatch, n)
    if batch is None:
        known = sorted(x for x in session.exec(select(CsvBatch.n)).all())
        raise ValueError(f"no batch CSV{n} — recorded: {', '.join(f'CSV{k}' for k in known) or 'none'}")
    at = utcnow()
    batch.done_at = batch.done_at or at
    session.add(batch)
    ids = session.exec(select(CsvBatchContact.contact_id).where(CsvBatchContact.batch_n == n)).all()
    marked = 0
    for cid in ids:
        lead = session.get(Contact, cid)
        if lead is not None and lead.mailed_by_tejmul_at is None:
            lead.mailed_by_tejmul_at = at
            session.add(lead)
            marked += 1
    session.commit()
    return {"n": n, "leads": len(ids), "marked": marked, "done_at": batch.done_at}


def batch_of(session: Session, contact_ids: list[int]) -> dict[int, dict]:
    """contact id -> {"batch": n, "mailed_by_tejmul": bool} for the leads that are in a batch.
    What the dashboard's read-only column shows. The latest batch wins if a lead is in two."""
    rows = session.exec(select(CsvBatchContact.contact_id, CsvBatchContact.batch_n)
                        .where(col(CsvBatchContact.contact_id).in_(contact_ids))
                        .order_by(col(CsvBatchContact.batch_n))).all()
    out: dict[int, dict] = {}
    for cid, n in rows:
        lead = session.get(Contact, cid)
        out[cid] = {"batch": n, "mailed_by_tejmul": bool(lead is not None and lead.mailed_by_tejmul_at)}
    return out
