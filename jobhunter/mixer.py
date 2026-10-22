"""The mixer — decide first, then scrape (2026-10-23).

Tejmul: "make a mixer pipeline where it collaborates with Apify and finds the best company …
in terms of pay, work culture, and everything … First, you decide, then you scrape", then
"divide it by layers: pay power, then concise it with location; downgrade one by one", "another layer is like no dsa", and for remote (US / UK / anywhere) "even if
there are a little bit of DSA rounds … the only cap is that the pay has to be good".

FREE LAYERS — nothing is bought until a company has passed all of them
    1 pay power   pay_power_band in mixer.pay_bands, or a stated pay / PPO at the India floor
    2 location    a decide pool: Bengaluru, Hyderabad, or Remote (a role open to someone in India)
    3 no DSA      DSA intensity under mixer.max_dsa — India only; Remote tolerates DSA
    4 the brain   verdict target or fill_first, best P(offer) first, not bought in refresh_days

PAID LAYERS — cheapest first, each next one only while the company still looks worth it
    linkedin  every LinkedIn ad from the company, in its city (Remote: ads open to India).
              No ad of theirs → the company stops here: nothing more is bought.
    naukri    India only: their Naukri ads, which state pay more often and carry the company's
              AmbitionBox rating — the culture signal the brain reads (decide: a low rating on
              enough reviews is a skip in India; in Remote, remote work is the culture bar).

Every ad is matched to the company by its name key before it is kept — a Naukri keyword search
returns other employers too — and goes through the same relevance gate and store as any scraper
(pipeline.persist), then the role scores and the brain run again, and the run reports each
company's P(offer) before and after. Spend: apify_source.run_actor — the shared daily cap, the
mixer's own share of it (mixer.daily_usd), the 24 h cache, one ledger — and a referral reserve
(mixer.referral_reserve_usd) it never eats into: Apify is for referrals first. Nothing scheduled
calls it; it spends only when asked.

Layering: pipeline layer — actors (Apify), pipeline.persist, confidence, decide below; api / CLI above.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone

from sqlmodel import col, select

from jobhunter import CONFIG, admission, decide
from jobhunter.db import Company, CompanyDecision, Job, MixerScrape, get_session, init_db, utcnow

log = logging.getLogger(__name__)

_M = CONFIG.get("mixer") or {}
DAILY_USD = float(_M.get("daily_usd", 0.05))
RESERVE_USD = float(_M.get("referral_reserve_usd", 0.30))
PER_RUN = int(_M.get("per_run", 2))
REFRESH_DAYS = int(_M.get("refresh_days", 14))
PAY_BANDS = set(_M.get("pay_bands") or ["pays", "deep_pockets", "funded"])
MAX_DSA = float(_M.get("max_dsa", 0.5))
LINKEDIN = {"actor": "valig~linkedin-jobs-scraper", "per_item": 0.0004, "start_fee": 0.001, "limit": 15, **(_M.get("linkedin") or {})}
NAUKRI = {"actor": "valig~naukri-jobs-scraper", "per_item": 0.0004, "start_fee": 0.001, "limit": 15, **(_M.get("naukri") or {})}
PAY_FLOOR_LPA = float(((CONFIG.get("targeting") or {}).get("india") or {}).get("min_lpa", 20))
LABEL = "mixer"


# ------------------------------------------------------------------ the free layers

def _pay_ok(c: Company, d: CompanyDecision) -> tuple[bool, str]:
    if c.pay_power_band in PAY_BANDS:
        return True, f"pay power: {c.pay_power_band}"
    if c.ppo_lpa and c.ppo_lpa >= PAY_FLOOR_LPA:
        return True, f"states ₹{c.ppo_lpa:g} L"
    if d.p_pay >= 0.8:   # the best role's own stated pay cleared the floor
        return True, "a role states pay at the floor"
    return False, f"pay power {c.pay_power_band or 'unknown'}"


def plan(limit: int = PER_RUN) -> dict:
    """The funnel, layer by layer, and the companies that would be bought now. Free: no network."""
    init_db()
    since = utcnow() - timedelta(days=REFRESH_DAYS)
    with get_session() as session:
        decs = {d.company_id: d for d in session.exec(select(CompanyDecision)).all()}
        comps = {c.id: c for c in session.exec(select(Company).where(col(Company.id).in_(list(decs) or [-1]))).all()}
        bought = set(session.exec(select(MixerScrape.company_id).where(MixerScrape.at >= since)).all())
        fresh = set(session.exec(select(Job.company_id).where(Job.scraped_at >= since)).all())
        culture = decide.culture_index(session)
    layers = []
    alive = [cid for cid in decs if cid in comps]

    def layer(name: str, test) -> None:
        nonlocal alive
        kept, dropped = [], []
        for cid in alive:
            ok, why = test(comps[cid], decs[cid])
            (kept if ok else dropped).append((cid, why))
        layers.append({"layer": name, "in": len(alive), "out": len(kept),
                       "dropped_sample": [f"{comps[c].name}: {w}" for c, w in dropped[:4]]})
        alive = [c for c, _ in kept]

    layer("pay power", _pay_ok)
    layer("location", lambda c, d: (d.pool in ("Bengaluru", "Hyderabad", "Remote"), d.pool or "no pool"))
    layer("no DSA (India)", lambda c, d: (d.pool == "Remote" or d.dsa < MAX_DSA, f"DSA {d.dsa:.2f}"))
    layer("the brain", lambda c, d: (d.verdict in ("target", "fill_first"), f"{d.verdict}: {json.loads(d.why or '{}').get('reason', '')}"[:120]))
    layer(f"not bought in {REFRESH_DAYS} days", lambda c, d: (c.id not in bought, "bought recently"))

    def needs(c: Company, d: CompanyDecision):
        # Apify only where it answers something we do not hold (2026-10-23: "use only where it needed")
        gaps = _gaps(c, d, c.id in fresh, culture.get(c.id))
        return bool(gaps), ("needs " + ", ".join(gaps)) if gaps else "fresh ads, pay and culture already known"
    layer("needs paid data", needs)

    # best P(offer) first, taking the pools in turn so Remote and the cities each get a share
    by_pool: dict[str, list[int]] = {}
    for cid in sorted(alive, key=lambda x: (-decs[x].p_offer, x)):
        by_pool.setdefault(decs[cid].pool, []).append(cid)
    order, i = [], 0
    while any(by_pool.values()):
        for p in ("Bengaluru", "Remote", "Hyderabad"):
            if by_pool.get(p):
                order.append(by_pool[p].pop(0))
        i += 1
    picks = [{"company_id": cid, "name": comps[cid].name, "pool": decs[cid].pool, "p_offer": decs[cid].p_offer,
              "verdict": decs[cid].verdict, "dsa": decs[cid].dsa} for cid in order]
    est_one = (LINKEDIN["limit"] * LINKEDIN["per_item"] + LINKEDIN["start_fee"])
    est_india = est_one + NAUKRI["limit"] * NAUKRI["per_item"] + NAUKRI["start_fee"]
    now = picks[:limit]
    from jobhunter.actors import apify_source

    return {"layers": layers, "survivors": len(picks), "next": now, "queue_sample": picks[limit:limit + 12],
            "estimate_usd": round(sum(est_one if p["pool"] == "Remote" else est_india for p in now), 4),
            "spent_today_usd": apify_source.spent_today(), "mixer_spent_today_usd": apify_source.spent_today(LABEL),
            "mixer_daily_usd": DAILY_USD, "apify_daily_usd": apify_source.DAILY_USD, "referral_reserve_usd": RESERVE_USD,
            "can_spend_now": apify_source.DAILY_USD - apify_source.spent_today() - est_india >= RESERVE_USD
                             and apify_source.spent_today(LABEL) + est_india <= DAILY_USD}


# ------------------------------------------------------------------ the paid layers

def _same(a: str | None, company: Company) -> bool:
    if not a:
        return False
    k = admission.name_key(a)
    return bool(k) and k in {company.name_key, admission.name_key(company.name)}


def _when(v) -> datetime | None:
    try:
        if isinstance(v, (int, float)):
            return datetime.fromtimestamp(v / 1000, tz=timezone.utc).replace(tzinfo=None)
        if isinstance(v, str) and v:
            return datetime.fromisoformat(v.replace("Z", "+00:00")).replace(tzinfo=None)
    except (ValueError, OSError):
        return None
    return None


def _linkedin_job(it: dict, company: Company):
    from jobhunter.scrapers.base import RawJob

    desc = "\n".join(x for x in (it.get("description"), f"Experience level: {it['experienceLevel']}" if it.get("experienceLevel") else None,
                                  it.get("applicationsCount")) if x)
    return RawJob(company_name=company.name, title=it.get("title") or "", url=(it.get("url") or "").split("?")[0], source="apify-linkedin",
                  location=it.get("location"), remote=bool(re.search(r"remote", f"{it.get('location')} {it.get('title')}", re.I)),
                  description=desc, posted_at=_when(it.get("postedDate")), salary_raw=it.get("salary") or None,
                  extra={"linkedin_job_id": it.get("id"), "applicants": it.get("applicationsCount")})


def _naukri_job(it: dict, company: Company):
    from jobhunter.scrapers.base import RawJob

    sal = it.get("salary") or {}
    raw = None if (sal.get("label") or "").lower().startswith("not disclosed") or not sal.get("maximum") else \
        f"₹{sal['minimum'] / 1e5:g}–{sal['maximum'] / 1e5:g} LPA ({sal.get('label')})"
    exp = (it.get("experience") or {}).get("text")
    full = (it.get("description") or {}).get("full") or ""
    skills = ", ".join((it.get("skills") or {}).get("preferred") or [])
    desc = "\n".join(x for x in (f"Experience: {exp}" if exp else None, re.sub(r"<[^>]+>", "\n", full), f"Skills: {skills}" if skills else None) if x)
    loc = ", ".join(l.get("label") for l in it.get("locations") or [] if l.get("label"))
    return RawJob(company_name=company.name, title=it.get("title") or "", url=(it.get("url") or "").split("?")[0], source="apify-naukri",
                  location=loc or None, remote=str(it.get("wfhType") or "0") not in ("0", ""), description=desc,
                  posted_at=_when(it.get("createdDate")), salary_raw=raw, extra={"naukri_job_id": it.get("id")})


def _culture(items: list[dict]) -> tuple[float | None, int | None]:
    for it in items:
        info = ((it.get("ambitionBox") or {}).get("companyInfo") or {})
        try:
            return float(info["AggregateRating"]), int(str(info.get("ReviewsCount") or "0").replace(",", ""))
        except (KeyError, TypeError, ValueError):
            continue
    return None, None


def _save(raws: list) -> list[int]:
    """Through the same gate and store as every scraper; the ids of the rows now holding them."""
    from jobhunter.pipeline import ScrapeStats, persist

    if not raws:
        return []
    persist(raws, ScrapeStats(scraped=len(raws)))
    with get_session() as session:
        return [j.id for j in session.exec(select(Job).where(col(Job.url).in_([r.url for r in raws]))).all()]


def _gaps(c: Company, d: CompanyDecision, fresh_ads: bool, culture: tuple | None) -> list[str]:
    """What only a paid lookup would tell us about this company — empty means do not buy."""
    q = json.loads(d.why or "{}").get("questions") or {}
    gaps = []
    if not fresh_ads:
        gaps.append(f"current ads (none seen in {REFRESH_DAYS} days)")
    if (q.get("pay") or {}).get("fill_fixes"):
        gaps.append("pay")
    if d.pool != "Remote" and culture is None:
        gaps.append("culture rating")
    return gaps


def _remember_linkedin(company_id: int, ads: list[dict]) -> None:
    """Every LinkedIn ad names its company's page (companyUrl); keep the one their ads agree on."""
    from collections import Counter

    from jobhunter import fill

    urls = Counter(u for u in (fill.linkedin_company_url(a.get("companyUrl")) for a in ads) if u)
    if not urls:
        return
    url, n = urls.most_common(1)[0]
    with get_session() as session:
        c = session.get(Company, company_id)
        if c is not None and fill.set_linkedin(session, c, url, source=ads[0].get("url"), channel="apify-linkedin-ads",
                                               evidence=f"the company page on {n} of their LinkedIn ads"):
            session.add(c)
            session.commit()


def _buy(company: Company, pool: str) -> dict:
    from jobhunter.actors import apify_source

    out = {"company_id": company.id, "name": company.name, "pool": pool, "layers": [], "job_ids": [], "cost_usd": 0.0}
    location = "India" if pool == "Remote" else pool
    steps = [("linkedin", LINKEDIN, {"companyName": [company.name], "location": location, "limit": LINKEDIN["limit"]},
              lambda it: it.get("companyName"), _linkedin_job)]
    with get_session() as session:
        d = session.get(CompanyDecision, company.id)
        pay_known = d is not None and not ((json.loads(d.why or "{}").get("questions") or {}).get("pay") or {}).get("fill_fixes")
        culture_known = company.id in decide.culture_index(session)
    if pool != "Remote" and not (pay_known and culture_known):
        steps.append(("naukri", NAUKRI, {"keywords": company.name, "location": pool.lower(), "limit": NAUKRI["limit"]},
                      lambda it: (it.get("company") or {}).get("name"), _naukri_job))
    for source, conf, inp, who, make in steps:
        est = conf["limit"] * conf["per_item"] + conf["start_fee"]
        if apify_source.DAILY_USD - apify_source.spent_today() - est < RESERVE_USD:
            # referrals first: whatever the People-tab job may still need today is not ours to spend
            r = {"error": f"referral reserve: ${apify_source.spent_today()} of ${apify_source.DAILY_USD} spent today; "
                          f"${RESERVE_USD} stays for referrals"}
        else:
            r = apify_source.run_actor(conf["actor"], inp, per_item=conf["per_item"], start_fee=conf["start_fee"],
                                   max_items=conf["limit"], label=LABEL, cap_usd=DAILY_USD)
        items = r.get("items") or []
        mine = [it for it in items if _same(who(it), company)]
        ids = _save([make(it, company) for it in mine]) if mine else []
        if source == "linkedin" and mine:
            _remember_linkedin(company.id, mine)
        rating, reviews = _culture(mine) if source == "naukri" else (None, None)
        stopped = None
        if r.get("error"):
            stopped = "error"
        elif source == "linkedin" and not mine:
            stopped = f"no LinkedIn ad of theirs in {location} — nothing more bought"
        row = MixerScrape(company_id=company.id, source=source, found=len(items), matched=len(mine), saved=len(ids),
                          cost_usd=float(r.get("cost_usd") or 0), cached=bool(r.get("cached")), error=r.get("error"),
                          culture_rating=rating, culture_reviews=reviews, stopped=stopped)
        layer = {"source": source, "found": len(items), "matched": len(mine), "saved": len(ids),
                 "cost_usd": row.cost_usd, "cached": row.cached, "error": row.error}   # read before the commit expires the row
        with get_session() as session:
            session.add(row)
            session.commit()
        out["layers"].append({**layer,
                              "culture": {"rating": rating, "reviews": reviews} if rating is not None else None, "stopped": stopped})
        out["job_ids"] += ids
        out["cost_usd"] = round(out["cost_usd"] + layer["cost_usd"], 4)
        if stopped:
            break
    return out


def run(limit: int = PER_RUN, *, company_ids: list[int] | None = None) -> dict:
    """Decide, then buy: the plan's next companies (or `company_ids`), then re-score and re-decide."""
    from jobhunter import confidence

    decide.run()
    p = plan(limit)
    with get_session() as session:
        decs = {d.company_id: d for d in session.exec(select(CompanyDecision)).all()}
        targets = company_ids or [x["company_id"] for x in p["next"]]
        comps = {c.id: c for c in session.exec(select(Company).where(col(Company.id).in_(targets or [-1]))).all()}
    before = {cid: decs[cid].p_offer for cid in targets if cid in decs}
    bought = []
    for cid in targets:
        if cid not in comps or cid not in decs:
            continue
        b = _buy(comps[cid], decs[cid].pool)
        bought.append(b)
        if any(re.search(r"cap|reserve", l.get("error") or "") for l in b["layers"]):
            break
    new_ids = [j for b in bought for j in b["job_ids"]]
    if new_ids:
        confidence.recompute(job_ids=new_ids, force=True)
    decide.run()
    with get_session() as session:
        after = {d.company_id: d for d in session.exec(select(CompanyDecision).where(col(CompanyDecision.company_id).in_(targets or [-1]))).all()}
    for b in bought:
        a = after.get(b["company_id"])
        b["p_offer_before"] = round(before.get(b["company_id"], 0), 4)
        b["p_offer_after"] = round(a.p_offer, 4) if a else None
        b["verdict_after"] = a.verdict if a else None
        b.pop("job_ids")
    from jobhunter.actors import apify_source

    return {"funnel": p["layers"], "bought": bought, "jobs_saved": len(new_ids),
            "cost_usd": round(sum(b["cost_usd"] for b in bought), 4), "mixer_spent_today_usd": apify_source.spent_today(LABEL),
            "spent_today_usd": apify_source.spent_today()}


def history(limit: int = 50) -> list[dict]:
    with get_session() as session:
        rows = session.exec(select(MixerScrape).order_by(col(MixerScrape.at).desc()).limit(limit)).all()
        names = {c.id: c.name for c in session.exec(select(Company).where(col(Company.id).in_([r.company_id for r in rows] or [-1]))).all()}
    return [{**r.model_dump(), "name": names.get(r.company_id), "at": r.at.isoformat()} for r in rows]


__all__ = ["plan", "run", "history"]
