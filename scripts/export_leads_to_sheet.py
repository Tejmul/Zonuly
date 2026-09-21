#!/usr/bin/env python
"""Push every lead (a contact with an email) to a Google Sheet.

    python scripts/export_leads_to_sheet.py                    # the DB config.yaml points at
    python scripts/export_leads_to_sheet.py --db demo.db       # the committed snapshot
    python scripts/export_leads_to_sheet.py --reachable        # only network._reachable leads

Auth is a service account (secrets/google_service_account.json, or GOOGLE_SERVICE_ACCOUNT_FILE);
share the sheet with its client_email as Editor. The sheet id comes from ZONULY_LEADS_SHEET_ID
or --sheet. The tab is cleared and rewritten each run, so re-running refreshes it.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import typer

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobhunter import ROOT  # noqa: E402

DEFAULT_SHEET_ID = "16Z0fVM9L2WC0U1CBs-UxHDgaph_NRcKHcjdWsDTCPB0"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

HEADER = ["Company", "Domain", "Name", "Role", "Email", "Confidence", "Source", "Role Class",
          "Seniority", "Referral Rank", "GitHub", "LinkedIn", "X", "Employer Claim", "Created"]


def _rows(db: Path, reachable: bool) -> list[list]:
    con = sqlite3.connect(db)
    cols = {r[1] for r in con.execute("pragma table_info(contact)")}
    x = "ct.x_handle" if "x_handle" in cols else "NULL"  # older snapshots predate the column
    rows = con.execute(f"""
        select co.id, co.name, co.domain, ct.id, ct.name, ct.role, ct.email, ct.confidence, ct.source,
               ct.role_class, ct.seniority, ct.referral_rank, ct.github, ct.linkedin, {x},
               ct.employer_claim, ct.created_at
        from contact ct join company co on co.id = ct.company_id
        where ct.email is not null and ct.email != ''
        order by co.name, ct.referral_rank, ct.name
    """).fetchall()
    if reachable:
        # same gate the drafter uses: an address, a referring role, a domain that fits
        from sqlmodel import Session, select
        from jobhunter import network
        from jobhunter.db import Company, Contact, engine
        with Session(engine) as s:
            companies = {c.id: c for c in s.exec(select(Company)).all()}
            people: dict[int, list[Contact]] = {}
            for p in s.exec(select(Contact)).all():
                people.setdefault(p.company_id, []).append(p)
        keep = {p.id for cid, c in companies.items() for p in network._reachable(c, people.get(cid, []))}
        rows = [r for r in rows if r[3] in keep]
    return [["" if v is None else v for v in r[1:3] + r[4:]] for r in rows]


def main(
    sheet: str = typer.Option(os.environ.get("ZONULY_LEADS_SHEET_ID", DEFAULT_SHEET_ID), help="spreadsheet id"),
    db: Path = typer.Option(None, help="sqlite file; default is the configured DB"),
    tab: str = typer.Option("Leads", help="worksheet name"),
    reachable: bool = typer.Option(False, help="only leads that pass network._reachable"),
    key: Path = typer.Option(None, help="service account JSON"),
):
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    if db is None:
        from jobhunter.db import DB_PATH
        db = DB_PATH
    elif not db.is_absolute():
        db = ROOT / db
    if reachable and db.resolve() != Path(__import__("jobhunter.db", fromlist=["DB_PATH"]).DB_PATH).resolve():
        raise typer.BadParameter("--reachable reads the configured DB; drop --db or set ZONULY_DB_PATH")
    key = key or Path(os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE") or ROOT / "secrets/google_service_account.json")
    if not key.exists():
        raise typer.BadParameter(f"service account key not found at {key}")

    rows = _rows(db, reachable)
    typer.echo(f"{len(rows)} leads from {db}")

    creds = service_account.Credentials.from_service_account_file(str(key), scopes=SCOPES)
    svc = build("sheets", "v4", credentials=creds, cache_discovery=False).spreadsheets()

    meta = svc.get(spreadsheetId=sheet, fields="sheets.properties").execute()
    if not any(s["properties"]["title"] == tab for s in meta["sheets"]):
        svc.batchUpdate(spreadsheetId=sheet, body={"requests": [{"addSheet": {"properties": {"title": tab}}}]}).execute()
    svc.values().clear(spreadsheetId=sheet, range=tab).execute()

    # the API caps a request at ~10 MB; 5k rows of this width is comfortably under.
    # append grows the grid as it goes, where update would stop at the tab's 1000/5000 rows
    values = [HEADER] + rows
    step = 5000
    for i in range(0, len(values), step):
        svc.values().append(
            spreadsheetId=sheet, range=f"{tab}!A1", valueInputOption="RAW",
            insertDataOption="INSERT_ROWS", body={"values": values[i:i + step]},
        ).execute()
        typer.echo(f"  wrote rows {i + 1}-{min(i + step, len(values))}")

    sheet_id = next(s["properties"]["sheetId"] for s in
                    svc.get(spreadsheetId=sheet, fields="sheets.properties").execute()["sheets"]
                    if s["properties"]["title"] == tab)
    svc.batchUpdate(spreadsheetId=sheet, body={"requests": [
        {"updateSheetProperties": {"properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": 1}},
                                   "fields": "gridProperties.frozenRowCount"}},
        {"repeatCell": {"range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1},
                        "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                        "fields": "userEnteredFormat.textFormat.bold"}},
    ]}).execute()
    typer.echo(f"done: https://docs.google.com/spreadsheets/d/{sheet}/edit")


if __name__ == "__main__":
    typer.run(main)
