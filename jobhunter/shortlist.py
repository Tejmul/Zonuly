"""Complete the shortlist — one company at a time, every detail, Bengaluru first (2026-10-23).

Tejmul: "complete the Bengaluru companies, and then go to one company at a time and scrape
all the details." The free scrapers spread their effort over thousands of rows; this job
works down the decision brain's shortlists in order — Bengaluru by rank, then Hyderabad,
then Remote — and finishes each company before it moves on.

ONE COMPANY, IN THIS ORDER
    fill        fill.run: their own site (description, About, careers page, LinkedIn link),
                hiring proof and open roles, referrers from every free source, the learned
                address format                                          free
    linkedin    the homepage's LinkedIn link, when the fill found none   free
    then TWO PIPELINES IN PARALLEL (2026-10-23, Tejmul: "Apify for referrals … Agent Reach for
    the funding, the seed round and the series, as a parallel pipeline"):
      details   Agent Reach: Exa's company index (enrich.facts_from_search — description,
                founded, round, amount, date, total, valuation), then, while funding is still
                blank, a targeted funding search whose best hit is read through Jina Reader.
                A hit counts only when it names their domain, or their name and their city;
                set_funding rejects a quote that does not name them    Exa (exa_daily_cap)
      people    Apify: referrals are the Apify job's (com.zonuly.apify), which buys the
                shortlist's People tabs in this same order, filtered to the tech team; this step
                records the company's place in that queue                paid (referrals)
    rescore     the company's roles re-scored, the brain re-run
    draft       2026-10-24 ("the cold mailing part is pending"): one draft into the review queue for
                the best engineer (then founder) whose address is REAL — never a pattern guess —
                when the company has no cold mail yet; at most shortlist.drafts_per_day. Nothing
                is approved here: the Queue is where you approve, the send job paces it out.
                A template draft (the model at its daily ceiling) is marked for polishing, and
                every run re-writes up to three of them once the model is back.

WHAT "COMPLETE" MEANS — the card the user reads, part by part (PARTS). A part still missing
after the pass carries its reason: the fill's own (company.fill_state), or "not public" for
funding that a search found nothing for. The pass is recorded (ShortlistFill) either way,
and the company is not taken again for `shortlist.refresh_days`.

Layering: pipeline layer — fill, actors, confidence, decide below; tick, api and CLI above.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import timedelta

from sqlmodel import col, select

from jobhunter import CONFIG
from jobhunter.db import Company, CompanyDecision, Contact, Job, ShortlistFill, get_session, init_db, utcnow

log = logging.getLogger(__name__)

_S = CONFIG.get("shortlist") or {}
POOL_ORDER = tuple(_S.get("pool_order") or ("Bengaluru", "Hyderabad", "Remote"))
REFRESH_DAYS = int(_S.get("refresh_days", 14))
TECH_WANTED = int(_S.get("tech_referrers_wanted", 2))
BUDGET_S = int(_S.get("seconds_per_company", 540))
DRAFTS_PER_DAY = int(_S.get("drafts_per_day", 10))
TECH = ("senior_engineer", "engineer", "eng_manager")

PARTS = {
    "website": "their website",
    "description": "what they build",
    "linkedin_url": "LinkedIn page",
    "team_size": "team size",
    "founded_year": "year founded",
    "funding": "funding (stage or amount)",
    "hiring": "hiring proof on their own site",
    "careers_url": "careers page",
    "pay": "pay evidence",
    "role": "a scored role",
    "tech_referrers": f"{TECH_WANTED}+ engineers ready to ask",
    "referrer_address": "an address for one of them",
}


def _tech_ready(session, cid: int) -> tuple[int, int]:
    from jobhunter import guardrails

    people = session.exec(select(Contact).where(Contact.company_id == cid, col(Contact.role_class).in_(TECH))).all()
    ready = [p for p in people if guardrails.is_ready(p) or (p.affiliation in guardrails.READY and not p.email)]
    return len(ready), sum(1 for p in ready if p.email)


def state(session, c: Company) -> dict[str, bool]:
    """Which parts of the card are filled."""
    tech, tech_addr = _tech_ready(session, c.id)
    roles = session.exec(select(Job.id).where(Job.company_id == c.id, col(Job.selection_score).is_not(None))).first()
    paid_role = session.exec(select(Job.id).where(Job.company_id == c.id, (col(Job.salary_max_lpa).is_not(None))
                                                  | col(Job.salary_min_lpa).is_not(None) | col(Job.ppo_lpa).is_not(None))).first()
    return {
        "website": bool(c.website),
        "description": bool(c.description and len(c.description) >= 40),
        "linkedin_url": bool(c.linkedin_url),
        "team_size": bool(c.team_size),
        "founded_year": bool(c.founded_year),
        "funding": bool(c.funding_stage or c.funding_amount_usd_m or c.funding_total_usd_m),
        "hiring": c.hiring_status == "verified",
        "careers_url": bool(c.careers_url),
        "pay": bool(c.ppo_lpa or paid_role or (c.pay_power_band or "unknown") not in ("unknown", "")),
        "role": roles is not None,
        "tech_referrers": tech >= TECH_WANTED,
        "referrer_address": tech_addr >= 1,
    }


_FILL_KEYS = {"description": ("description",), "hiring": ("hiring", "careers"), "careers_url": ("careers", "hiring"),
              "funding": ("funding", "facts"), "linkedin_url": ("linkedin",), "team_size": ("linkedin", "team_size"),
              "founded_year": ("linkedin", "founded"), "tech_referrers": ("referrers", "people"),
              "referrer_address": ("referrer_email", "addresses", "referrers")}


def _why(c: Company, part: str, steps: dict, tech: int) -> str:
    """Why a part is still missing, in the words of the step that tried it."""
    try:
        fs = json.loads(c.fill_state or "{}")
    except json.JSONDecodeError:
        fs = {}
    people = steps.get("people") or {}
    if part == "funding" and c.funding_evidence:
        return f"not public: {c.funding_evidence[:160]}"
    if part == "tech_referrers":
        if people.get("queued"):
            return f"{tech} engineer(s) ready; #{people['queued']} in the Apify referral queue (3 companies every 30 min)"
        if people.get("skipped"):
            return f"{tech} engineer(s) ready; People tab not bought: {people['skipped']}"
        if "found" in people and not err:
            return f"{tech} engineer(s) ready after buying the People tab — that may be all the engineers they have"
    if part == "referrer_address":
        return ("no real address for an engineer yet; guessed company addresses are held back until two real "
                "ones confirm the format")
    for key in _FILL_KEYS.get(part, ()):
        if fs.get(key):
            return str(fs[key])[:200]
    return "not found by the free sources yet"


def queue(limit: int | None = None) -> list[dict]:
    """The shortlist in working order, without companies done inside refresh_days."""
    init_db()
    since = utcnow() - timedelta(days=REFRESH_DAYS)
    retry_before = utcnow() - timedelta(hours=12)
    with get_session() as s:
        recent: set[int] = set()
        for r in s.exec(select(ShortlistFill).where(ShortlistFill.at >= since).order_by(col(ShortlistFill.at))).all():
            # a pass stopped only by Apify's daily cap comes back after 12 h, not refresh_days
            blocked = '"retry": true' in (r.steps or "")
            if blocked and r.at < retry_before:
                recent.discard(r.company_id)
            else:
                recent.add(r.company_id)
        decs = s.exec(select(CompanyDecision).where(col(CompanyDecision.rank).is_not(None))).all()
    order = {p: i for i, p in enumerate(POOL_ORDER)}
    rows = sorted((d for d in decs if d.pool in order), key=lambda d: (order[d.pool], d.rank))
    out = [{"company_id": d.company_id, "pool": d.pool, "rank": d.rank} for d in rows if d.company_id not in recent]
    return out[:limit] if limit else out


def details(cid: int) -> dict:
    """The Agent Reach pipeline: what the company is and how it is funded, evidence-linked."""
    import re

    from jobhunter import enrich, guardrails
    from jobhunter.research import web
    from jobhunter.research.agent import extract_funding

    out: dict = {}
    try:
        r = enrich.facts_from_search(cid)
        if r.get("channel"):          # the metered index is out (cap, 429): the keyless path instead
            r = enrich.facts_from_search(cid, keyless=True)
        out["company_index"] = {k: r.get(k) for k in ("description", "website", "team_size", "founded", "hq", "funding",
                                                      "rejected", "error") if r.get(k)} or "nothing new"
    except Exception as e:  # noqa: BLE001
        out["company_index"] = {"error": f"{type(e).__name__}: {e}"[:200]}
    with get_session() as s:
        c = s.get(Company, cid)
        if c.funding_stage or c.funding_amount_usd_m or c.funding_total_usd_m:
            return out
        name, domain, city = c.name, (c.domain or "").lower(), (c.city or "").split("·")[0].strip()
    hits = web.search(f"{name} {domain} raises funding seed series round".strip(), limit=6)
    if hits.get("error"):
        out["funding_search"] = {"error": hits["error"]}
        return out
    city_rx = {"bengaluru": "bengaluru|bangalore", "hyderabad": "hyderabad"}.get(city.lower(), re.escape(city.lower()) or "$^")
    tried = []
    for h in (hits.get("results") or [])[:4]:
        url, blob = h.get("url") or "", f"{h.get('title', '')} {h.get('snippet', '')}"
        page = web.read(url, timeout=30).get("text") or blob
        low = page.lower()
        theirs = (domain and (domain in low or domain in url.lower())) or (
            name.lower() in low and re.search(city_rx, low))
        if not theirs:
            tried.append(f"{url}: does not name {domain or name + ' in ' + city}")
            continue
        # only the sentences about them: a press page lists other companies' rounds too
        about = " ".join(x for x in re.split(r"(?<=[.!?])\s+", page) if name.lower() in x.lower())[:4000]
        fs = extract_funding(about, url=url)
        if not fs:
            tried.append(f"{url}: names them, states no round")
            continue
        fund = {**fs.as_dict(), "evidence_quote": fs.evidence_quote or about[:300]}
        with get_session() as s:
            c = s.get(Company, cid)
            w = guardrails.set_funding(s, c, fund, source=url, channel="agent-reach")
            s.add(c)
            s.commit()
        out["funding_search"] = {"source": url, **w}
        if w.get("written"):
            return out
    out["funding_search"] = {"found": None, "tried": tried[:4]}
    return out


def people(cid: int) -> dict:
    """Referrals are the Apify job's (referrals.apify_step, launchd com.zonuly.apify), which
    buys the shortlist's People tabs in this same order; this says where the company stands."""
    from jobhunter.referrals import apify_queue

    with get_session() as s:
        c = s.get(Company, cid)
        tech, _ = _tech_ready(s, cid)
        has_li = bool(c.linkedin_url)
    if tech >= TECH_WANTED:
        return {"skipped": "enough engineers already"}
    if not has_li:
        return {"skipped": "no LinkedIn page yet, so Apify cannot buy its People tab"}
    q = apify_queue()
    if cid in q:
        return {"queued": q.index(cid) + 1}
    return {"skipped": "People tab already bought (Apify) — no more engineers there"}


_DRAFT_ORDER = {"senior_engineer": 0, "engineer": 1, "eng_manager": 2, "founder": 3}


def draft(cid: int, job_id: int | None) -> dict:
    """One draft for the company's best real-address referrer, into the review queue."""
    from jobhunter import guardrails
    from jobhunter.db import Email
    from jobhunter.outreach import drafter

    day0 = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    with get_session() as s:
        if s.exec(select(Email.id).where(Email.company_id == cid, Email.kind == "cold",
                                         col(Email.status).in_(["draft", "approved", "sending", "sent", "replied"]))).first():
            return {"skipped": "the company already has a cold email"}
        made_today = len(s.exec(select(Email.id).where(Email.kind == "cold", Email.created_at >= day0)).all())
        if made_today >= DRAFTS_PER_DAY:
            return {"skipped": f"today's {DRAFTS_PER_DAY} drafts are written"}
        people = [p for p in s.exec(select(Contact).where(Contact.company_id == cid, col(Contact.email).is_not(None),
                                                          col(Contact.role_class).in_(list(_DRAFT_ORDER)))).all()
                  if guardrails.is_ready(p) and p.confidence != "pattern-guessed"]
    if not people:
        return {"skipped": "no engineer or founder with a real address yet"}
    best = min(people, key=lambda p: (_DRAFT_ORDER[p.role_class], p.confidence != "verified", p.id))
    try:
        r = drafter.draft_for(best.id, job_id, mark_polish=True)
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"[:300]}
    return {"email_id": r.get("email_id"), "to": best.name, "error": r.get("error"),
            "incomplete": r.get("missing_labels") if r.get("incomplete") else None,
            "flags": [f.get("kind") for f in r.get("flags") or []]}


def one(cid: int) -> dict:
    """Fill one company completely, record it, return the record."""
    from jobhunter import confidence, decide, fill
    from jobhunter.actors import apify_source

    init_db()
    t0 = time.time()
    with get_session() as s:
        c = s.get(Company, cid)
        d = s.get(CompanyDecision, cid)
        if c is None:
            return {"error": f"no company {cid}"}
        before = state(s, c)
        name = c.name
    steps: dict = {}
    spent0 = apify_source.spent_today()

    try:
        r = fill.run(ids=[cid], people=True, budget_s=BUDGET_S)
        steps["fill"] = {k: (dict(v) if hasattr(v, "items") else v) for k, v in r.items() if k != "missing"}
    except Exception as e:  # noqa: BLE001 — a failed step is recorded, the next one still runs
        steps["fill"] = {"error": f"{type(e).__name__}: {e}"[:300]}

    with get_session() as s:
        c = s.get(Company, cid)
        no_li = not c.linkedin_url and bool(c.website)
    if no_li:
        steps["linkedin"] = fill.backfill_linkedin(ids=[cid])

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(2) as pool:
        f_details, f_people = pool.submit(details, cid), pool.submit(people, cid)
        steps["details"], steps["people"] = f_details.result(), f_people.result()

    with get_session() as s:
        job_ids = list(s.exec(select(Job.id).where(Job.company_id == cid)).all())
    if job_ids:
        confidence.recompute(job_ids=job_ids, force=True)
    decide.run()
    with get_session() as s:
        best_job = (s.get(Company, cid).best_selection_job_id if s.get(Company, cid) else None)
    steps["draft"] = draft(cid, best_job)

    with get_session() as s:
        c = s.get(Company, cid)
        after = state(s, c)
        tech_after, _ = _tech_ready(s, cid)
        missing = {p: _why(c, p, steps, tech_after) for p, ok in after.items() if not ok}
        steps["retry"] = bool((steps.get("people") or {}).get("queued"))
        rec = ShortlistFill(company_id=cid, pool=d.pool if d else "-", rank=d.rank if d else None,
                            status="complete" if not missing else "partial",
                            missing_before=json.dumps([p for p, ok in before.items() if not ok]),
                            missing_after=json.dumps(missing), steps=json.dumps(steps, default=str)[:20000],
                            cost_usd=round(apify_source.spent_today() - spent0, 4), seconds=round(time.time() - t0, 1))
        s.add(rec)
        s.commit()
        out = {"company_id": cid, "name": name, "pool": rec.pool, "rank": rec.rank, "status": rec.status,
               "filled": [p for p in after if after[p] and not before[p]], "missing": missing,
               "cost_usd": rec.cost_usd, "seconds": rec.seconds}
    log.info("shortlist: %s", out)
    return out


def catch_up(limit: int = 2) -> list[dict]:
    """Drafts for shortlisted companies already visited, whose referrers arrived after the
    visit. 2026-10-25: Apify bought 156 verified engineers for companies the shortlist had
    passed (GoComet, SatSure, AntStack…), and `one` drafts only on its own visit, so none of
    them had a draft. Pool order, then rank; draft() keeps the one-per-company and daily caps."""
    from jobhunter.db import Email

    init_db()
    with get_session() as s:
        decs = s.exec(select(CompanyDecision).where(col(CompanyDecision.rank).is_not(None))).all()
        has_mail = set(s.exec(select(Email.company_id).where(
            Email.kind == "cold", col(Email.status).in_(["draft", "approved", "sending", "sent", "replied"]))).all())
        ready = set(s.exec(select(Contact.company_id).where(
            col(Contact.email).is_not(None), col(Contact.role_class).in_(list(_DRAFT_ORDER)),
            Contact.confidence != "pattern-guessed", col(Contact.do_not_contact).is_not(True))).all())
        order = {p: i for i, p in enumerate(POOL_ORDER)}
        todo = sorted((d for d in decs if d.company_id in ready and d.company_id not in has_mail),
                      key=lambda d: (order.get(d.pool, 99), d.rank))
        jobs = {d.company_id: (s.get(Company, d.company_id).best_selection_job_id
                               if s.get(Company, d.company_id) else None) for d in todo[:limit * 3]}
    out = []
    for d in todo[:limit * 3]:
        r = draft(d.company_id, jobs.get(d.company_id))
        out.append({"company_id": d.company_id, **r})
        if "drafts are written" in str(r.get("skipped", "")) or len([x for x in out if x.get("email_id")]) >= limit:
            break
    return out


def run(limit: int = 1) -> dict:
    """The next `limit` companies on the shortlist, one after another; then up to three
    template drafts re-written by the model, which does nothing while its daily ceiling holds."""
    from jobhunter.outreach import drafter

    done = [one(q["company_id"]) for q in queue(limit)]
    try:
        caught = catch_up(limit=2)
    except Exception as e:  # noqa: BLE001
        caught = [{"error": f"{type(e).__name__}: {e}"[:200]}]
    try:
        polish = drafter.polish_pending(limit=3)
    except Exception as e:  # noqa: BLE001
        polish = {"error": f"{type(e).__name__}: {e}"[:200]}
    return {"done": done, "catch_up": caught, "polish": polish, "left": len(queue())}


def progress() -> dict:
    """Per pool: how many shortlisted companies are complete / partial / not started, the
    company being worked on next, and the latest record of each company."""
    init_db()
    with get_session() as s:
        decs = s.exec(select(CompanyDecision).where(col(CompanyDecision.rank).is_not(None))).all()
        latest: dict[int, ShortlistFill] = {}
        for r in s.exec(select(ShortlistFill).order_by(col(ShortlistFill.at))).all():
            latest[r.company_id] = r
        names = {c.id: c.name for c in s.exec(select(Company).where(col(Company.id).in_([d.company_id for d in decs] or [-1]))).all()}
    pools = {}
    for p in POOL_ORDER:
        ds = sorted((d for d in decs if d.pool == p), key=lambda d: d.rank)
        rows = []
        for d in ds:
            r = latest.get(d.company_id)
            rows.append({"company_id": d.company_id, "name": names.get(d.company_id), "rank": d.rank,
                         "status": r.status if r else "waiting", "missing": json.loads(r.missing_after or "{}") if r else None,
                         "at": r.at.isoformat() if r else None, "cost_usd": r.cost_usd if r else 0})
        pools[p] = {"total": len(rows), "complete": sum(1 for x in rows if x["status"] == "complete"),
                    "partial": sum(1 for x in rows if x["status"] == "partial"),
                    "waiting": sum(1 for x in rows if x["status"] == "waiting"), "rows": rows}
    nxt = queue(1)
    return {"pools": pools, "next": ({**nxt[0], "name": names.get(nxt[0]["company_id"])} if nxt else None),
            "parts": PARTS, "order": list(POOL_ORDER)}


_ROLE_ORDER = {"senior_engineer": 0, "engineer": 1, "eng_manager": 2, "founder": 3, "tech_recruiter": 4, "recruiter": 5}


def referrals(pool: str | None = None, *, tech_only: bool = False) -> dict:
    """Every person at every shortlisted company, Best bets order, the tech team first —
    the leads list the user works from (2026-10-24: "I need the full list of the people")."""
    from jobhunter import guardrails

    init_db()
    order = {p: i for i, p in enumerate(POOL_ORDER)}
    with get_session() as s:
        decs = sorted((d for d in s.exec(select(CompanyDecision).where(col(CompanyDecision.rank).is_not(None))).all()
                       if d.pool in order and (pool in (None, "all") or d.pool == pool)),
                      key=lambda d: (order[d.pool], d.rank))
        ids = [d.company_id for d in decs]
        comps = {c.id: c for c in s.exec(select(Company).where(col(Company.id).in_(ids or [-1]))).all()}
        people: dict[int, list[Contact]] = {}
        for p in s.exec(select(Contact).where(col(Contact.company_id).in_(ids or [-1]),
                                              col(Contact.do_not_contact).is_not(True))).all():
            people.setdefault(p.company_id, []).append(p)
    out, totals = [], {"companies": 0, "people": 0, "engineers": 0, "ready": 0, "with_address": 0, "real_address": 0}
    for d in decs:
        c = comps.get(d.company_id)
        rows = []
        for p in people.get(d.company_id, []):
            if p.role_class not in _ROLE_ORDER or (tech_only and p.role_class not in TECH):
                continue
            ready = guardrails.is_ready(p) if p.email else p.affiliation in guardrails.READY
            rows.append({"id": p.id, "name": p.name or p.handle, "role": p.role, "role_class": p.role_class,
                         "current": p.affiliation in guardrails.READY, "ready": ready,
                         "email": p.email, "address": p.confidence if p.email else None, "email_kind": p.email_kind,
                         "linkedin": p.linkedin, "github": p.github, "x": p.x_handle, "source": p.source,
                         "found": p.created_at.isoformat() if p.created_at else None})
        rows.sort(key=lambda r: (_ROLE_ORDER[r["role_class"]], not r["current"], r["email"] is None, r["name"] or ""))
        eng = [r for r in rows if r["role_class"] in TECH]
        totals["companies"] += 1
        totals["people"] += len(rows)
        totals["engineers"] += len(eng)
        totals["ready"] += sum(r["ready"] for r in rows)
        totals["with_address"] += sum(1 for r in rows if r["email"])
        totals["real_address"] += sum(1 for r in rows if r["email"] and r["address"] != "pattern-guessed")
        out.append({"company_id": d.company_id, "name": c.name if c else None, "pool": d.pool, "rank": d.rank,
                    "p_offer": d.p_offer, "linkedin_url": c.linkedin_url if c else None, "engineers": len(eng),
                    "with_address": sum(1 for r in rows if r["email"]), "people": rows})
    return {"totals": totals, "companies": out}


__all__ = ["queue", "one", "run", "progress", "state", "referrals", "PARTS"]
