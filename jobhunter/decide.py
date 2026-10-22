"""The decision brain — which Bengaluru / Hyderabad companies to spend the days on, and in what order.

Tejmul, 2026-10-23: "we should not target thousands of companies … around 100 in Bengaluru, 15
in Hyderabad … it has to use all the statistical and probabilistic measures, chances and odds …
eliminate the DSA companies which are high-paying right now … it should ask me to fill that
company, and we can target at least one company a day."

The method is Laya's (github.com/NandhaKishorM/laya), not its model: a decision is a set of
typed yes/no questions, each answered with a probability; an answer whose evidence is too thin
abstains instead of guessing; and the probabilities are scored against outcomes with proper
scoring rules (Brier, log loss). Laya answers its questions with a 421M-parameter encoder — this
machine runs no local model, so here every question is answered from the fields the pipeline
already holds, with the evidence quoted.

THE FIVE QUESTIONS, per company
    open    Is a role open now that a 2027 grad is eligible for?        noisy-OR over its roles
    reach   Does a referral ask get a reply?                             1 − (1 − r)^asks, r ~ Beta
    screen  Does the resume pass a cold screen?                          logistic on skills, needs, size
    loop    Do I clear their interview loop?                             Rasch: my DSA level vs theirs
    pay     Will they pay at least the floor?                            the pay term / pay power

    call    = reach · σ(logit(screen) + ln OR) + (1 − reach) · screen     a referral multiplies the odds
    P(offer) = open · call · loop · pay

Each answer is a Beta distribution — mean from the evidence, strength from how much evidence
there is — and P(offer) is sampled (seeded per company, so the same inputs give the same
answer) for its mean and 80% interval. The reply rate r is a Beta prior updated by every email
actually sent and every positive reply, so the brain gets less wrong as outcomes arrive.

VERDICTS
    skip        a guardrail fails (quarantined, do-not-contact, overrated, rejected tier, below
                the pay bar). A hiring claim their own site does not back is not a skip: it makes
                "a role is open" unlikely, and a fill re-checks it
    after_dsa   their loop is DSA-heavy and they pay at the top — parked until `me.dsa_level`
                rises (config), then they return on their own. India only: for Remote
                (2026-10-23, "even if there are a little bit of DSA rounds … I'm still fine")
                DSA only lowers the loop answer; the hard bar there is pay — thin pay is a
                skip, unknown pay is fill_first. Remote work itself is the culture bar, and every
                Remote company has passed it (a role confidence.py reads as open to India).
    fill_first  the abstention gate: too few of the deciding fields are known, or there is
                nobody to ask yet — fill it, then decide. (The interval is reported, not gated
                on: a product of five uncertain answers is always about 2.5× its mean wide, so
                its width says little about how much is known; the share of fields does.)
    target      enough evidence, a referrer to ask: write to them

The shortlist is the best `decide.shortlist[pool]` by P(offer) among target + fill_first. One
company a day is picked from it (today()); the snapshot is frozen on the pick so calibrate() can
score what the brain said then against what happened.

Layering: db / confidence (role terms, referral index) below; tick, api and the CLI above.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
from collections import Counter
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
from sqlmodel import col, select

from jobhunter import CONFIG
from jobhunter.db import (Company, CompanyDecision, DailyPick, DecisionOutcome, Email, Job, MixerScrape, Reply,
                          get_session, init_db, utcnow)

log = logging.getLogger(__name__)

_D = CONFIG.get("decide") or {}
SHORTLIST = {str(k): int(v) for k, v in (_D.get("shortlist") or {"Bengaluru": 100, "Hyderabad": 50, "Remote": 100}).items()}
POOLS = tuple(SHORTLIST)
REMOTE = "Remote"
CITY_POOLS = tuple(p for p in POOLS if p != REMOTE)
DSA_LEVEL = float((_D.get("me") or {}).get("dsa_level", 0.35))
_DSA = _D.get("dsa") or {}
DSA_HEAVY = float(_DSA.get("heavy", 0.6))
DSA_PARK_ALWAYS = float(_DSA.get("park_always_above", 0.8))
DSA_MARGIN = float(_DSA.get("margin", 0.15))
HIGH_PAY_LPA = float(_DSA.get("high_pay_lpa", 35))
HEAVY_NAMES = {str(n).strip().lower() for n in (_DSA.get("heavy_names") or [])}
_P = _D.get("priors") or {}
REPLY_PRIOR = (float((_P.get("reply") or {}).get("alpha", 2)), float((_P.get("reply") or {}).get("beta", 18)))
ASKS = int(_P.get("asks_per_company", 3))
FIND_UNSEARCHED = float(_P.get("find_referrer_unsearched", 0.5))
FIND_SEARCHED = float(_P.get("find_referrer_searched", 0.15))
REFERRAL_OR = float(_P.get("referral_odds_ratio", 6.0))
SCREEN = {"base": -4.0, "skills": 2.0, "gap": 0.8, "small": 1.0, **(_P.get("screen") or {})}
LOOP = {"slope": 4.0, "fit": 2.0, "base": -0.5, **(_P.get("loop") or {})}
_G = _D.get("gate") or {}
MIN_INFO = float(_G.get("min_info", 0.55))
DRAWS = int(_D.get("draws", 4000))
AVOID_DAYS = int(_D.get("avoid_repeat_days", 30))
_MX = CONFIG.get("mixer") or {}
CULTURE_MIN = float(_MX.get("culture_min", 3.0))
CULTURE_MIN_REVIEWS = int(_MX.get("culture_min_reviews", 10))
LOCAL_TZ = ZoneInfo(str((CONFIG.get("outreach") or {}).get("timezone", "Asia/Kolkata")))
PAY_FLOOR_LPA = float(((CONFIG.get("targeting") or {}).get("india") or {}).get("min_lpa", 20))

VERDICTS = ("target", "fill_first", "after_dsa", "skip")
VERDICT_LABEL = {"target": "Target", "fill_first": "Fill first", "after_dsa": "After DSA", "skip": "Skip"}
STAGES = ("open", "reach", "screen", "loop", "pay")
STAGE_LABEL = {"open": "A fresher role is open", "reach": "A referral ask gets a reply",
               "screen": "The resume passes a cold screen", "call": "Gets an interview call",
               "loop": "Clears their interview loop", "pay": "Pays at least the floor"}


def _sig(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def _logit(p: float) -> float:
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))


def _clip(p: float, lo: float = 0.01, hi: float = 0.99) -> float:
    return min(max(p, lo), hi)


def pool_of(company: Company, remote_roles: int = 0) -> str | None:
    """The first city pool named in the company's city ("Bengaluru · Hyderabad" → Bengaluru);
    otherwise Remote when it has a role open to someone in India (confidence's remote market:
    remote-anywhere or remote-from-India, wherever the company sits — US, UK, Europe, anywhere)."""
    city = (company.city or "").lower()
    hits = [(city.find(p.lower()), p) for p in CITY_POOLS if p.lower() in city]
    if hits:
        return min(hits)[1]
    return REMOTE if remote_roles and REMOTE in POOLS else None


# ------------------------------------------------------------------ DSA intensity

_HEAVY = [
    (re.compile(r"leetcode|codeforces|codechef|competitive programming|\bicpc\b|hackerrank|hackerearth|codility|codesignal", re.I),
     1.5, "names a competitive-programming / assessment platform"),
    (re.compile(r"data structures?\s*(?:and|&)\s*algorithms?|\bdsa\b", re.I), 1.2, "asks for data structures and algorithms"),
    (re.compile(r"online (?:coding )?(?:assessment|test)|coding (?:round|test|challenge)|\boa\b", re.I), 1.0,
     "an online assessment / coding round"),
    (re.compile(r"strong (?:cs|computer science) fundamentals|algorithmic (?:problem|thinking)|time (?:and|&) space complexity|"
                r"\bbig[- ]o\b|problem[- ]solving (?:skills|ability)", re.I), 0.6, "CS-fundamentals / problem-solving language"),
]
_LIGHT = [
    (re.compile(r"take[- ]home|work sample|paid trial|pair[- ]programming|no leetcode|no whiteboard|practical (?:assignment|interview|round)|"
                r"show us (?:what|something) you(?:'ve| have) built|portfolio|github profile|side projects?|shipped", re.I), -1.0,
     "a practical / built-work interview"),
]


# A heavy signal said the other way round — "What we care about less: LeetCode performance",
# "no LeetCode", "projects over LeetCode" — is evidence of a practical loop, not a DSA one.
_DOWNPLAYED = re.compile(r"(care (?:about )?less|less (?:about|important|weight)|matters? less|(?:don'?t|do not|we don'?t) "
                         r"(?:care|need|ask|test|do)|not (?:required|needed|important|a (?:priority|requirement|focus)))", re.I)
_SAME_LINE_NOT = re.compile(r"\b(no|not|without|instead of|rather than|never|over|isn'?t|aren'?t|don'?t|won'?t|skip)\b", re.I)


def _downplayed(blob: str, m: re.Match) -> bool:
    before = blob[max(0, m.start() - 300):m.start()]
    line = before[before.rfind("\n") + 1:]
    return bool(_DOWNPLAYED.search(before) or _SAME_LINE_NOT.search(line))


def _quote(text: str, m: re.Match, pad: int = 90) -> str:
    a, b = max(0, m.start() - pad), min(len(text), m.end() + pad)
    return ("…" if a else "") + re.sub(r"\s+", " ", text[a:b]).strip() + ("…" if b < len(text) else "")


def dsa_intensity(company: Company, texts: list[str]) -> tuple[float, list[dict], int]:
    """(0..1 intensity, evidence, how many signals were text) — log-odds over a prior of 0.35.

    Text signals come from their own postings and description; company signals stand in when the
    text says nothing about the loop (money, headcount and stage are what make a loop standardised)."""
    z = _logit(0.35)
    ev: list[dict] = []
    text_hits = 0
    blob = "\n".join(t for t in texts if t)[:60000]
    for rx, w, why in _HEAVY + _LIGHT:
        m = rx.search(blob)
        if m:
            if w > 0 and _downplayed(blob, m):
                w, why = -1.0, f"says DSA matters less to them ({why.split(' — ')[0]}, downplayed)"
            z += w
            text_hits += 1
            ev.append({"signal": why, "delta": w, "quote": _quote(blob, m)})
    name = (company.name or "").strip().lower()
    if name in HEAVY_NAMES:
        z += 2.5
        ev.append({"signal": "on your list of known DSA-loop companies (config decide.dsa.heavy_names)", "delta": 2.5})
    if company.pay_power_band == "deep_pockets":
        z += 0.8
        ev.append({"signal": "deep pockets — top-paying companies standardise on DSA rounds", "delta": 0.8,
                   "quote": company.pay_power_why})
    if company.valuation_usd_m and company.valuation_usd_m >= 1000:
        z += 0.8
        ev.append({"signal": f"valued ${company.valuation_usd_m:g}M", "delta": 0.8})
    n = company.team_size
    if n:
        d = 0.8 if n >= 500 else 0.4 if n >= 200 else -0.6 if n <= 30 else -0.3 if n <= 80 else 0.0
        if d:
            z += d
            ev.append({"signal": f"team of ~{n}" + (" — founders still hire on shipped work" if d < 0 else " — a hiring process, not a founder"),
                       "delta": d})
    st = (company.funding_stage or "").lower()
    if re.search(r"pre-?seed|seed", st):
        z -= 0.4
        ev.append({"signal": f"{company.funding_stage}", "delta": -0.4})
    elif re.search(r"series [d-z]|public|ipo", st):
        z += 0.6
        ev.append({"signal": f"{company.funding_stage}", "delta": 0.6})
    return round(_sig(z), 3), ev, text_hits


# ------------------------------------------------------------------ the five questions

def _terms(job: Job) -> dict[str, dict]:
    try:
        why = json.loads(job.selection_why or "{}")
    except json.JSONDecodeError:
        return {}
    return {t["key"]: t for t in why.get("terms") or []} | {"_cap": {"value": why.get("cap")}}


_HIRING_PRIOR = {"verified": 0.55, "careers_only": 0.45, "needs_recheck": 0.35, "role_missing": 0.3, "unreachable": 0.25,
                 "not_authorized": 0.12}


def q_open(company: Company, roles: list[tuple[Job, dict]]) -> dict:
    h = _HIRING_PRIOR.get(company.hiring_status or "", 0.3)
    elig = []
    for job, t in roles:
        if job.selection_market not in ("india", "remote"):
            continue
        s = (t.get("fresher") or t.get("seniority") or {}).get("value", 0.6)
        if job.selection_market == "remote":
            s *= (t.get("remote_terms") or {}).get("value", 0.5)
        r = (t.get("recency") or {}).get("value", 0.4)
        cap = (t.get("_cap") or {}).get("value")
        e = s * (0.5 + 0.5 * r) * (0.25 if cap is not None and cap <= 40 else 1.0)
        elig.append((e, job))
    if elig:
        miss = 1.0
        for e, _ in elig:
            miss *= 1 - 0.7 * e
        p = 1 - miss * (1 - 0.25 * h)
        best = max(elig, key=lambda x: x[0])
        why = f"{len(elig)} scored role(s); best fresher fit “{best[1].title}” ({best[0]:.2f}); hiring {company.hiring_status or 'unchecked'}"
        return {"p": _clip(p), "n": min(20, 6 + 2 * len(elig)), "why": why,
                "fill_fixes": company.hiring_status == "not_authorized"}
    return {"p": _clip(h * 0.6), "n": 4, "why": f"no scored role at the company; hiring {company.hiring_status or 'unchecked'}",
            "fill_fixes": True}


def reply_posterior(session) -> dict:
    """The per-ask reply rate: the Beta prior updated by every cold email sent and every positive reply."""
    sent_ids = [e for e in session.exec(select(Email.id).where(col(Email.status).in_(["sent", "replied"]),
                                                               Email.kind == "cold")).all()]
    pos = len({r for r in session.exec(select(Reply.email_id).where(col(Reply.email_id).in_(sent_ids or [-1]),
                                                                   Reply.sentiment == "positive")).all()})
    a, b = REPLY_PRIOR[0] + pos, REPLY_PRIOR[1] + len(sent_ids) - pos
    return {"alpha": a, "beta": b, "mean": a / (a + b), "sent": len(sent_ids), "positive": pos}


def q_reach(company: Company, ref: dict, r: float) -> dict:
    ready, verified = ref.get("ready", 0), ref.get("ready_verified", 0)
    if ready:
        q = 1.0 if verified else 0.8
        k = min(ready, ASKS)
        p = 1 - (1 - r * q) ** k
        return {"p": _clip(p), "n": 10, "fill_fixes": False,
                "why": f"{ready} ready referrer(s) ({verified} verified); {k} ask(s) at {r:.0%} each; best: {ref.get('best')}"}
    searched = bool(company.contacts_found_at) or ref.get("people", 0) > 0
    find = FIND_SEARCHED if searched else FIND_UNSEARCHED
    p = find * (1 - (1 - r * 0.8) ** ASKS)
    why = (f"{ref.get('people', 0)} people found, none ready to ask" if searched else "nobody searched for yet") + \
        f" — P(a fill finds one) {find:.0%}"
    return {"p": _clip(p), "n": 3, "why": why, "fill_fixes": True}


def _stage_small(company: Company) -> float:
    n = company.team_size
    if n:
        return 1.0 if n <= 50 else 0.75 if n <= 200 else 0.45 if n <= 1000 else 0.2
    st = (company.funding_stage or "").lower()
    return 0.9 if "seed" in st else 0.8 if "series a" in st else 0.6 if "series b" in st else 0.4 if st else 0.5


def q_screen(company: Company, best: tuple[Job, dict] | None) -> dict:
    t = best[1] if best else {}
    sk = (t.get("skills") or {}).get("value")
    gp = (t.get("gap") or {}).get("value")
    small = (t.get("stage") or {}).get("value", _stage_small(company))
    known = sk is not None
    sk, gp = (sk if sk is not None else 0.45), (gp if gp is not None else 0.4)
    z = SCREEN["base"] + SCREEN["skills"] * sk + SCREEN["gap"] * gp + SCREEN["small"] * small
    why = (f"skills {sk:.2f}, needs covered {gp:.2f} on “{best[0].title}”" if known else "no scored role — the resume's average fit") + \
        f"; size/stage {small:.2f}"
    return {"p": _clip(_sig(z)), "n": 10 if known else 4, "why": why, "fill_fixes": not known, "skills": sk}


def q_loop(dsa: float, dsa_text_hits: int, skills: float) -> dict:
    z = LOOP["slope"] * (DSA_LEVEL - dsa) + LOOP["fit"] * (skills - 0.5) + LOOP["base"]
    return {"p": _clip(_sig(z)), "n": min(16, 5 + 3 * dsa_text_hits), "fill_fixes": False,
            "why": f"your DSA {DSA_LEVEL:.2f} vs their loop {dsa:.2f}; skills fit {skills:.2f}"}


def q_pay(company: Company, best: tuple[Job, dict] | None, pool: str | None = None) -> tuple[dict, bool]:
    """(answer, high_paying). high_paying feeds the after-DSA rule. A remote role is paid from
    the company's home market, so its pay power reads higher than the same band in India."""
    t = (best[1].get("pay") if best else None) or {}
    job = best[0] if best else None
    stated = max([x for x in ((job.salary_max_lpa or job.salary_min_lpa) if job else None, company.ppo_lpa,
                              job.ppo_lpa if job else None) if x] or [0])
    high = stated >= HIGH_PAY_LPA or company.pay_power_band == "deep_pockets" or bool(
        company.valuation_usd_m and company.valuation_usd_m >= 1000)
    if t:
        v = t.get("value", 0.35)
        return {"p": _clip(0.15 + 0.8 * v), "n": 10 if v not in (0.35,) else 4, "why": t.get("why") or "", "fill_fixes": v == 0.35}, high
    if company.ppo_lpa and company.ppo_lpa >= PAY_FLOOR_LPA:
        return {"p": 0.85, "n": 8, "why": f"states ₹{company.ppo_lpa:g} L", "fill_fixes": False}, high
    band = company.pay_power_band or "unknown"
    table = ({"deep_pockets": 0.9, "pays": 0.8, "funded": 0.65, "thin": 0.25} if pool == REMOTE
             else {"deep_pockets": 0.75, "pays": 0.7, "funded": 0.55, "thin": 0.3})
    p = table.get(band, 0.45)
    return {"p": p, "n": 3 if band == "unknown" else 5, "why": f"pay power: {company.pay_power_why or band}",
            "fill_fixes": band == "unknown"}, high


def info_share(company: Company, best, ref: dict) -> tuple[float, list[str]]:
    fields = {
        "description": bool(company.description),
        "hiring checked": company.hiring_status in ("verified", "careers_only", "role_missing", "unreachable"),
        "size or stage": bool(company.team_size or company.funding_stage),
        "pay evidence": bool(company.ppo_lpa or (company.pay_power_band or "unknown") != "unknown"),
        "a scored role": best is not None,
        "people searched": bool(company.contacts_found_at) or ref.get("people", 0) > 0,
        "a ready referrer": ref.get("ready", 0) > 0,
    }
    return round(sum(fields.values()) / len(fields), 3), [k for k, v in fields.items() if not v]


def _skip_reason(company: Company) -> str | None:
    if company.status in ("quarantined", "merged"):
        return f"{company.status}: {company.status_reason or ''}".strip(": ")
    if company.status == "below_pay_bar":
        return company.status_reason or "under the India pay floor"
    if company.do_not_contact:
        return f"do not contact: {company.dnc_reason or ''}".strip(": ")
    if company.overrated and not company.list_key:
        return f"overrated: {company.overrated_reason or 'famous'}"
    if company.tier == "reject":
        return company.tier_reason or "rejected tier"
    return None


# ------------------------------------------------------------------ sampling

def sample(qs: dict[str, dict], seed: int) -> dict:
    """Mean and 80% interval of P(offer) and of the interview call, from Beta draws."""
    rng = np.random.default_rng(seed)
    d = {}
    for k in STAGES:
        m, n = _clip(qs[k]["p"]), max(2.0, float(qs[k]["n"]))
        d[k] = rng.beta(m * n, (1 - m) * n, DRAWS)
    s = np.clip(d["screen"], 1e-4, 1 - 1e-4)
    with_ref = 1 / (1 + np.exp(-(np.log(s / (1 - s)) + math.log(REFERRAL_OR))))
    call = d["reach"] * with_ref + (1 - d["reach"]) * s
    offer = d["open"] * call * d["loop"] * d["pay"]
    lo, hi = np.quantile(offer, [0.1, 0.9])
    return {"p_offer": float(offer.mean()), "p_lo": float(lo), "p_hi": float(hi), "p_call": float(call.mean())}


# ------------------------------------------------------------------ the engine

def parked_for_dsa(dsa: float, high_pay: bool, level: float | None = None) -> bool:
    """India: a DSA-heavy loop that is also well above your level — so raising `me.dsa_level`
    really does bring these back (the loop must stay more than `dsa.margin` above you)."""
    level = DSA_LEVEL if level is None else level
    return dsa >= DSA_HEAVY and dsa > level + DSA_MARGIN and (high_pay or dsa >= DSA_PARK_ALWAYS)


def culture_index(session) -> dict[int, tuple[float, int]]:
    """Latest AmbitionBox rating per company, as the mixer read it off their own Naukri ads."""
    out: dict[int, tuple[float, int]] = {}
    for row in session.exec(select(MixerScrape).where(col(MixerScrape.culture_rating).is_not(None))
                            .order_by(col(MixerScrape.at))).all():
        out[row.company_id] = (row.culture_rating, row.culture_reviews or 0)
    return out


def decide_one(company: Company, roles: list[tuple[Job, dict]], ref: dict, r: float,
               culture: tuple[float, int] | None = None) -> dict:
    pool = pool_of(company, sum(1 for j, _ in roles if j.selection_market == "remote"))
    best = max(roles, key=lambda x: x[0].selection_score or 0) if roles else None
    texts = [j.description or "" for j, _ in roles] + [company.description or "", company.needs_context or ""]
    dsa, dsa_ev, hits = dsa_intensity(company, texts)
    qs = {"open": q_open(company, roles), "reach": q_reach(company, ref, r)}
    qs["screen"] = q_screen(company, best)
    qs["loop"] = q_loop(dsa, hits, qs["screen"]["skills"])
    qs["pay"], high_pay = q_pay(company, best, pool)
    seed = int(hashlib.sha1(f"{company.id}".encode()).hexdigest()[:8], 16)
    s = sample(qs, seed)
    info, missing = info_share(company, best, ref)

    skip = _skip_reason(company)
    remote = pool == REMOTE
    band = company.pay_power_band or "unknown"
    if not skip and not remote and culture and culture[0] < CULTURE_MIN and culture[1] >= CULTURE_MIN_REVIEWS:
        # India: a poor rating on enough reviews. Remote: remote work is the culture bar, a rating is shown only
        skip = f"culture: rated {culture[0]:g}/5 on {culture[1]} AmbitionBox reviews"
    if not skip and remote and band == "thin":
        # 2026-10-23: "the only cap is that the pay has to be good"
        skip = f"remote, but the pay is thin: {company.pay_power_why or 'little money per head'}"
    if skip:
        verdict, reason = "skip", skip
    elif remote and band == "unknown" and not company.ppo_lpa:
        verdict, reason = "fill_first", "remote, but how well they pay is not known yet — the one thing a remote company must show"
    elif not remote and parked_for_dsa(dsa, high_pay):
        verdict = "after_dsa"
        reason = (f"DSA-heavy loop ({dsa:.2f} vs your {DSA_LEVEL:.2f})" + (" and high-paying" if high_pay else "") +
                  " — comes back when decide.me.dsa_level rises")
    elif info < MIN_INFO or not ref.get("ready"):
        verdict = "fill_first"
        why = []
        if not ref.get("ready"):
            why.append("nobody ready to ask yet")
        if info < MIN_INFO:
            why.append(f"only {info:.0%} of the deciding fields known (missing: {', '.join(missing)})")
        reason = "; ".join(why)
    else:
        verdict, reason = "target", f"{ref.get('ready')} ready referrer(s), {info:.0%} of the deciding fields known"

    return {
        "company_id": company.id, "pool": pool, "verdict": verdict, "reason": reason,
        **{k: round(v, 5) for k, v in s.items()},
        **{f"p_{k}": round(qs[k]["p"], 4) for k in STAGES},
        "dsa": dsa, "info": info, "missing": missing, "high_pay": high_pay,
        "questions": {k: {"label": STAGE_LABEL[k], "p": round(qs[k]["p"], 3), "evidence_strength": qs[k]["n"],
                          "why": qs[k]["why"], "fill_fixes": qs[k]["fill_fixes"]} for k in STAGES},
        "culture": {"rating": culture[0], "reviews": culture[1], "source": "AmbitionBox"} if culture else None,
        "dsa_evidence": dsa_ev, "best_job_id": best[0].id if best else None,
        "best_job_title": best[0].title if best else None,
    }


def _load(session, ids: list[int] | None = None):
    remote = Counter(session.exec(select(Job.company_id).where(Job.selection_market == "remote")).all())
    q = select(Company).where(col(Company.status).is_(None) | (Company.status != "merged"))
    if ids:
        q = q.where(col(Company.id).in_(ids))
    companies = [c for c in session.exec(q).all() if pool_of(c, remote.get(c.id, 0))]
    cids = [c.id for c in companies]
    roles: dict[int, list[tuple[Job, dict]]] = {}
    for j in session.exec(select(Job).where(col(Job.company_id).in_(cids or [-1]), col(Job.selection_why).is_not(None))).all():
        roles.setdefault(j.company_id, []).append((j, _terms(j)))
    return companies, roles


def run(ids: list[int] | None = None) -> dict:
    """Decide every Bengaluru / Hyderabad company (or `ids`), store it, re-rank the shortlists."""
    from jobhunter import confidence

    init_db()
    t0 = datetime.now()
    out = {"decided": 0, "by_verdict": {}, "by_pool": {}}
    with get_session() as session:
        rp = reply_posterior(session)
        refs = confidence.referral_index(session)
        companies, roles = _load(session, ids)
        culture = culture_index(session)
        decided = [(c, decide_one(c, roles.get(c.id, []), refs.get(c.id, {}), rp["mean"], culture.get(c.id))) for c in companies]
        # the weakest stage is the one furthest below what is normal among live companies — every
        # company's lowest raw number is the interview call, which says nothing
        live = [d for _, d in decided if d["verdict"] in ("target", "fill_first")] or [d for _, d in decided]
        med = {k: float(np.median([d[f"p_{k}"] for d in live])) for k in STAGES} if live else {}
        for c, d in decided:
            if med:
                d["weakest"] = min(STAGES, key=lambda k: d[f"p_{k}"] / max(med[k], 1e-6))
            row = session.get(CompanyDecision, c.id) or CompanyDecision(company_id=c.id, pool=d["pool"], verdict=d["verdict"],
                                                                        p_offer=0, p_lo=0, p_hi=0, p_open=0, p_reach=0, p_screen=0,
                                                                        p_call=0, p_loop=0, p_pay=0, dsa=0, info=0)
            for k in ("pool", "verdict", "p_offer", "p_lo", "p_hi", "p_open", "p_reach", "p_screen", "p_call", "p_loop", "p_pay",
                      "dsa", "info"):
                setattr(row, k, d[k])
            row.why = json.dumps(d, default=str)
            row.at = utcnow()
            session.add(row)
            out["decided"] += 1
            out["by_verdict"][d["verdict"]] = out["by_verdict"].get(d["verdict"], 0) + 1
        session.commit()
        out["by_pool"] = rank(session)
    out["reply_rate"] = rp
    out["seconds"] = round((datetime.now() - t0).total_seconds(), 1)
    log.info("decide: %s", out)
    return out


def rank(session) -> dict:
    """Shortlist slots: the best N per pool by P(offer) among target + fill_first."""
    out = {}
    rows = session.exec(select(CompanyDecision)).all()
    for pool in POOLS:
        live = sorted((r for r in rows if r.pool == pool and r.verdict in ("target", "fill_first")),
                      key=lambda r: (-r.p_offer, -r.p_hi, r.company_id))
        keep = {r.company_id: i + 1 for i, r in enumerate(live[:SHORTLIST[pool]])}
        for r in rows:
            if r.pool == pool:
                new = keep.get(r.company_id)
                if r.rank != new:
                    r.rank = new
                    session.add(r)
        out[pool] = {"shortlist": len(keep), "eligible": len(live)}
    session.commit()
    return out


def tick() -> dict:
    res = run()
    return {k: res[k] for k in ("decided", "by_verdict", "by_pool", "seconds")}


# ------------------------------------------------------------------ reading

def _odds(p: float) -> float:
    p = _clip(p, 1e-6, 1 - 1e-6)
    return p / (1 - p)


def row(d: CompanyDecision, company: Company | None, median_odds: float | None = None, full: bool = False) -> dict:
    why = json.loads(d.why or "{}")
    out = {
        "company_id": d.company_id, "name": company.name if company else None, "city": company.city if company else None,
        "pool": d.pool, "rank": d.rank, "verdict": d.verdict, "verdict_label": VERDICT_LABEL[d.verdict],
        "reason": why.get("reason"), "p_offer": d.p_offer, "p_lo": d.p_lo, "p_hi": d.p_hi,
        "one_in": round(1 / d.p_offer) if d.p_offer > 0 else None,
        "odds": round(_odds(d.p_offer), 4), "lift": round(_odds(d.p_offer) / median_odds, 2) if median_odds else None,
        "stages": {"open": d.p_open, "reach": d.p_reach, "screen": d.p_screen, "call": d.p_call, "loop": d.p_loop, "pay": d.p_pay},
        "dsa": d.dsa, "info": d.info, "weakest": why.get("weakest"), "missing": why.get("missing") or [],
        "best_job_id": why.get("best_job_id"), "best_job_title": why.get("best_job_title"), "culture": why.get("culture"),
        "filled_at": company.filled_at.isoformat() if company and company.filled_at else None,
        "at": d.at.isoformat() if d.at else None,
    }
    if full:
        out["questions"] = why.get("questions")
        out["dsa_evidence"] = why.get("dsa_evidence")
        out["stage_labels"] = STAGE_LABEL
    return out


def _median_odds(rows: list[CompanyDecision]) -> float | None:
    """The shortlist's median odds — "2× the typical company on your list", not on all 1,700."""
    live = sorted(_odds(r.p_offer) for r in rows if r.rank is not None)
    return live[len(live) // 2] if live else None


def shortlist(pool: str | None = None, verdict: str | None = None, limit: int = 200) -> dict:
    """The ranked shortlist (or, with `verdict` after_dsa / skip, those parked rows) plus counts."""
    with get_session() as session:
        rows = session.exec(select(CompanyDecision)).all()
        counts: dict[str, dict] = {}
        for r in rows:
            c = counts.setdefault(r.pool, {v: 0 for v in VERDICTS} | {"shortlist": 0})
            c[r.verdict] += 1
            c["shortlist"] += r.rank is not None
        med = {p: _median_odds([r for r in rows if r.pool == p]) for p in POOLS}
        sel = [r for r in rows if (pool in (None, "all") or r.pool == pool)]
        if verdict:
            sel = sorted((r for r in sel if r.verdict == verdict), key=lambda r: -r.p_offer)
        else:
            sel = sorted((r for r in sel if r.rank is not None), key=lambda r: (-r.p_offer, r.company_id))
        sel = sel[:limit]
        comps = {c.id: c for c in session.exec(select(Company).where(col(Company.id).in_([r.company_id for r in sel] or [-1]))).all()}
        items = [row(r, comps.get(r.company_id), med.get(r.pool)) for r in sel]
        at = max((r.at for r in rows), default=None)
    return {"items": items, "counts": counts, "dsa_level": DSA_LEVEL, "shortlist_sizes": SHORTLIST,
            "generated_at": at.isoformat() if at else None}


def company_view(company_id: int) -> dict | None:
    with get_session() as session:
        d = session.get(CompanyDecision, company_id)
        if d is None:
            return None
        med = _median_odds(session.exec(select(CompanyDecision).where(CompanyDecision.pool == d.pool)).all())
        return row(d, session.get(Company, company_id), med, full=True)


# ------------------------------------------------------------------ one company a day

def local_day(now: datetime | None = None) -> str:
    return (now or datetime.now(LOCAL_TZ)).astimezone(LOCAL_TZ).strftime("%Y-%m-%d")


def _worked_companies(session) -> set[int]:
    """Companies already being written to — a cold email sent or waiting approved."""
    return set(session.exec(select(Email.company_id).where(col(Email.status).in_(["sent", "replied", "approved"]))).all())


def _next_pick(session, day: str) -> DailyPick | None:
    since = (datetime.now(LOCAL_TZ) - timedelta(days=AVOID_DAYS)).strftime("%Y-%m-%d")
    recent = set(session.exec(select(DailyPick.company_id).where(DailyPick.day >= since)).all())
    worked = _worked_companies(session)
    cands = session.exec(select(CompanyDecision).where(col(CompanyDecision.rank).is_not(None))
                         .order_by(col(CompanyDecision.p_offer).desc(), col(CompanyDecision.company_id))).all()
    for d in cands:
        if d.company_id in recent or d.company_id in worked:
            continue
        pick = DailyPick(day=day, company_id=d.company_id, action="ask" if d.verdict == "target" else "fill",
                         p_offer=d.p_offer, p_reach=d.p_reach, verdict=d.verdict)
        session.add(pick)
        session.commit()
        session.refresh(pick)
        return pick
    return None


def _pick_view(session, pick: DailyPick) -> dict:
    c = session.get(Company, pick.company_id)
    d = session.get(CompanyDecision, pick.company_id)
    if pick.status == "picked" and pick.action == "fill" and c and c.filled_at and c.filled_at > pick.created_at:
        pick.status, pick.updated_at = "filled", utcnow()
        session.add(pick)
        session.commit()
    med = _median_odds(session.exec(select(CompanyDecision).where(CompanyDecision.pool == d.pool)).all()) if d else None
    return {"id": pick.id, "day": pick.day, "action": pick.action, "status": pick.status, "note": pick.note,
            "at_pick": {"p_offer": pick.p_offer, "p_reach": pick.p_reach, "verdict": pick.verdict},
            "decision": row(d, c, med, full=True) if d else None}


def today(create: bool = True) -> dict:
    """Today's company: the live pick for the local day, or a new one off the shortlist."""
    init_db()
    day = local_day()
    with get_session() as session:
        picks = session.exec(select(DailyPick).where(DailyPick.day == day).order_by(col(DailyPick.id))).all()
        live = next((p for p in reversed(picks) if p.status != "skipped"), None)
        if live is None and create:
            live = _next_pick(session, day)
        return {"day": day, "pick": _pick_view(session, live) if live else None,
                "skipped": [{"company_id": p.company_id, "note": p.note} for p in picks if p.status == "skipped"]}


def mark(status: str, note: str | None = None) -> dict:
    """asked | skipped for today's live pick. A skip picks the next company on the shortlist."""
    if status not in ("asked", "skipped", "filled"):
        raise ValueError("status must be asked, filled or skipped")
    day = local_day()
    with get_session() as session:
        live = next((p for p in reversed(session.exec(select(DailyPick).where(DailyPick.day == day)
                                                           .order_by(col(DailyPick.id))).all()) if p.status != "skipped"), None)
        if live is None:
            raise LookupError("no company picked today")
        live.status, live.note, live.updated_at = status, note or live.note, utcnow()
        session.add(live)
        session.commit()
    return today(create=(status == "skipped"))


def record_outcome(company_id: int, stage: str, note: str | None = None) -> dict:
    if stage not in ("replied", "referred", "interview", "offer", "rejected", "no_response"):
        raise ValueError("stage must be replied | referred | interview | offer | rejected | no_response")
    init_db()
    with get_session() as session:
        if session.get(Company, company_id) is None:
            raise LookupError(f"no company {company_id}")
        o = DecisionOutcome(company_id=company_id, stage=stage, note=note)
        session.add(o)
        session.commit()
        return {"id": o.id, "company_id": company_id, "stage": stage}


# ------------------------------------------------------------------ proper scoring

def _scores(pairs: list[tuple[float, int]]) -> dict:
    if not pairs:
        return {"n": 0}
    n = len(pairs)
    base = sum(y for _, y in pairs) / n
    brier = sum((p - y) ** 2 for p, y in pairs) / n
    ref = sum((base - y) ** 2 for _, y in pairs) / n
    ll = -sum(y * math.log(_clip(p, 1e-6, 1 - 1e-6)) + (1 - y) * math.log(1 - _clip(p, 1e-6, 1 - 1e-6)) for p, y in pairs) / n
    return {"n": n, "observed_rate": round(base, 4), "mean_predicted": round(sum(p for p, _ in pairs) / n, 4),
            "brier": round(brier, 4), "log_loss": round(ll, 4),
            "brier_skill": round(1 - brier / ref, 3) if ref > 0 else None,
            "verdict": "too few outcomes to judge" if n < 20 else
                       "better than guessing the base rate" if ref > 0 and brier < ref else "no better than the base rate"}


def calibrate() -> dict:
    """Score what the brain said against what happened.

    reply  each company we wrote to: the p_reach frozen on its pick (or the current one) vs a
           positive reply. offer: the p_offer at pick vs a recorded offer, for companies whose
           story has ended (offer / rejected / no_response recorded)."""
    init_db()
    with get_session() as session:
        sent = session.exec(select(Email.company_id, Email.id).where(col(Email.status).in_(["sent", "replied"]),
                                                                     Email.kind == "cold")).all()
        by_co: dict[int, list[int]] = {}
        for cid, eid in sent:
            by_co.setdefault(cid, []).append(eid)
        pos = set(session.exec(select(Reply.email_id).where(Reply.sentiment == "positive")).all())
        picks = {p.company_id: p for p in session.exec(select(DailyPick).order_by(col(DailyPick.id))).all()}
        decs = {d.company_id: d for d in session.exec(select(CompanyDecision)).all()}
        reply_pairs = []
        for cid, eids in by_co.items():
            p = picks[cid].p_reach if cid in picks else decs[cid].p_reach if cid in decs else None
            if p is not None:
                reply_pairs.append((p, int(any(e in pos for e in eids))))
        outcomes: dict[int, set[str]] = {}
        for o in session.exec(select(DecisionOutcome)).all():
            outcomes.setdefault(o.company_id, set()).add(o.stage)
        offer_pairs = [(picks[cid].p_offer if cid in picks else decs[cid].p_offer, int("offer" in st))
                       for cid, st in outcomes.items() if st & {"offer", "rejected", "no_response"} and (cid in picks or cid in decs)]
        rp = reply_posterior(session)
    return {"reply": _scores(reply_pairs), "offer": _scores(offer_pairs), "reply_rate_posterior": rp}


__all__ = ["run", "tick", "rank", "shortlist", "company_view", "today", "mark", "record_outcome", "calibrate",
           "dsa_intensity", "decide_one", "VERDICTS", "STAGES"]
