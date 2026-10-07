#!/usr/bin/env python
"""Re-run the guardrails over what is already stored — companies and contacts.

    python scripts/revalidate.py                    # dry run (the default): counts and samples, nothing written
    python scripts/revalidate.py --apply            # back up, then write
    python scripts/revalidate.py --apply --github 300   # and check GitHub affiliation for 300 companies

What it does (jobhunter/guardrails.py is the rulebook; nothing here is a new rule):

  companies   description, funding stage / amount / date / investors / valuation and the
              quote they came from, hiring status, careers url — each through its
              validator. A value that fails is set to NULL on the row and kept, with the
              reason, in extraction_reject. Rows are never deleted. A hand-picked list's
              own funding (the YAML) is its own evidence and is not re-judged.
  contacts    name (a handle is moved to `handle`), address kind (company / personal /
              generic / other), affiliation (verified-current / likely-current /
              unconfirmed / generic) from the evidence already on the row, and the
              ADDRESS tier made honest: a commit address with no date on it is `scraped`,
              not `verified`, until the GitHub pass finds a commit inside 12 months.
  github      (--apply --github N) the top-priority companies first: public org members,
              commits in the last 12 months to their own non-fork repos, and profiles —
              display names for handles, the "company" field. Stops at --reserve calls left.
  completeness  recomputed for every active company.

Before --apply writes anything the live database is copied with SQLite's backup API to
data/backups/jobhunter-<date>-pre-revalidate.db (WAL-safe while the tick runs).
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import col, func, select  # noqa: E402

from jobhunter import ROOT, admission, guardrails  # noqa: E402
from jobhunter.db import DB_PATH, Company, Contact, Job, get_session, init_db, utcnow  # noqa: E402

log = logging.getLogger("revalidate")

# boards that are the company's own (its ATS or careers page), as Job.source values
OWN_BOARD_SOURCES = {"greenhouse", "lever", "ashby", "careers", "workable", "smartrecruiters", "recruitee",
                     "bamboohr", "breezy", "keka", "darwinbox", "zoho", "freshteam", "harvest"}
# a name that is no company at all is quarantined; an investor-looking or colliding name is
# only flagged (hygiene merges duplicates, and "Attack Capital" may be a startup)
_JUNK_NAME = ("a job-list phrase", "too long to be a name", "a placeholder", "a place, not")


def backup() -> Path:
    dest = ROOT / "data" / "backups" / f"jobhunter-{datetime.now():%Y-%m-%d-%H%M}-pre-revalidate.db"
    dest.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(DB_PATH, timeout=60)
    dst = sqlite3.connect(dest)
    with dst:
        src.backup(dst)
    dst.close()
    src.close()
    return dest


# ------------------------------------------------------------------ companies

def company_actions(c: Company, *, roles_on_board: list[str],
                    known: dict[str, int] | None = None) -> list[tuple[str, object, str | None, object]]:
    """[(field, new value, reject reason or None, old value)] — what the validators say."""
    acts: list[tuple[str, object, str | None, object]] = []
    name, domain = c.name, c.domain

    if c.description:
        chk = guardrails.description(c.description, name=name, domain=domain, strict=False, known=known, self_id=c.id)
        if not chk:
            acts.append(("description", None, chk.reason, c.description))
        elif chk.value != c.description:
            acts.append(("description", chk.value, None, c.description))

    listed = bool(c.list_key)
    has_funding = any([c.funding_stage, c.funding_amount_usd_m, c.funding_announced, c.funding_total_usd_m,
                       c.funding_investors not in (None, "", "[]")])
    ev_ok = True
    if has_funding and not listed:
        channel = "yc" if (c.funding_evidence or "").startswith("YC ") else None
        ev = guardrails.funding_evidence(c.funding_evidence, name=name, domain=domain, channel=channel)
        if not ev:
            ev_ok = False
            for f in ("funding_stage", "funding_amount_usd_m", "funding_announced", "funding_total_usd_m",
                      "funding_investors"):
                if getattr(c, f) not in (None, "", "[]"):
                    acts.append((f, None, ev.reason, getattr(c, f)))
            acts.append(("funding_evidence", None, None, c.funding_evidence))
    if ev_ok:
        if c.funding_stage:
            s = guardrails.funding_stage(c.funding_stage)
            if not s:
                acts.append(("funding_stage", None, s.reason, c.funding_stage))
            elif s.value != c.funding_stage:
                acts.append(("funding_stage", s.value, None, c.funding_stage))
        if c.funding_amount_usd_m and not listed:
            a = guardrails.funding_amount(c.funding_amount_usd_m, c.funding_evidence)
            if not a:
                acts.append(("funding_amount_usd_m", None, a.reason, c.funding_amount_usd_m))
        if c.funding_announced:
            d = guardrails.funding_date(c.funding_announced, founded_year=c.founded_year)
            if not d:
                acts.append(("funding_announced", None, d.reason, c.funding_announced))
        if c.funding_investors not in (None, "", "[]"):
            inv = guardrails.investors(c.funding_investors)
            if not inv:
                acts.append(("funding_investors", None, inv.reason, c.funding_investors))
            elif json.dumps(inv.value) != c.funding_investors:
                acts.append(("funding_investors", json.dumps(inv.value), None, c.funding_investors))
    if c.valuation_usd_m:
        amount = None if any(a[0] == "funding_amount_usd_m" and a[1] is None for a in acts) else c.funding_amount_usd_m
        v = guardrails.valuation(c.valuation_usd_m, amount=amount, total=c.funding_total_usd_m,
                                 stage=c.funding_stage, evidence=c.valuation_evidence, name=name, domain=domain)
        if not v:
            acts.append(("valuation_usd_m", None, v.reason, c.valuation_usd_m))
            acts.append(("valuation_evidence", None, None, c.valuation_evidence))

    if c.careers_url:
        cu = guardrails.careers_page(c.careers_url, domain=domain)
        if not cu:
            acts.append(("careers_url", None, cu.reason, c.careers_url))
    if c.hiring_status == "verified":
        try:
            roles = json.loads(c.hiring_roles or "[]") or []
        except (TypeError, ValueError):
            roles = []
        roles = roles or roles_on_board
        careers = None if any(a[0] == "careers_url" for a in acts) else c.careers_url
        h = guardrails.hiring("verified", roles=roles, careers_url=careers, checked_at=c.hiring_checked_at,
                              domain=domain)
        if not h:
            acts.append(("hiring_status", h.value, h.reason, "verified"))
    return acts


def name_check(c: Company, index: dict[str, int]) -> str | None:
    chk = guardrails.company_name(c.name, others=index, self_id=c.id)
    return None if chk else chk.reason


# ------------------------------------------------------------------ contacts

def contact_plan(p: Contact, company: Company | None) -> dict:
    return guardrails.relabel_contact(p, company)


# ------------------------------------------------------------------ the run

def counts(session) -> dict:
    def grp(column) -> dict:
        return {str(k): v for k, v in session.exec(select(column, func.count()).group_by(column)).all()}

    fill = {}
    rows = session.exec(select(Company).where(col(Company.status).is_(None))).all()
    n = len(rows) or 1
    for label, fn in (("description≥60", lambda c: c.description and len(c.description) >= 60),
                      ("stage", lambda c: c.funding_stage), ("amount", lambda c: c.funding_amount_usd_m),
                      ("date", lambda c: c.funding_announced),
                      ("investors", lambda c: c.funding_investors not in (None, "", "[]")),
                      ("valuation", lambda c: c.valuation_usd_m), ("hiring verified", lambda c: c.hiring_status == "verified"),
                      ("careers url", lambda c: c.careers_url)):
        fill[label] = round(100 * sum(1 for c in rows if fn(c)) / n, 1)
    return {"companies": len(rows), "fill_pct": fill,
            "contacts_confidence": grp(Contact.confidence), "contacts_affiliation": grp(Contact.affiliation),
            "contacts_email_kind": grp(Contact.email_kind)}


def run(apply: bool, *, github: int, reserve: int, samples: int) -> dict:
    init_db()
    out: dict = {"mode": "APPLY" if apply else "DRY RUN"}
    if apply:
        out["backup"] = str(backup())
    with get_session() as s:
        out["before"] = counts(s)

    # ---- companies
    rejects_by: Counter = Counter()
    changed_fields: Counter = Counter()
    sample_rows: dict[str, list] = {}
    name_flags: Counter = Counter()
    name_samples: list[str] = []
    with get_session() as s:
        index: dict[str, int] = {}
        for cid, cname, ckey in s.exec(select(Company.id, Company.name, Company.name_key).where(
                col(Company.status).is_(None))).all():
            index.setdefault(ckey or admission.name_key(cname), cid)
        board_titles: dict[int, list[str]] = {}
        for cid, title, source in s.exec(select(Job.company_id, Job.title, Job.source).where(
                col(Job.company_id).is_not(None))).all():
            if (source or "").lower() in OWN_BOARD_SOURCES:
                board_titles.setdefault(cid, []).append(title)
        ids = [c for c in s.exec(select(Company.id).where(col(Company.status).is_(None))).all()]
    for start in range(0, len(ids), 300):
        with get_session() as s:
            for c in s.exec(select(Company).where(col(Company.id).in_(ids[start:start + 300]))).all():
                why = name_check(c, index)
                if why:
                    name_flags[why.split(" (")[0].split(" '")[0]] += 1
                    if len(name_samples) < samples * 3:
                        name_samples.append(f"#{c.id} {c.name!r}: {why}")
                    junk = why.startswith(_JUNK_NAME) and not c.list_key
                    if junk:
                        name_flags["→ quarantined"] += 1
                    if apply:
                        guardrails.reject(s, company_id=c.id, field="company.name", value=c.name, reason=why,
                                          source="revalidate", channel="revalidate")
                        if junk:
                            # not a company at all: hidden, never deleted (hygiene's own rule)
                            c.status, c.status_reason = "quarantined", f"guardrails: {why}"
                for fld, new, reason, old in company_actions(c, roles_on_board=board_titles.get(c.id, []), known=index):
                    changed_fields[fld] += 1
                    if reason:
                        key = f"{fld}: {reason.split(' (')[0].split(': ')[0] if fld != 'description' else reason.split(' (')[0]}"
                        rejects_by[key] += 1
                        lst = sample_rows.setdefault(key, [])
                        if len(lst) < samples:
                            lst.append(f"#{c.id} {c.name}: {str(old)[:90]!r}")
                    if apply:
                        if reason:
                            guardrails.reject(s, company_id=c.id, field=fld, value=old, reason=reason,
                                              source="on record", channel=(guardrails.meta_of(c, fld) or {}).get("channel") or "revalidate")
                        setattr(c, fld, new)
                        if fld == "hiring_status":
                            c.hiring_evidence = f"[guardrails] {reason}. Was: {c.hiring_evidence or ''}"[:300]
                if apply:
                    c.validated_at = utcnow()
                    s.add(c)
            if apply:
                s.commit()
    out["companies"] = {"fields_changed": dict(changed_fields.most_common()),
                        "rejected_by_reason": dict(rejects_by.most_common()), "samples": sample_rows,
                        "name_flags": dict(name_flags.most_common()), "name_samples": name_samples}

    # ---- contacts
    c_stats: Counter = Counter()
    c_samples: dict[str, list] = {}
    with get_session() as s:
        companies = {c.id: c for c in s.exec(select(Company)).all()}
        cids = s.exec(select(Contact.id)).all()
        for c in companies.values():
            s.expunge(c)
    for start in range(0, len(cids), 1000):
        with get_session() as s:
            for p in s.exec(select(Contact).where(col(Contact.id).in_(cids[start:start + 1000]))).all():
                plan = contact_plan(p, companies.get(p.company_id))
                if "_name_reject" in plan:
                    c_stats["name rejected: " + plan["_name_reject"].split(" (")[0]] += 1
                    lst = c_samples.setdefault("name", [])
                    if len(lst) < samples:
                        lst.append(f"{p.name!r} -> handle {plan.get('handle')!r}")
                if "_email_reject" in plan:
                    c_stats["email rejected: " + plan["_email_reject"]] += 1
                if plan.get("email_kind") == "generic":
                    c_stats["generic inbox"] += 1
                if plan.get("affiliation"):
                    c_stats[f"affiliation → {plan['affiliation']}"] += 1
                if plan.get("confidence") and plan["confidence"] != p.confidence:
                    c_stats[f"address {p.confidence} → {plan['confidence']}"] += 1
                if not apply:
                    continue
                if "_name_reject" in plan:
                    guardrails.reject(s, company_id=p.company_id, contact_id=p.id, field="contact.name", value=p.name,
                                      reason=plan["_name_reject"], source=p.github or p.linkedin or "on record",
                                      channel=p.source)
                if "_email_reject" in plan:
                    guardrails.reject(s, company_id=p.company_id, contact_id=p.id, field="contact.email", value=p.email,
                                      reason=plan["_email_reject"], source="on record", channel=p.source)
                for k, v in plan.items():
                    if not k.startswith("_"):
                        setattr(p, k, v)
                p.affiliation_checked_at = p.affiliation_checked_at or utcnow()
                s.add(p)
            if apply:
                s.commit()
    out["contacts"] = {"changes": dict(c_stats.most_common()), "samples": c_samples}

    # ---- GitHub affiliation evidence, top-priority companies first
    if apply and github:
        from jobhunter.contacts import affiliation

        out["github"] = affiliation.run(github, reserve=reserve)

    if apply:
        out["completeness"] = guardrails.sweep()
    with get_session() as s:
        out["after"] = counts(s) if apply else None
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", default=True, help="report only (default)")
    mode.add_argument("--apply", action="store_true", help="back up, then write the changes")
    ap.add_argument("--github", type=int, default=0, help="companies to check on GitHub (with --apply)")
    ap.add_argument("--reserve", type=int, default=1200, help="GitHub calls to leave for the hourly people hunt")
    ap.add_argument("--samples", type=int, default=4)
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    out = run(args.apply, github=args.github, reserve=args.reserve, samples=args.samples)
    print(json.dumps(out, indent=2, default=str, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
