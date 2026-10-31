"""The referral CSV for the mail-merge sheet (2026-10-31), in the format of
ZoNuLy-referrals-500.csv: company,email,subject,message,name,role,address.

Never an address already mailed: the Email table (pipeline sends) and every CSV in
data/merge_sent/ (what Tejmul sent from his own merge sheet).

Only proved addresses: contact.email (which db._only_verified_addresses lets hold nothing
unproved) AND an email_check row that says `safe`. No guessed, no unproved, no shared inbox.
Never a person already mailed, nobody suppressed, at most PER_CO per company, fresher tech
roles only (no senior titles).

Order (Tejmul, 2026-10-31): best bets first, then Bengaluru / Hyderabad companies that can pay
24+ LPA, then the rest of Bengaluru / Hyderabad (second option), then remote. A Bengaluru /
Hyderabad company with no opening we know of is still included: its mail asks about software and
AI roles there instead of naming an opening (OPEN_ASK); a remote company needs a real opening.

Plus --tests rows (default 3) to Tejmul's own Gmail at random places: "it is working".

Usage: python scripts/export_referrals_csv.py [--target 1000] [--out path] [--tests 3]
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlmodel import col, select  # noqa: E402

from jobhunter import referrals  # noqa: E402
from jobhunter.db import Company, Contact, Email, EmailCheck, Job, get_session  # noqa: E402
from jobhunter.outreach import drafter, gmail, suppress  # noqa: E402

PER_CO = 5
TEST = {"company": "TEST", "email": "tejmulmovin9@gmail.com", "subject": "Test", "message": "it is working",
        "name": "Tejmul", "role": "", "address": "test"}
INDIA = ("bengaluru", "bangalore", "hyderabad")
TECH = re.compile(r"engineer|develop|sde|software|ai\b|ml\b|machine learning|data|backend|front|full[- ]?stack|"
                  r"platform|devops|sre|security|robotics|vision|research|scientist|intern|programmer|qa|test", re.I)
NOT_TECH = re.compile(r"recruit|talent|sales|marketing|account exec|hr\b|finance|legal|biolog|chief of staff|"
                      r"designer|consultant|customer success|operations manager", re.I)


SENIOR = re.compile(r"senior|\bsr\.?\b|staff|principal|lead|manager|director|head|architect|\bvp\b|\bii\b|\biii\b", re.I)
ROLE_TOKEN = "ROLEXX"
BOT = re.compile(r"\bbot\b|\[bot\]|\bagent\b|\bautomation\b|\bactions\b|no-?reply|\bci\b", re.I)
# 2026-10-31: a Bengaluru / Hyderabad company with no opening we know of still gets asked —
# about software and AI roles in general, never about a made-up opening
OPEN_ASK = [
    (f"and saw that you're hiring for {ROLE_TOKEN}. Could you refer me for this role?",
     "and wanted to ask about software and AI engineering roles there. Could you refer me for one?"),
    (f"and saw the opening for {ROLE_TOKEN}. Could you please refer me for it?",
     "and wanted to ask about software and AI engineering roles there. Could you please refer me for one?"),
    (f"and noticed the {ROLE_TOKEN} opening. Would you be open to referring me for it?",
     "and wanted to ask about software and AI engineering roles there. Would you be open to referring me for one?"),
]


def page_role(c: Company) -> str | None:
    """A fresher tech title from the company's own careers page (company.hiring_roles), if any."""
    try:
        titles = json.loads(c.hiring_roles or "[]")
    except ValueError:
        return None
    for title in titles:
        title = (title or "").strip()
        if title and TECH.search(title) and not NOT_TECH.search(title) and not SENIOR.search(title):
            return title
    return None


def pays_24(c: Company, job: Job | None) -> bool:
    """Can they pay a fresher 24+ LPA? A stated number says so, else deep pockets / a stated pay band."""
    stated = [x for x in (c.ppo_lpa, job and job.ppo_lpa, job and job.salary_min_lpa) if x]
    if stated:
        return max(stated) >= 24
    return (c.pay_power or 0) >= 85


def tier(c: Company, job: Job | None, bets: dict[int, int]) -> int:
    india = (c.city or "").lower() in INDIA or any(k in ((job and job.location) or "").lower() for k in INDIA)
    if c.id in bets:
        return 0
    if india and pays_24(c, job):
        return 1
    if india:
        return 2
    return 3


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, default=1000)
    ap.add_argument("--out", default=str(ROOT.parent / "ZoNuLy-referrals-verified.csv"))
    ap.add_argument("--tests", type=int, default=3, help="test rows to tejmulmovin9@gmail.com at random places")
    ap.add_argument("--test-at", default="", help="sheet rows for the test rows instead, e.g. 250,480,752,940 "
                    "(row 1 is the header); a row past the end of the file goes at the end")
    ap.add_argument("--with-unproved", action="store_true",
                    help="also leads whose address was found (commit, profile, page) but not proved, "
                         "labelled 'unverified' — never a guessed, dead, shared or nameless address")
    ap.add_argument("--all-cities", action="store_true",
                    help="ignore config focus.cities (default: only Bengaluru / Hyderabad companies while it is set)")
    args = ap.parse_args()
    from jobhunter import guardrails
    from jobhunter.db import get_setting, in_focus

    rows, per_co, why, tiers = [], Counter(), Counter(), Counter()
    with get_session() as s:
        safe = {a for a in s.exec(select(EmailCheck.address).where(EmailCheck.verdict == "safe")).all()}
        went = s.exec(select(Email.contact_id, Email.to_email).where(col(Email.status).in_(
            ["sent", "replied", "failed", "sending"]))).all()
        mailed = {cid for cid, _ in went}
        mailed_addr = {(a or "").strip().lower() for _, a in went}
        # mail sent from Tejmul's own merge sheet never reaches the Email table: every CSV he
        # sent is kept in data/merge_sent/, and its addresses are never offered again
        for sent_csv in sorted((ROOT / "data" / "merge_sent").glob("*.csv")):
            with open(sent_csv, encoding="utf-8") as fh:
                mailed_addr |= {(r.get("email") or "").strip().lower() for r in csv.DictReader(fh)}
        bets = referrals.best_bets(s)
        # the held batch (scripts/verified_sprint.py) goes first — Tejmul, 2026-10-31: "next time I
        # ask for csv I should get that emails"
        held = set((json.loads(get_setting(s, "csv:held_batch", "{}") or "{}")).get("contact_ids") or [])
        invalid = {a for a in s.exec(select(EmailCheck.address).where(EmailCheck.verdict == "invalid")).all()}
        q = col(Contact.email).is_not(None)
        if args.with_unproved:
            q = q | col(Contact.email_unverified).is_not(None)
        people = s.exec(select(Contact).where(q)).all()
        company_names = dict(s.exec(select(Company.id, Company.name)).all())
        by_co: dict[int, list[Contact]] = {}
        seen_addr: set[str] = set()
        meta: dict[int, tuple[str, bool]] = {}   # contact id -> (address to write to, proved)
        for p in people:
            addr = (p.email or p.email_unverified or "").strip().lower()
            proved = bool(p.email) and addr in safe
            if not proved and not args.with_unproved:
                why["address not proved"] += 1
                continue
            if not proved:
                # found, not proved — never one we made up, one that bounced or died, a shared
                # inbox, or an address with nobody behind it
                if (p.email_status or "") not in ("fake:not-proved", "fake:unknown", "fake:risky", ""):
                    why[f"unproved: {p.email_status}"] += 1
                    continue
                if p.confidence == "pattern-guessed" or p.source == "pattern" or "email by pattern" in (p.research_notes or ""):
                    why["unproved: guessed"] += 1
                    continue
                if addr in invalid or (p.dead_email or "").lower() == addr or p.do_not_contact:
                    why["unproved: dead / opted out"] += 1
                    continue
                chk = guardrails.email(addr)
                if not chk.ok or chk.value[1] == "generic":
                    why["unproved: shared inbox / bad address"] += 1
                    continue
            co_name = (company_names.get(p.company_id) or "").lower()
            if BOT.search(p.name or "") or BOT.search(p.handle or "") or BOT.search(addr.split("@")[0]) or \
                    (co_name and (p.name or "").lower().startswith(co_name)):
                why["a bot / the company's own account"] += 1
                continue
            if p.id in mailed or addr in mailed_addr:
                why["already mailed"] += 1
                continue
            if addr in seen_addr:
                why["same address twice"] += 1
                continue
            seen_addr.add(addr)
            meta[p.id] = (addr, proved)
            by_co.setdefault(p.company_id, []).append(p)
        plan = []
        for cid, leads in by_co.items():
            c = s.get(Company, cid)
            jobs = [j for j in s.exec(select(Job).where(Job.company_id == cid, col(Job.is_senior).is_not(True))
                                      .order_by(col(Job.match_score).desc().nulls_last())).all()
                    if TECH.search(j.title or "") and not NOT_TECH.search(j.title or "")]
            if c is None:
                continue
            if not args.all_cities and not in_focus(c):
                why["outside the focus cities"] += len(leads)
                continue
            job = jobs[0] if jobs else None
            role = job.title if job else page_role(c)
            india = (c.city or "").lower() in INDIA
            if role is None and not india:
                why["no open fresher tech role"] += len(leads)
                continue
            t = tier(c, job, bets)
            leads.sort(key=lambda p: (p.id not in held, not meta[p.id][1], (p.referral_rank or 9), p.name is None))
            has_held = any(p.id in held for p in leads)
            plan.append((not has_held, t, bets.get(cid, 10**6), role is None, -(c.pay_power or 0),
                         -((job and job.match_score) or 0), c, job, role, leads))
        plan.sort(key=lambda x: x[:6])

        for _, t, _, _, _, _, c, job, role, leads in plan:
            for p in leads:
                if len(rows) >= args.target:
                    break
                if per_co[c.name] >= PER_CO and p.id not in held:
                    break     # the held batch is his in full; the per-company cap is for everyone else
                if suppress.blocked(s, contact=p, company=c, to_email=meta[p.id][0]):
                    why["suppressed / cooling down"] += 1
                    continue
                g = drafter.gather(p.id, job.id if job else None, research=False,
                                   allow_unproved=not meta[p.id][1])
                if g.get("error"):
                    why["draft: " + g["error"][:40]] += 1
                    continue
                if job is None:   # a careers-page title, or no opening known (the open ask)
                    g["job"] = {"id": None, "title": role or ROLE_TOKEN, "url": c.careers_url, "location": c.city,
                                "remote": False, "remote_anywhere": False, "market": "india",
                                "selection_score": None, "is_senior": False}
                r = drafter.write(g, use_model=False)
                if r.get("incomplete") or r.get("blocked") or r.get("structure") != "direct_intro":
                    why["draft blocked by the checks"] += 1
                    continue
                subject, body = r["subject"], gmail.as_plain(r["body"]).strip()
                if role is None:
                    for said, ask in OPEN_ASK:
                        body = body.replace(said, ask)
                    if ROLE_TOKEN in subject:
                        first = (p.name or "").split()[0] if p.name else ""
                        subject = f"Quick question, {first}" if first else "Quick question"
                    if ROLE_TOKEN in body or ROLE_TOKEN in subject:
                        why["open ask: template not recognised"] += 1
                        continue
                rows.append({"_held": p.id in held, "company": c.name, "email": meta[p.id][0], "subject": subject,
                             "message": body, "name": p.name or "",
                             "role": role or "SDE / AI roles (no opening posted)",
                             "address": "verified" if meta[p.id][1] else "unverified"})
                per_co[c.name] += 1
                tiers[("best bets", "BLR/HYD pays 24+", "BLR/HYD other", "remote")[t]] += 1

    # a few rows to Tejmul's own Gmail, at random places, so he can see the merge is going out
    import random

    if args.test_at:
        for sheet_row in sorted(int(x) for x in args.test_at.split(",") if x.strip()):
            rows.insert(min(sheet_row - 2, len(rows)), dict(TEST))
    else:
        for _ in range(args.tests):
            rows.insert(random.randint(1, max(1, len(rows))), dict(TEST))
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["company", "email", "subject", "message", "name", "role", "address"],
                           extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"rows {len(rows)} · companies {len(per_co)} · {dict(tiers)} · "
          f"verified {sum(r['address'] == 'verified' for r in rows)} · unverified {sum(r['address'] == 'unverified' for r in rows)}")
    print("held-batch rows:", sum(1 for r in rows if r.get("_held")))
    print("left out:", dict(why))
    print("file:", args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
