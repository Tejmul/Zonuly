"""The send day's target companies, picked and readied the day before (2026-10-26).

Tejmul: "suppose tomorrow you gonna target Aurva — check the leads and other things today
itself … once it's done then only go to next company", then "target 10 companies, then it
should not be marked as spam", then "create a small circle of 10 companies … send 25 emails
to each … once all the employees of the 10 companies are fulfilled, we can send to the next 10".

    circle    `outreach.focus.companies_per_day` companies (setting `focus:circle`). A company
              stays until `asks_per_company` of its engineers are asked or nobody with a real
              address is left there; then the next company in the decision brain's order
              (the ranked shortlist, then the other "target" companies) with at least
              `min_ready` sendable engineers and no ask in company_cooldown_days joins.
    pick      the evening before (any time after the current send day): the day's cap shared
              across the circle round-robin, at most `per_company` into one company a day —
              10 × 3 covers the 25 while no mail server sees more than three. Stored in the
              setting `focus:<day>`, so the plan stays put once made.
    draft     each target is drafted up to its share for the day (drafts and approvals still
              waiting count), engineers first, verified first, into the review queue. Nothing
              is approved here: the review gate stays.
    send      circle companies may hold up to asks_per_company open asks
              (outreach/suppress.py); the day's per-domain cap is per_company for that day's
              targets; the cap, the gaps and the bounce brake are unchanged.

Referrers are bought by the Apify job in the same order (actors.apify.per_company), so the
companies after today's targets are already being filled for the days after.

Layering: pipeline layer — db, outreach and guardrails below; shortlist, tick and api above.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta

from sqlmodel import col, or_, select

from jobhunter import CONFIG, admission
from jobhunter.db import Company, CompanyDecision, Contact, Email, Setting, get_session, init_db, utcnow
from jobhunter.outreach import sender, suppress

log = logging.getLogger(__name__)

_F = (CONFIG.get("outreach") or {}).get("focus") or {}
COMPANIES = int(_F.get("companies_per_day", 10))
PER_COMPANY = suppress.FOCUS_PER_COMPANY
MIN_READY = int(_F.get("min_ready", 2))
GOAL = suppress.FOCUS_GOAL
_WAITING = ("draft", "approved", "sending")
DUD_AFTER = 3   # rejected drafts, with none ever approved, before a company leaves the circle
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
    not asked before, not refused by the drafter. Verified first, then seniority."""
    from jobhunter import guardrails

    asked = set(session.exec(select(Email.contact_id).where(
        Email.company_id == cid, Email.kind == "cold", col(Email.status).in_(["draft", *suppress._LIVE]))).all())
    # the drafter could not write to them (no real first name, no role to ask about): not again
    asked |= set(session.exec(select(Email.contact_id).where(
        Email.company_id == cid, Email.status == "incomplete", col(Email.expires_at) > utcnow())).all())
    # a draft to them was rejected (by hand or by outreach/autoreview.py): fix the cause, not redraft
    asked |= set(session.exec(select(Email.contact_id).where(
        Email.company_id == cid, Email.kind == "cold", Email.status == "rejected")).all())
    people = session.exec(select(Contact).where(
        Contact.company_id == cid, col(Contact.role_class).in_(list(TECH)), col(Contact.email).is_not(None))).all()
    # while the bounce brake holds, the sender sends verified addresses only — so count only those
    real = ("verified",) if suppress.brake(session) else ("verified", "scraped")
    ok = [p for p in people if p.id not in asked and p.confidence in real and guardrails.is_ready(p)]
    return sorted(ok, key=lambda p: (p.confidence != "verified", TECH[p.role_class], p.id))


def _stored(session, day: str) -> dict | None:
    row = session.get(Setting, suppress.FOCUS_KEY + day)
    return json.loads(row.value) if row else None


def _since(plan: dict) -> datetime:
    return datetime.fromisoformat(plan["picked_at"])


def _asks(session, cid: int) -> list[str]:
    """Statuses of every cold ask made (or drafted) at the company."""
    return list(session.exec(select(Email.status).where(
        Email.company_id == cid, Email.kind == "cold", col(Email.status).in_(["draft", *suppress._LIVE]))).all())


def _rejected(session, cid: int, since: datetime | None = None) -> int:
    """Cold drafts at the company the review rejected (outreach/autoreview.py), since `since`."""
    q = select(Email.id).where(Email.company_id == cid, Email.kind == "cold", Email.status == "rejected",
                               col(Email.error).like("auto-review:%"))
    if since is not None:
        q = q.where(col(Email.created_at) >= since)
    return len(session.exec(q).all())


def _blocked(s, c: Company | None) -> bool:
    if c is None or c.status or c.overrated or c.do_not_contact or c.tier == "reject":
        return True
    if not c.list_key and admission.body_shop(c.name, industry=c.description):
        return True       # the drafter refuses staffing agencies and body shops
    # no fresher role there to ask about: every draft would be rejected (outreach/autoreview.py)
    return role_for(s, c) is False


def role_for(s, c: Company) -> int | None | bool:
    """The role a draft at the company asks about: its best bet, or — when that one is senior or
    wants 3+ years — its best fresher role. None when no role is on file (the drafter frames a
    fresher ask); False when every role there is senior."""
    from jobhunter.db import Job
    from jobhunter.outreach import autoreview

    best = s.get(Job, c.best_selection_job_id) if c.best_selection_job_id else None
    if best is not None and not autoreview.role_problem(best):
        return best.id
    jobs = s.exec(select(Job).where(Job.company_id == c.id, col(Job.status).not_in(["closed", "expired", "dead"]))
                  .order_by(col(Job.selection_score).desc().nulls_last(), col(Job.id).desc())).all()
    ok = [j for j in jobs if not autoreview.role_problem(j)]
    if ok:
        return ok[0].id
    return False if jobs or best is not None else None


def _candidates(s):
    """Companies in the decision brain's order. 2026-10-26, Tejmul: "the best bets are our
    priority, let's complete that first" — so the Best bets shortlists come first, city by
    city (Bengaluru, Hyderabad, Remote) by rank; only then the companies the brain also
    judged "target" but left past the shortlist cut, Bengaluru first, best odds first."""
    order = {p: i for i, p in enumerate((CONFIG.get("shortlist") or {}).get("pool_order")
                                        or ["Bengaluru", "Hyderabad", "Remote"])}
    rows = s.exec(select(CompanyDecision).where(or_(col(CompanyDecision.rank).is_not(None),
                                                    CompanyDecision.verdict == "target"))).all()
    return sorted((d for d in rows if d.pool in order),
                  key=lambda d: (d.rank is None, order[d.pool], d.rank or 0, -d.p_offer, d.company_id))


def _circle(s) -> dict:
    row = s.get(Setting, suppress.CIRCLE_KEY)
    if row:
        return json.loads(row.value)
    # first run: the circle starts as the latest day already picked
    seed = s.exec(select(Setting).where(col(Setting.key) >= suppress.FOCUS_KEY + "0",
                                        col(Setting.key) <= suppress.FOCUS_KEY + "9")
                  .order_by(col(Setting.key).desc())).first()
    members = [{k: t[k] for k in ("company_id", "name", "pool", "rank")} for t in json.loads(seed.value)["targets"]] if seed else []
    return {"companies": members, "done": []}


def pick(day: date | None = None) -> dict:
    """The day's plan: stored, or made now from the circle and stored.

    2026-10-26, Tejmul: "create a small circle of 10 companies … send 25 emails to each of
    the 10 companies … once all the employees of the 10 companies are fulfilled, we can send
    to the next 10". The circle holds `companies_per_day` companies; a company stays until
    `asks_per_company` of its engineers are asked or nobody with a real address is left, and
    the next company in the brain's order takes its place. The day's cap is shared across
    the circle round-robin, at most `per_company` into one company a day — the spread that
    keeps one mail server from seeing a burst."""
    from jobhunter.outreach import ledger

    init_db()
    day = day or target_day()
    key = day.isoformat()
    with get_session() as s:
        have = _stored(s, key)
        if have:
            return have
        cap = ledger.effective_cap(key)
        circle = _circle(s)
        members, done = [], list(circle.get("done") or [])
        ready: dict[int, list[Contact]] = {}
        for m in circle["companies"]:
            c = s.get(Company, m["company_id"])
            asks = _asks(s, m["company_id"])
            blocked = _blocked(s, c)
            ready[m["company_id"]] = [] if blocked else sendable(s, m["company_id"])
            pending = sum(1 for x in asks if x in _WAITING)
            # every draft there rejected and none ever passed: the role or the company is wrong for us
            dud = _rejected(s, m["company_id"]) >= DUD_AFTER and not any(x in suppress._LIVE for x in asks)
            finished = blocked or dud or len(asks) >= GOAL or not ready[m["company_id"]]
            if finished and not pending:
                done.append(m["company_id"])
            else:
                members.append(m)
        room: dict[int, int] = {}

        def share(cid: int) -> int:
            # the day's share: new people only — mail already waiting goes before this day —
            # never past the goal or per_company
            return max(0, min(PER_COMPANY, len(ready[cid]), GOAL - len(_asks(s, cid))))

        # a foreign time zone's morning holds only so many sends (sender.abroad_per_day):
        # what the circle can really send that day is counted per zone
        tz: dict[int, str] = {}
        abroad = sender.abroad_per_day()
        # approved mail into a foreign morning from outside the circle goes first: it is counted
        backlog: dict[str, int] = {}
        mine = {m["company_id"] for m in members}
        for e in s.exec(select(Email).where(Email.kind == "cold", col(Email.status).in_(["approved", "sending"]))).all():
            if e.company_id not in mine:
                z = sender.company_tz(s.get(Company, e.company_id))
                if z != sender._LOCAL_TZ:
                    backlog[z] = backlog.get(z, 0) + 1

        def capacity() -> int:
            by: dict[str, int] = {}
            for cid, n in room.items():
                by[tz[cid]] = by.get(tz[cid], 0) + n
            return sum(n if z == sender._LOCAL_TZ else min(n, max(0, abroad - backlog.get(z, 0)))
                       for z, n in by.items())

        for m in members:
            room[m["company_id"]] = share(m["company_id"])
            tz[m["company_id"]] = sender.company_tz(s.get(Company, m["company_id"]))
        # refill the circle to its size — and past it on a day its members cannot fill the cap
        if len(members) < COMPANIES or capacity() < cap:
            since = utcnow() - timedelta(days=suppress.COMPANY_COOLDOWN_DAYS)
            asked = set(s.exec(select(Email.company_id).where(
                Email.kind == "cold", col(Email.status).in_(list(suppress._LIVE)), col(Email.created_at) >= since)).all())
            inside = {m["company_id"] for m in members} | set(done)
            for d in _candidates(s):
                if len(members) >= COMPANIES and capacity() >= cap:
                    break
                if d.company_id in inside or d.company_id in asked:
                    continue
                c = s.get(Company, d.company_id)
                if _blocked(s, c):
                    continue
                r = sendable(s, c.id)
                if len(r) < MIN_READY:
                    continue
                ready[c.id] = r
                members.append({"company_id": c.id, "name": c.name, "pool": d.pool, "rank": d.rank,
                                "joined": key})
                room[c.id] = share(c.id)
                tz[c.id] = sender.company_tz(c)
                inside.add(c.id)
        # share the day round-robin: one more per company per pass, up to its room
        take = {m["company_id"]: 0 for m in members}
        per_tz = dict(backlog)
        slots, moved = 0, True
        while slots < cap and moved:
            moved = False
            for m in members:
                cid = m["company_id"]
                z = tz[cid]
                full = z != sender._LOCAL_TZ and per_tz.get(z, 0) >= abroad
                if slots < cap and take[cid] < room[cid] and not full:
                    take[cid] += 1
                    per_tz[z] = per_tz.get(z, 0) + 1
                    slots += 1
                    moved = True
        targets = [{**m, "ready": len(ready[m["company_id"]]), "take": take[m["company_id"]],
                    "asked": len(_asks(s, m["company_id"]))} for m in members if take[m["company_id"]]]
        plan = {"day": key, "picked_at": utcnow().isoformat(timespec="seconds"), "cap": cap,
                "slots": slots, "targets": targets}
        s.merge(Setting(key=suppress.CIRCLE_KEY, value=json.dumps({"companies": members, "done": done})))
        s.add(Setting(key=suppress.FOCUS_KEY + key, value=json.dumps(plan)))
        s.commit()
    log.info("focus: %s — circle of %d, %d slots", key, len(members), slots)
    return plan


def circle() -> dict:
    """The circle now, with each company's asks so far against the goal."""
    init_db()
    with get_session() as s:
        cur = _circle(s)
        for m in cur["companies"]:
            asks = _asks(s, m["company_id"])
            m["asked"] = len(asks)
            m["sent"] = sum(1 for x in asks if x in ("sent", "replied"))
            m["ready"] = len(sendable(s, m["company_id"]))
        cur["goal"] = GOAL
    return cur


def prepare(day: date | None = None) -> dict:
    """Pick the day's plan and draft for it: each target gets `take` drafts, counted from
    when the plan was made. Idempotent: a run that finds them written does nothing."""
    from jobhunter.outreach import drafter

    plan = pick(day)
    out = {"day": plan["day"], "targets": len(plan["targets"]), "slots": plan["slots"], "drafted": [], "errors": []}
    for t in plan["targets"]:
        cid = t["company_id"]
        with get_session() as s:
            have = len(s.exec(select(Email.id).where(
                Email.company_id == cid, Email.kind == "cold", col(Email.status).in_(["draft", *suppress._LIVE]),
                col(Email.created_at) >= _since(plan))).all())
            # a rejected draft is replaced by the next person — up to `take` times a day, so a
            # company whose drafts all fail does not use up its people in one evening
            spare = t["take"] - _rejected(s, cid, _since(plan))
            people = [p.id for p in sendable(s, cid)][:max(0, min(t["take"] - have, spare))]
            c = s.get(Company, cid)
            job_id = role_for(s, c) if c else None
            job_id = job_id or None
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
