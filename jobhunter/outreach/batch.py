"""The week's draft batch — the best targets, one ask per company, into the review queue.

Targets, in order:
    1. companies on the ★ India list (list order),
    2. then every other COMPLETE company (site + description + hiring proof + a ready
       referrer with an address) by selection score — jobhunter.confidence when it exists
       (another agent is building it; used only through a guarded call) — else priority.
Per company the best ready referrer with an address: engineers / engineering managers /
founders first (referral_rank), a verified address before a guessed one, verified-current
before likely-current. Skipped: suppressed people and companies, companies with an ask
already drafted / approved / out and unanswered (one ask per company until a reply or 30
days), overrated / merged / below-pay-bar rows.

`plan()` reads only. `build()` writes drafts through drafter.draft_for (the model, the
gates, the evidence) and adds the opt-out line. NOTHING here approves: every draft waits
for the user in the review queue.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from sqlmodel import col, select

from jobhunter import admission, guardrails
from jobhunter.db import Company, Contact, Email, Job, Reply, get_session, init_db, utcnow

log = logging.getLogger(__name__)

STAR = "india-star"
_ROLE_ORDER = {"engineer": 0, "senior_engineer": 0, "eng_manager": 1, "founder": 2, "tech_recruiter": 4,
               "recruiter": 5, "other": 6}


def _selection_score(session, company: Company) -> float | None:
    """jobhunter.confidence's score for a company, if that module exists yet. Every
    failure (not built, another signature, an error inside) falls back to None."""
    try:
        from jobhunter import confidence  # type: ignore[attr-defined]
    except ImportError:
        return None
    # confidence.rollup writes the best role's selection score onto the company
    best = getattr(company, "best_selection_score", None)
    if best is not None:
        try:
            return float(best)
        except (TypeError, ValueError):
            pass
    for name in ("selection_score", "company_selection_score", "score_company", "company_score"):
        fn = getattr(confidence, name, None)
        if not callable(fn):
            continue
        for args in ((session, company), (company,), (company.id,)):
            try:
                v = fn(*args)
            except TypeError:
                continue
            except Exception as e:  # noqa: BLE001 — their module, their bug: fall back
                log.debug("confidence.%s failed: %s", name, e)
                return None
            if isinstance(v, dict):
                v = v.get("score")
            try:
                return float(v) if v is not None else None
            except (TypeError, ValueError):
                return None
    return None


def _busy_companies(session, candidate_id: int) -> dict[int, str]:
    """company_id -> why no new ask goes there now."""
    out: dict[int, str] = {}
    since = utcnow() - timedelta(days=30)
    for e in session.exec(select(Email).where(Email.kind == "cold", Email.candidate_id == candidate_id,
                                              col(Email.status).in_(["draft", "approved", "sending", "sent", "replied"]))).all():
        if e.status in ("draft", "approved", "sending"):
            if e.status != "draft" or not (e.expires_at and e.expires_at < utcnow()):
                out[e.company_id] = f"an ask is already {e.status} (email {e.id})"
            continue
        when = e.sent_at or e.created_at
        if when and when >= since and not session.exec(select(Reply.id).where(Reply.email_id == e.id)).first():
            out.setdefault(e.company_id, f"asked on {when:%d %b}, no reply yet")
    return out


def _referrer_key(p: Contact) -> tuple:
    # a guessed company address last of all (2026-10-23): real addresses deliver, guessed ones bounce
    return (p.confidence == "pattern-guessed", p.referral_rank or 9, _ROLE_ORDER.get(p.role_class or "other", 6), p.confidence != "verified",
            p.affiliation != "verified-current", p.name is None, p.id)


def plan(limit: int = 50, *, candidate_id: int = 1, guessed_max: int | None = None) -> dict:
    """Who the next drafts would go to, best first — reads only. `guessed_max` caps how
    many non-verified addresses are picked (the guessed cap × the sending days the batch
    covers): past it, a company is taken only with a verified address, so the 5-a-day
    guessed cap never leaves a day short."""
    from jobhunter.outreach import suppress

    init_db()
    with get_session() as session:
        busy = _busy_companies(session, candidate_id)
        rows = session.exec(select(Company).where(col(Company.status).is_(None), Company.complete == True)).all()  # noqa: E712
        star = session.exec(select(Company).where(Company.list_key == STAR, col(Company.status).is_(None))).all()
        pool = {c.id: c for c in rows}
        for c in star:
            pool.setdefault(c.id, c)
        skipped: dict[str, int] = {}
        cands = []
        for c in pool.values():
            if c.do_not_contact:
                skipped["company suppressed"] = skipped.get("company suppressed", 0) + 1
                continue
            if not c.list_key and c.overrated:
                skipped["overrated"] = skipped.get("overrated", 0) + 1
                continue
            if not c.list_key and admission.body_shop(c.name, industry=c.description):
                skipped["staffing / body shop"] = skipped.get("staffing / body shop", 0) + 1
                continue
            if c.id in busy:
                skipped["ask already out"] = skipped.get("ask already out", 0) + 1
                continue
            people = [p for p in session.exec(select(Contact).where(Contact.company_id == c.id,
                                                                    col(Contact.email).is_not(None))).all()
                      if guardrails.is_ready(p) and not suppress.blocked(session, contact=p, company=c)]
            # anyone already written to (or rejected) is not asked again; an "incomplete" row
            # (the contract was missing a slot) does not count — the evidence may have grown
            people = [p for p in people if not session.exec(select(Email.id).where(
                Email.contact_id == p.id, Email.kind == "cold", Email.status != "incomplete")).first()]
            if not people:
                skipped["no ready referrer with an address"] = skipped.get("no ready referrer with an address", 0) + 1
                continue
            people.sort(key=_referrer_key)
            best = people[0]
            sel = _selection_score(session, c)
            from jobhunter.outreach.drafter import pick_job

            job = pick_job(session, c.id)
            if job is None:
                # the contract needs a specific role to ask about (an engineering title on file)
                skipped["no engineering role on file"] = skipped.get("no engineering role on file", 0) + 1
                continue
            cands.append({
                "company_id": c.id, "company": c.name, "list": c.list_key, "list_rank": c.list_rank,
                "selection_score": sel, "priority": c.priority_score or 0,
                "contact_id": best.id, "person": best.name, "role": best.role_class, "rank": best.referral_rank,
                "address": best.email, "address_confidence": best.confidence, "affiliation": best.affiliation,
                "evidence": (best.affiliation_evidence or "")[:140], "job_id": job.id if job else None,
                "job": job.title if job else None, "alternates": len(people) - 1,
                "alternate_ids": [p.id for p in people[1:4]],
                "market": job.selection_market if job else None,
            })
    cands.sort(key=lambda r: (r["list"] != STAR, r["list_rank"] or 9999,
                              -(r["selection_score"] if r["selection_score"] is not None else -1),
                              -r["priority"], r["address_confidence"] != "verified", r["company_id"]))
    if guessed_max is not None:
        picked, g = [], 0
        for r in cands:
            if r["address_confidence"] != "verified":
                if g >= guessed_max:
                    continue
                g += 1
            picked.append(r)
        cands = picked
    return {"targets": cands[:limit], "available": len(cands), "skipped": skipped,
            "ranked_by": "★ India list, then " + ("jobhunter.confidence selection score" if any(
                r["selection_score"] is not None for r in cands) else "priority score (jobhunter.confidence not available)")}


def week(start: date | str | None = None) -> dict:
    """The warm-up week from `start` (default: the warm-up start) and what the queue holds."""
    from jobhunter.outreach import warmup

    init_db()
    days = [d for d in warmup.schedule(7, from_day=start) if d["send_day"]]
    with get_session() as session:
        approved = session.exec(select(Email).where(Email.status == "approved")).all()
        drafts = session.exec(select(Email).where(Email.status == "draft")).all()
    now = utcnow()
    live_drafts = [e for e in drafts if not (e.expires_at and e.expires_at < now)]
    need = sum(d["quota"] for d in days)
    first = days[0] if days else None
    return {"days": days, "week_total": need, "approved": len(approved), "in_review": len(live_drafts),
            "expired_drafts": len(drafts) - len(live_drafts),
            "first_day": first["day"] if first else None, "first_day_quota": first["quota"] if first else 0,
            "first_day_expected": min(first["quota"], len(approved)) if first else 0,
            "note": "drafts expire after outreach.draft_expiry_days — approve within the week they were written"}


def build(n: int | None = None, *, candidate_id: int = 1, research: bool = True, dry_run: bool = False,
          use_model: bool = True) -> dict:
    """Draft the next `n` asks (default: the warm-up week's total minus what is already
    approved or in review), one per company, into the review queue. Never approves."""
    from jobhunter.outreach import drafter, suppress

    wk = week()
    if n is None:
        n = max(0, wk["week_total"] - wk["approved"] - wk["in_review"])
    from jobhunter.outreach import ledger

    send_days = max(1, sum(1 for d in wk["days"] if d["send_day"]))
    p = plan(limit=max(n * 2, n + 10), candidate_id=candidate_id, guessed_max=ledger.GUESSED_CAP * send_days)
    out = {"wanted": n, "available": p["available"], "drafted": 0, "failed": 0, "results": [], "dry_run": dry_run,
           "ranked_by": p["ranked_by"], "week": wk}
    if dry_run or n <= 0:
        out["would_draft"] = p["targets"][:n]
        return out
    model_wanted = use_model
    for t in p["targets"]:
        if out["drafted"] >= n:
            break
        # the best referrer first; when the personalisation contract cannot be filled for
        # them (no real name, no hook, no covered need), the next one at the same company
        r: dict = {}
        tried = []
        for cid in [t["contact_id"], *t.get("alternate_ids", [])]:
            try:
                r = drafter.draft_for(cid, t["job_id"], research=research, candidate_id=candidate_id,
                                      use_model=use_model, record_incomplete=False,
                                      mark_polish=model_wanted and not use_model)
            except Exception as e:  # noqa: BLE001 — one target failing must not end the batch
                r = {"error": f"{type(e).__name__}: {str(e)[:160]}"}
            if r.get("budget"):
                use_model = False       # the ceiling: the rest are written by the template, marked for polish
            if not r.get("incomplete"):
                break
            tried.append((cid, r.get("missing_labels") or []))
        if r.get("incomplete") and tried:
            r = drafter.draft_for(tried[0][0], t["job_id"], research=False, candidate_id=candidate_id,
                                  use_model=False, record_incomplete=True)
            r["tried"] = [{"contact_id": c, "missing": m} for c, m in tried]
        row = {"company": t["company"], "person": t["person"], "address_confidence": t["address_confidence"], **r}
        out["results"].append(row)
        if r.get("email_id") and not r.get("incomplete"):
            with get_session() as session:
                e = session.get(Email, r["email_id"])
                if e is not None:
                    e.body = suppress.with_opt_out(e.body)
                    session.add(e)
                    session.commit()
            out["drafted"] += 1
        else:
            out["failed"] += 1
    log.info("batch: drafted %s of %s", out["drafted"], n)
    return out


__all__ = ["plan", "build", "week"]
