"""The send day's target companies, picked and readied the day before (2026-10-26).

Tejmul: "suppose tomorrow you gonna target Aurva — check the leads and other things today
itself … once it's done then only go to next company", and then "target 10 companies,
then it should not be marked as spam". So a send day is not a scatter of one ask per
company: it goes to up to `outreach.focus.companies_per_day` target companies, each
taking up to `outreach.focus.per_company` cold mails to its best engineers — 10 × 3
fills the 25-a-day cap while no company's mail server sees more than three.

    pick      the evening before (any time after the current send day), the next
              companies in the decision brain's order (Bengaluru, Hyderabad, Remote by
              rank) that have at least `min_ready` engineers with a REAL address —
              confirmed current, never a pattern guess — and nobody asked there in the
              last company_cooldown_days. Stored in the setting `focus:<day>`, so the
              plan stays put once made.
    draft     up to per_company drafts per target, engineers first, verified first, into
              the review queue. Nothing is approved here: the review gate stays.
    send      the sender lets a target take per_company asks (outreach/suppress.py);
              every other rule — the cap, the gaps, the bounce brake — is unchanged.

Referrers are bought by the Apify job in the same order (actors.apify.per_company), so the
companies after today's targets are already being filled for the days after.

Layering: pipeline layer — db, outreach and guardrails below; shortlist, tick and api above.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta

from sqlmodel import col, select

from jobhunter import CONFIG, admission
from jobhunter.db import Company, CompanyDecision, Contact, Email, Setting, get_session, init_db, utcnow
from jobhunter.outreach import suppress

log = logging.getLogger(__name__)

_F = (CONFIG.get("outreach") or {}).get("focus") or {}
COMPANIES = int(_F.get("companies_per_day", 10))
PER_COMPANY = suppress.FOCUS_PER_COMPANY
MIN_READY = int(_F.get("min_ready", 2))
TECH = {"senior_engineer": 0, "engineer": 1, "eng_manager": 2}


def target_day(today: date | None = None) -> date:
    """The first send day after today — today's sends are already settled."""
    from jobhunter.outreach import warmup

    d = (today or date.today()) + timedelta(days=1)
    while not warmup.is_send_day(d):
        d += timedelta(days=1)
    return d


def sendable(session, cid: int) -> list[Contact]:
    """Engineers at the company who can be asked now: confirmed current, a real address,
    not asked before. Verified first, then seniority."""
    from jobhunter import guardrails

    asked = set(session.exec(select(Email.contact_id).where(
        Email.company_id == cid, Email.kind == "cold", col(Email.status).in_(["draft", *suppress._LIVE]))).all())
    people = session.exec(select(Contact).where(
        Contact.company_id == cid, col(Contact.role_class).in_(list(TECH)), col(Contact.email).is_not(None))).all()
    ok = [p for p in people if p.id not in asked and p.confidence != "pattern-guessed" and guardrails.is_ready(p)]
    return sorted(ok, key=lambda p: (p.confidence != "verified", TECH[p.role_class], p.id))


def _stored(session, day: str) -> dict | None:
    row = session.get(Setting, suppress.FOCUS_KEY + day)
    return json.loads(row.value) if row else None


def _since(plan: dict) -> datetime:
    return datetime.fromisoformat(plan["picked_at"])


def pick(day: date | None = None) -> dict:
    """The day's targets: stored ones, or picked now and stored."""
    from jobhunter.outreach import ledger

    init_db()
    day = day or target_day()
    key = day.isoformat()
    with get_session() as s:
        have = _stored(s, key)
        if have:
            return have
        cap = ledger.effective_cap(key)
        earlier = suppress._focus_rows(s, "0000")
        since = utcnow() - timedelta(days=suppress.COMPANY_COOLDOWN_DAYS)
        asked = set(s.exec(select(Email.company_id).where(
            Email.kind == "cold", col(Email.status).in_(list(suppress._LIVE)), col(Email.created_at) >= since)).all())
        order = {p: i for i, p in enumerate((CONFIG.get("shortlist") or {}).get("pool_order")
                                            or ["Bengaluru", "Hyderabad", "Remote"])}
        decs = sorted((d for d in s.exec(select(CompanyDecision).where(col(CompanyDecision.rank).is_not(None))).all()
                       if d.pool in order), key=lambda d: (order[d.pool], d.rank))
        targets, slots = [], 0
        for d in decs:
            if len(targets) >= COMPANIES or slots >= cap:
                break
            if d.company_id in earlier or d.company_id in asked:
                continue
            c = s.get(Company, d.company_id)
            if c is None or c.status or c.overrated or c.do_not_contact or c.tier == "reject":
                continue
            if not c.list_key and admission.body_shop(c.name, industry=c.description):
                continue      # the drafter refuses staffing agencies and body shops
            ready = sendable(s, c.id)
            if len(ready) < MIN_READY:
                continue
            take = min(len(ready), PER_COMPANY)
            targets.append({"company_id": c.id, "name": c.name, "pool": d.pool, "rank": d.rank,
                            "ready": len(ready), "take": take})
            slots += take
        plan = {"day": key, "picked_at": utcnow().isoformat(timespec="seconds"), "cap": cap,
                "slots": slots, "targets": targets}
        s.add(Setting(key=suppress.FOCUS_KEY + key, value=json.dumps(plan)))
        s.commit()
    log.info("focus: %s — %d targets, %d slots", key, len(targets), slots)
    return plan


def prepare(day: date | None = None) -> dict:
    """Pick the day's targets and draft for them, up to per_company each. Idempotent: a
    run that finds the drafts written does nothing."""
    from jobhunter.outreach import drafter

    plan = pick(day)
    out = {"day": plan["day"], "targets": len(plan["targets"]), "slots": plan["slots"], "drafted": [], "errors": []}
    for t in plan["targets"]:
        cid = t["company_id"]
        with get_session() as s:
            have = len(s.exec(select(Email.id).where(
                Email.company_id == cid, Email.kind == "cold", col(Email.status).in_(["draft", *suppress._LIVE]),
                col(Email.created_at) >= _since(plan))).all())
            people = [p.id for p in sendable(s, cid)][:max(0, t["take"] - have)]
            c = s.get(Company, cid)
            job_id = c.best_selection_job_id if c else None
        for pid in people:
            try:
                r = drafter.draft_for(pid, job_id, mark_polish=True)
            except Exception as e:  # noqa: BLE001 — one failed draft must not stop the others
                out["errors"].append({"company": t["name"], "error": f"{type(e).__name__}: {e}"[:200]})
                continue
            if r.get("email_id") and not r.get("error"):
                out["drafted"].append({"company": t["name"], "email_id": r["email_id"]})
            else:
                out["errors"].append({"company": t["name"], "error": str(r.get("error") or r.get("missing_labels"))[:200]})
    return out


def status(day: date | str | None = None) -> dict | None:
    """The stored plan for `day` (default: the next target day), with each target's drafts."""
    init_db()
    key = day if isinstance(day, str) else (day or target_day()).isoformat()
    with get_session() as s:
        plan = _stored(s, key)
        if not plan:
            return None
        for t in plan["targets"]:
            rows = s.exec(select(Email.status).where(
                Email.company_id == t["company_id"], Email.kind == "cold",
                col(Email.created_at) >= _since(plan))).all()
            t["drafts"] = sum(1 for x in rows if x == "draft")
            t["approved"] = sum(1 for x in rows if x in ("approved", "sending"))
            t["sent"] = sum(1 for x in rows if x in ("sent", "replied"))
    return plan
