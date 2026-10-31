"""The verified sprint (2026-10-31): Apify up to the monthly stop (actors.apify.monthly_stop_usd, $15),
today, on the Bengaluru / Hyderabad companies we want most, keeping only people whose address the
vendor's SMTP check proved (valid, deliverable, not catch-all — apify_source.best_email "verified").

Tejmul, 2026-10-31: "till 15 you can scrape best companies and leads which are verified … in 2 days I
will send them mails … keep it separate", "focus on Bangalore and Hyderabad … get the best bets",
"I need a pure list of people", "store it in system … next time I ask for csv I should get that emails".

Order: the decision brain's best bets (Bengaluru, then Hyderabad), then companies that can pay a
fresher 24+ LPA, then the rest — a fresher tech opening first inside each, then pay power. Only
companies that pass our guardrails (not below the pay bar, not overrated/famous, not a staffing
agency or body shop, not rejected) and hold fewer than three verified referrers.

The people found go into the database like any People-tab buy (actors/fill.py), and the ones with a
proved address are recorded as the held batch (setting csv:held_batch) that
scripts/export_referrals_csv.py puts first in Tejmul's next CSV. Nothing here sends mail.

Usage: python scripts/verified_sprint.py [--per-company 12] [--dry-run] [--max-companies N]
Log: logs/verified-sprint.log
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlmodel import col, select  # noqa: E402

from jobhunter import CONFIG, admission, backoff, guardrails, referrals  # noqa: E402
from jobhunter.actors import apify_source  # noqa: E402
from jobhunter.db import (Company, Contact, EmailCheck, Job, get_session, get_setting, in_focus,  # noqa: E402
                          init_db, set_setting, utcnow)

BATCH_KEY = "csv:held_batch"
TECH = re.compile(r"engineer|develop|sde|software|\bai\b|\bml\b|machine learning|data|backend|front|full[- ]?stack|"
                  r"platform|devops|sre|security|robotics|vision|research|scientist|intern|programmer|qa|test", re.I)
NOT_TECH = re.compile(r"recruit|talent|sales|marketing|account exec|\bhr\b|finance|legal|biolog|chief of staff|"
                      r"designer|consultant|customer success|operations manager", re.I)
SENIOR = re.compile(r"senior|\bsr\.?\b|staff|principal|lead|manager|director|head|architect|\bvp\b|"
                    r"\b\d+\s*\+?\s*(?:years|yrs)|\bii\b|\biii\b|\bl[3-9]\b|experienced|mid[- ]level|\s[-–]\s*[3-9]\b", re.I)
# not a product startup we would ask for a referral: consultancies, staffing, an ATS host as a name
NOT_STARTUP = re.compile(r"consult|talent|staffing|recruit|manpower|outsourc|teamtailor|lever\.co|greenhouse|"
                         r"workable|ashbyhq", re.I)
SERVICES = re.compile(r"consulting firm|consultancy|professional services|it services|services company|staffing|"
                      r"recruitment|outsourcing|since 19\d\d", re.I)
BILLIONS = re.compile(r"\$\s?(\d+(?:\.\d+)?)\s?b(?:n|illion)?\b[^.]{0,40}raised|raised[^.]{0,40}\$\s?(\d+(?:\.\d+)?)\s?b", re.I)
TIERS = ["24+ best bet hiring", "24+ hiring", "24+ best bet", "<24 best bet hiring", "<24 hiring", "<24 other"]
log = logging.getLogger("verified_sprint")
for _noisy in ("httpx", "httpcore"):   # their INFO lines carry the Apify token in the URL
    logging.getLogger(_noisy).setLevel(logging.WARNING)


def fresher_tech_job(session, cid: int) -> Job | None:
    for j in session.exec(select(Job).where(Job.company_id == cid, col(Job.is_senior).is_not(True))
                          .order_by(col(Job.match_score).desc().nulls_last())).all():
        t = j.title or ""
        if TECH.search(t) and not NOT_TECH.search(t) and not SENIOR.search(t):
            return j
    return None


def pays_24(c: Company, job: Job | None) -> bool:
    stated = [x for x in (c.ppo_lpa, job and job.ppo_lpa, job and job.salary_min_lpa) if x]
    return max(stated) >= 24 if stated else (c.pay_power or 0) >= 85


def targets(session) -> list[dict]:
    """The companies worth Apify credit, best first — each with why it is there."""
    bets = referrals.best_bets(session)
    safe = set(session.exec(select(EmailCheck.address).where(EmailCheck.verdict == "safe")).all())
    out = []
    for c in session.exec(select(Company)).all():
        if not in_focus(c) or not c.linkedin_url:
            continue
        if c.status == "below_pay_bar" or (not c.list_key and (c.overrated or c.status)):
            continue      # the guardrails the drafter enforces: pay bar, famous, excluded
        if c.tier == "reject" or c.do_not_contact or (not c.list_key and admission.body_shop(c.name, industry=c.description)):
            continue
        if NOT_STARTUP.search(c.name or "") or SERVICES.search(c.description or ""):
            continue      # staffing agencies and body shops are never a target (admission.body_shop's rule)
        raised = BILLIONS.search(c.description or "")
        if raised and float(raised.group(1) or raised.group(2)) >= 1:
            continue      # raised $1B+ — overrated by admission.MAX_RAISED_USD_M, read from its own description
        refs = session.exec(select(Contact).where(Contact.company_id == c.id, col(Contact.email).is_not(None),
                                                  col(Contact.affiliation).in_(guardrails.READY))).all()
        verified_refs = sum(1 for p in refs if (p.email or "").lower() in safe)
        if verified_refs >= 3:
            continue
        job = fresher_tech_job(session, c.id)
        bet = bets.get(c.id)
        # 2026-10-31, Tejmul: "see the pay power … at least 24+; if you even doubt it can pay more
        # than 24, keep it as a second option" — pay first, then best bet, then a fresher opening
        pays = pays_24(c, job)
        tier = (0 if bet is not None and job else 1 if job else 2) if pays else \
               (3 if bet is not None and job else 4 if job else 5)
        out.append({"id": c.id, "name": c.name, "city": c.city, "tier": tier, "bet": bet, "job": job.title if job else None,
                    "pay_power": c.pay_power, "verified_refs": verified_refs,
                    "key": (tier, bet if bet is not None else 10**6, 0 if job else 1, verified_refs,
                            -(c.pay_power or 0), -(c.priority_score or 0), c.id)})
    out.sort(key=lambda t: t["key"])
    return out


def proved_people(cid: int, since: datetime) -> list[int]:
    """Contacts at this company saved since `since` whose address is proved (email set = safe)."""
    with get_session() as s:
        return [k.id for k in s.exec(select(Contact).where(Contact.company_id == cid, col(Contact.email).is_not(None),
                                                           Contact.created_at >= since)).all()]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-company", type=int, default=12, help="People-tab rows bought per company (engineering titles, own city)")
    ap.add_argument("--max-companies", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true", help="list the targets and the spend, buy nothing")
    args = ap.parse_args()
    logging.basicConfig(filename=ROOT / "logs" / "verified-sprint.log", level=logging.INFO,
                        format="%(asctime)s %(message)s")
    init_db()
    from jobhunter.actors.fill import fill_company

    conf = (CONFIG.get("actors") or {}).get("apify") or {}
    titles = list(conf.get("tech_titles") or []) or None
    per_item = apify_source.MODES["email"][1]
    est = round(args.per_company * per_item + apify_source.START_FEE, 4)
    with get_session() as s:
        queue = targets(s)
    if args.max_companies:
        queue = queue[:args.max_companies]
    apify_source.sprint(True)
    used, cap = apify_source.monthly_usage()
    stop = min(apify_source.MONTHLY_STOP_USD, (cap or apify_source.MONTHLY_STOP_USD) - 0.25)
    room = round(stop - (used or stop), 4)
    print(f"targets {len(queue)} · Apify used ${used:.2f} of ${cap:g}, stop ${stop:.2f} → room ${room:.2f} · "
          f"~${est} a company → ~{int(room // est) if est else 0} companies", flush=True)
    log.info("start: %d targets, used %.2f, stop %.2f, %.4f/company", len(queue), used or -1, stop, est)
    if args.dry_run:
        from collections import Counter
        print("  by tier:", dict(sorted(Counter(TIERS[t["tier"]] for t in queue).items())))
        for t in queue[:40]:
            print(f"  {TIERS[t['tier']]:22} {t['city']:9} {t['name'][:30]:30} job={str(t['job'])[:38]:38} "
                  f"pp={t['pay_power']} refs={t['verified_refs']}")
        return 0

    started = utcnow()
    with get_session() as s:
        held = json.loads(get_setting(s, BATCH_KEY, "{}") or "{}")
    batch = set(held.get("contact_ids") or [])
    bought = 0
    for t in queue:
        used, _ = apify_source.monthly_usage()          # fresh: sprint mode re-reads Apify every call
        if used is None or used + est > stop:
            log.info("stop: used %s + %.4f > %.2f", used, est, stop)
            break
        t0 = utcnow()
        r = fill_company(t["id"], sources=["known", "apify"], max_verify=0, apify=True, apify_max=args.per_company,
                         apify_mode="email", titles=titles)
        err = ((r.get("sources") or {}).get("apify") or {}).get("error")
        with get_session() as s:
            c = s.get(Company, t["id"])
            if c is not None and not (err and "cap" in err):
                backoff.fail(c, "apify", err or f"sprint: {r.get('found', 0)} people", retryable=bool(err))
                s.add(c)
                s.commit()
        new_ids = proved_people(t["id"], t0)
        batch |= set(new_ids)
        bought += 1
        line = (f"{t['name']} ({t['city']}, {TIERS[t['tier']]}): found {r.get('found')}, "
                f"proved {len(new_ids)}{' — ' + err if err else ''}")
        print(line, flush=True)
        log.info(line)
        with get_session() as s:     # saved after every company: a stop mid-way keeps what was bought
            set_setting(s, BATCH_KEY, json.dumps({
                "name": "verified sprint 2026-10-31 (Bengaluru + Hyderabad, best bets first)",
                "created": started.isoformat(timespec="seconds"), "contact_ids": sorted(batch)}))
            s.commit()
        if err and ("cap" in err or "credit" in err or "blind" in err):
            break
    used, _ = apify_source.monthly_usage()
    print(f"done: {bought} companies bought, {len(batch)} proved people held · Apify used ${used:.2f}", flush=True)
    log.info("done: %d companies, %d held, used %.2f", bought, len(batch), used or -1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
