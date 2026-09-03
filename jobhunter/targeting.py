"""Company targeting: which companies we chase, and in what order.

The rules are Tejmul's, from MOTIV §2 and the 2026-09-03 refinement, and they live in
`config.yaml` under `targeting:` so they can be changed without touching code:

  1. UNDERRATED ONLY   — a household name is disqualified outright. Thousands of
                         applications are already pending there and people who were
                         selected are sitting on waitlists; our odds are not there.
  2. RECENTLY FUNDED   — pre-seed to Series B, under the mega-round ceiling.
  3. THE STIPEND BAR   — an internship paying under ₹50k/month is a hard reject. It is
                         a predictor, not a perk: a company that will not pay ₹50k for
                         an intern does not pay ₹30 LPA on conversion.
  4. THE TIERS         — PPO / full-time package ≥ ₹30 LPA → tier1; ≥ ₹24 LPA → tier2
                         ("category two"); below → reject.
  5. SILENCE IS NOT A NO — most postings never state a PPO, so a company that states no
                         pay is not rejected. If it is underrated, employs real engineers
                         (SDEs / AI engineers shipping the product) and its own careers
                         page confirms it is hiring, it grades `prospect` and enters the
                         queue behind tier2. Only with no engineering signal at all does
                         it sit at `unknown`, waiting for research.

Every verdict carries the rule that produced it and the sentence the number came from,
so `tier_reason` in the database is always answerable.

Layering: imports config, normalize and db only. Nothing here reaches the network —
`enrich.py`-style acquisition is the research layer's job, this module only judges.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlmodel import select

from jobhunter import CONFIG
from jobhunter import normalize as norm
from jobhunter.db import Company, Job, get_session, init_db, utcnow

log = logging.getLogger(__name__)

_T = CONFIG.get("targeting") or {}

STIPEND_MIN = int(_T.get("stipend_min_inr_month", 50_000))
TIER1_LPA = float(_T.get("ppo_tier1_lpa", 30))
TIER2_LPA = float(_T.get("ppo_tier2_lpa", 24))

_FUNDING = _T.get("funding") or {}
STAGES_OK = [s.lower() for s in _FUNDING.get("stages_ok", [])]
MAX_FUNDING_USD_M = float(_FUNDING.get("max_amount_usd_m", 400))
MAX_FUNDING_AGE_MONTHS = int(_FUNDING.get("max_age_months", 30))

REGIONS: list[dict] = _T.get("regions") or []
_HYPED = _T.get("hyped") or {}

TIER1, TIER2, PROSPECT, REJECT, UNKNOWN = "tier1", "tier2", "prospect", "reject", "unknown"
# queue order: pay we can point at, then pay we cannot but people we can, then the rest
TIER_ORDER = {TIER1: 0, TIER2: 1, PROSPECT: 2, UNKNOWN: 3, REJECT: 4}


# ------------------------------------------------------------------ underrated

def _name_tokens(name: str) -> set[str]:
    """Word-wise tokens of a company name, so "Amazon" hits but "Amazonia Labs" does not."""
    cleaned = re.sub(r"[^a-z0-9 ]+", " ", (name or "").lower())
    cleaned = re.sub(
        r"\b(inc|llc|ltd|limited|corp|corporation|gmbh|bv|plc|pvt|private|technologies|"
        r"technology|labs|lab|systems|solutions|software|the)\b",
        " ",
        cleaned,
    )
    return {t for t in cleaned.split() if t}


def hype_check(name: str) -> tuple[bool, str | None]:
    """(underrated?, why not). A household name is not a target, however good the role."""
    tokens = _name_tokens(name)
    joined = " ".join(sorted(tokens))
    for bucket, listed in (("big tech", _HYPED.get("big_tech", [])), ("India household name", _HYPED.get("india_household", []))):
        for entry in listed:
            e = entry.lower().strip()
            if not e:
                continue
            if (" " in e and e in joined) or (" " not in e and e in tokens):
                return False, f"{bucket}: matches '{entry}' — oversubscribed, DSA-gated, not where our odds are"
    return True, None


# ------------------------------------------------------------------ region

def region_of(*texts: str | None) -> str | None:
    """Which target region does this location text belong to? None when it does not say."""
    blob = " ".join(t.lower() for t in texts if t)
    if not blob:
        return None
    best: tuple[int, str] | None = None
    for entry in REGIONS:
        for needle in entry.get("match", []):
            idx = blob.find(needle.lower())
            if idx >= 0 and (best is None or idx < best[0]):
                best = (idx, entry["key"])
    return best[1] if best else None


def region_floor(key: str | None) -> float:
    for entry in REGIONS:
        if entry.get("key") == key:
            return float(entry.get("min_lpa") or 0)
    return 0.0


# ------------------------------------------------------------------ the grade

@dataclass
class Grade:
    """One company's verdict, with every rule that fired."""

    tier: str = UNKNOWN
    reason: str = "not graded"
    underrated: bool | None = None
    hype_reason: str | None = None
    region: str | None = None
    stipend_inr_month: int | None = None
    stipend_evidence: str | None = None
    ppo_lpa: float | None = None
    ppo_evidence: str | None = None
    ppo_source: str | None = None          # "ppo" | "salary" | None
    eng_roles: int = 0                     # engineering postings / engineer contacts seen
    pay_basis: str | None = None           # stated | funding-strong | funding-weak | none
    pay_basis_evidence: str | None = None
    checks: list[str] = field(default_factory=list)

    @property
    def is_target(self) -> bool:
        return self.tier in (TIER1, TIER2, PROSPECT)

    def as_dict(self) -> dict:
        return {
            "tier": self.tier, "reason": self.reason, "underrated": self.underrated,
            "region": self.region, "stipend_inr_month": self.stipend_inr_month,
            "ppo_lpa": self.ppo_lpa, "ppo_source": self.ppo_source,
            "eng_roles": self.eng_roles, "checks": self.checks,
        }


def grade(
    name: str,
    *,
    region: str | None = None,
    stipend_inr_month: int | None = None,
    stipend_evidence: str | None = None,
    ppo_lpa: float | None = None,
    ppo_evidence: str | None = None,
    ppo_source: str | None = None,
    funding_stage: str | None = None,
    funding_amount_usd_m: float | None = None,
    funding_announced: str | None = None,
    funding_evidence: str | None = None,
    team_size: int | None = None,
    eng_roles: int = 0,
    hiring_status: str | None = None,
) -> Grade:
    """Apply the five rules in order. The first one that decides, decides."""
    g = Grade(
        region=region,
        stipend_inr_month=stipend_inr_month, stipend_evidence=stipend_evidence,
        ppo_lpa=ppo_lpa, ppo_evidence=ppo_evidence, ppo_source=ppo_source,
        eng_roles=eng_roles,
    )

    # 1. underrated
    g.underrated, g.hype_reason = hype_check(name)
    if not g.underrated:
        g.checks.append(f"hyped: {g.hype_reason}")
        g.tier, g.reason = REJECT, g.hype_reason or "hyped company"
        return g
    g.checks.append("underrated: not on the hyped list")

    # 2. recently funded, still small
    if funding_amount_usd_m is not None and funding_amount_usd_m > MAX_FUNDING_USD_M:
        g.checks.append(f"funding ${funding_amount_usd_m}M over the ${MAX_FUNDING_USD_M}M ceiling")
        g.tier, g.reason = REJECT, f"raised ${funding_amount_usd_m}M — past the point of being underrated"
        return g
    # A later round is no longer a rejection (Tejmul, 2026-09-03: a big recent raise means
    # they can pay — that is Pay Power's job). "Underrated" is now about size and hype:
    # the team ceiling lives in harvest.max_team and is applied at admission.
    if funding_stage and STAGES_OK and funding_stage.lower().strip() not in STAGES_OK:
        g.checks.append(f"stage '{funding_stage}' is past the seed–Series B window — noted, not rejected")
    if funding_stage or funding_amount_usd_m:
        age = _months_old(funding_announced)
        stale = age is not None and age > MAX_FUNDING_AGE_MONTHS
        g.checks.append(
            f"funding: {funding_stage or 'stage unstated'}"
            + (f" ${funding_amount_usd_m}M" if funding_amount_usd_m else "")
            + (f", {age} months old — stale" if stale else "")
        )

    # 3. the stipend bar
    if stipend_inr_month is not None:
        if stipend_inr_month < STIPEND_MIN:
            g.checks.append(f"stipend ₹{stipend_inr_month:,}/month < ₹{STIPEND_MIN:,}")
            g.tier = REJECT
            g.reason = (
                f"internship stipend ₹{stipend_inr_month:,}/month is under the ₹{STIPEND_MIN:,} bar — "
                "a company paying this will not convert at ₹30 LPA"
            )
            return g
        g.checks.append(f"stipend ₹{stipend_inr_month:,}/month clears the ₹{STIPEND_MIN:,} bar")

    # 4. the tiers
    if ppo_lpa is None:
        # Most postings never say. Then the proxy is funding — ability to pay, estimated
        # from what they raised, when, and where they are (Tejmul: "based on valuation
        # we decide") — and after that the people signal: engineers shipping the
        # product, hiring confirmed on their own page.
        g.checks.append("pay unknown: silence is not a rejection")
        stated = (
            f"stipend ₹{stipend_inr_month:,}/month clears the bar, no PPO figure stated"
            if stipend_inr_month else "no pay figure stated anywhere we have read"
        )
        g.pay_basis, g.pay_basis_evidence = funding_pay_basis(
            region, funding_stage, funding_amount_usd_m, funding_announced,
            team_size=team_size, evidence=funding_evidence)
        g.checks.append(f"pay basis: {g.pay_basis} — {g.pay_basis_evidence}")
        if g.pay_basis == "funding-strong" and eng_roles:
            g.tier = PROSPECT
            g.reason = f"{stated}; {g.pay_basis_evidence}; {eng_roles} engineering role(s)"
            g.checks.append(f"engineering signal: {eng_roles} role(s); hiring {hiring_status or 'unchecked'}")
        elif eng_roles and hiring_status == "verified":
            g.tier = PROSPECT
            g.reason = (
                f"{stated} — but {eng_roles} engineering role(s) and hiring confirmed on their "
                "own careers page, so it stays in the queue"
            )
            g.checks.append(f"engineering signal: {eng_roles} role(s); hiring verified")
        elif eng_roles:
            g.tier = UNKNOWN
            g.reason = f"{stated}; {eng_roles} engineering role(s) seen but hiring is {hiring_status or 'unchecked'}"
            g.checks.append(f"engineering signal: {eng_roles} role(s); hiring {hiring_status or 'unchecked'}")
        else:
            g.tier = UNKNOWN
            g.reason = f"{stated} and no engineering signal yet — kept for research, not rejected"
        if g.pay_basis == "funding-weak":
            g.reason += f" ({g.pay_basis_evidence})"
        return g

    g.pay_basis = "stated"
    g.pay_basis_evidence = ppo_evidence or f"₹{ppo_lpa:g} LPA stated in a posting"
    where = "PPO" if ppo_source == "ppo" else "full-time band"
    floor = region_floor(region)
    if ppo_lpa >= TIER1_LPA:
        g.tier = TIER1
        g.reason = f"{where} ₹{ppo_lpa:g} LPA ≥ ₹{TIER1_LPA:g} LPA"
    elif ppo_lpa >= TIER2_LPA:
        g.tier = TIER2
        g.reason = f"{where} ₹{ppo_lpa:g} LPA is in the ₹{TIER2_LPA:g}–{TIER1_LPA:g} LPA band — category two"
        if floor and ppo_lpa < floor:
            g.reason += f" (under the ₹{floor:g} LPA {region} floor)"
    else:
        g.tier = REJECT
        g.reason = f"{where} ₹{ppo_lpa:g} LPA is below the ₹{TIER2_LPA:g} LPA floor"
    g.checks.append(f"pay: {g.reason}")
    return g


_STRONG_USD_M = float(_FUNDING.get("strong_min_usd_m", 3))
_WEAK_USD_M = float(_FUNDING.get("weak_max_usd_m", 2))
_LATER_STAGES = ("series a", "series b", "series c", "series d", "series e", "series f")

# ------------------------------------------------------------------ PAY POWER, the benchmark

_PP = _T.get("pay_power") or {}
PP_DEEP_USD_M = float(_PP.get("deep_min_usd_m", 10))
PP_DEEP_MONTHS = int(_PP.get("deep_max_months", 24))
PP_DEEP_VALUATION_USD_M = float(_PP.get("deep_valuation_usd_m", 50))
PP_YC_MIN_TEAM = int(_PP.get("yc_min_team", 10))
PP_PER_HEAD_OK_K = float(_PP.get("per_head_comfortable_usd_k", 200))
PP_SCORE = {"pays": 100, "deep_pockets": 85, "funded": 65, "thin": 30, "unknown": 0}
PP_LABEL = {"pays": "Pays — they wrote the number", "deep_pockets": "Deep pockets",
            "funded": "Funded — can pay", "thin": "Thin — no evidence they can", "unknown": "Unknown — needs research"}


@dataclass
class PayPower:
    band: str = "unknown"
    score: int = 0
    why: str = "nothing found yet"
    per_head_usd_k: float | None = None

    def as_dict(self) -> dict:
        return {"band": self.band, "score": self.score, "why": self.why,
                "label": PP_LABEL.get(self.band, self.band), "per_head_usd_k": self.per_head_usd_k}


def pay_power(*, region: str | None, ppo_lpa: float | None, ppo_evidence: str | None,
              stage: str | None, amount_usd_m: float | None, announced: str | None,
              funding_evidence: str | None, valuation_usd_m: float | None,
              team_size: int | None) -> PayPower:
    """How easily can this company pay a fresher ₹30–40 L? The bands are in config.yaml
    and on the page; the first rule that fits decides, and the sentence says which."""
    pp = PayPower()
    if amount_usd_m and team_size:
        pp.per_head_usd_k = round(amount_usd_m * 1000 / team_size)
    floor = max(TIER1_LPA, region_floor(region))
    if ppo_lpa is not None and ppo_lpa >= floor:
        pp.band, pp.score = "pays", PP_SCORE["pays"]
        pp.why = f"a posting states ₹{ppo_lpa:g} LPA (≥ ₹{floor:g} L)" + (f" — {ppo_evidence[:120]}" if ppo_evidence else "")
        return pp
    stage_l = (stage or "").lower().strip()
    age = _months_old(announced)
    have_funding = bool(stage_l or amount_usd_m or valuation_usd_m)
    desc = " ".join(p for p in [stage_l or None, f"${amount_usd_m:g}M" if amount_usd_m else None,
                                f"{age} months ago" if age is not None else None] if p)
    if region == "india" and have_funding:
        pp.band, pp.score = "thin", PP_SCORE["thin"]
        pp.why = f"{desc or 'funding on record'} — but rupee pay does not follow dollar rounds; needs the stated number"
        return pp
    recent_deep = age is None or age <= PP_DEEP_MONTHS
    recent = age is None or age <= MAX_FUNDING_AGE_MONTHS
    head = f" (≈ ${pp.per_head_usd_k:,.0f}k per head)" if pp.per_head_usd_k else ""
    if valuation_usd_m and valuation_usd_m >= PP_DEEP_VALUATION_USD_M:
        pp.band, pp.score = "deep_pockets", PP_SCORE["deep_pockets"]
        pp.why = f"valued at ${valuation_usd_m:g}M — ₹30–40 L is a rounding error"
        return pp
    if amount_usd_m and amount_usd_m >= PP_DEEP_USD_M and recent_deep:
        pp.band, pp.score = "deep_pockets", PP_SCORE["deep_pockets"]
        pp.why = f"raised {desc}{head} — ₹30–40 L is a rounding error"
        return pp
    if stage_l in _LATER_STAGES and recent:
        pp.band, pp.score = "deep_pockets", PP_SCORE["deep_pockets"]
        pp.why = f"{desc}{head} — a priced round inside {MAX_FUNDING_AGE_MONTHS} months"
        return pp
    if amount_usd_m and amount_usd_m >= _STRONG_USD_M and recent:
        pp.band, pp.score = "funded", PP_SCORE["funded"]
        pp.why = f"raised {desc}{head} — a real seed, can pay a remote engineer ₹30–40 L"
        return pp
    if "yc" in (funding_evidence or "").lower() and recent and (team_size or 0) >= PP_YC_MIN_TEAM:
        pp.band, pp.score = "funded", PP_SCORE["funded"]
        pp.why = f"YC {desc}, team of {team_size} — hired past the batch cheque, so they raised more"
        return pp
    if have_funding:
        pp.band, pp.score = "thin", PP_SCORE["thin"]
        why = "older than %d months" % MAX_FUNDING_AGE_MONTHS if not recent else ("small" if amount_usd_m else "amount unstated")
        pp.why = f"{desc or 'funding on record'} — {why}; no evidence they can pay ₹30 L"
        return pp
    if ppo_lpa is not None:
        pp.band, pp.score = "thin", PP_SCORE["thin"]
        pp.why = f"a posting states ₹{ppo_lpa:g} LPA — under the ₹{floor:g} L floor"
        return pp
    return pp


def funding_pay_basis(region: str | None, stage: str | None, amount_usd_m: float | None,
                      announced: str | None, *, team_size: int | None = None,
                      evidence: str | None = None) -> tuple[str, str]:
    """(basis, evidence) — can they pay a fresher ₹30 L+, judged from funding alone.

    The estimate, in words a person would use: "they raised $12M in a seed round eight
    months ago and sit in the US — a $40k remote engineer is nothing to them". India
    is the exception: rupee salaries do not follow dollar rounds, so funding never
    lifts an Indian company above `funding-weak` without a stated figure.
    """
    stage_l = (stage or "").lower().strip()
    if not stage_l and amount_usd_m is None:
        return "none", "no funding found on their site, in postings or in press"
    age = _months_old(announced)
    parts = []
    if stage_l:
        parts.append(stage_l)
    if amount_usd_m is not None:
        parts.append(f"${amount_usd_m:g}M")
    if age is not None:
        parts.append(f"{age} months ago")
    desc = " ".join(parts) or "funding on record"
    stale = age is not None and age > MAX_FUNDING_AGE_MONTHS
    strong_amount = amount_usd_m is not None and amount_usd_m >= _STRONG_USD_M
    strong_stage = stage_l in _LATER_STAGES
    weak = (amount_usd_m is not None and amount_usd_m < _WEAK_USD_M) or stage_l == "pre-seed"
    if region == "india":
        return "funding-weak", f"{desc} — but rupee pay does not follow dollar rounds; needs a stated figure"
    if (strong_amount or strong_stage) and not stale:
        where = f"{region.upper()} HQ" if region else "outside India"
        return "funding-strong", f"raised {desc}, {where} — able to pay a $40k remote engineer without noticing"
    # A YC company states no amount, but the batch and the headcount are both on record:
    # a seed company that has hired ten or more people has raised past the batch cheque.
    if "yc" in (evidence or "").lower() and not stale and (team_size or 0) >= 10:
        where = f"{region.upper()} HQ" if region else "outside India"
        return "funding-strong", (f"YC {desc}, team of {team_size}, {where} — a seed company that hired "
                                  f"{team_size} people has raised beyond the batch cheque")
    if weak or stale:
        why = "stale" if stale else "small"
        return "funding-weak", f"{desc} — {why} for a ₹30 L hire; needs a stated figure"
    return "funding-weak", f"{desc} — amount unstated, cannot judge ability to pay"


_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"], start=1)}


def _months_old(announced: str | None) -> int | None:
    """Age in months of a funding date written any of the ways the press writes it."""
    if not announced:
        return None
    text = announced.strip().lower()
    dt: datetime | None = None
    m = re.search(r"(\d{4})-(\d{2})(?:-(\d{2}))?", text)
    if m:
        try:
            dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3) or 1))
        except ValueError:
            dt = None
    if dt is None:
        m = re.search(r"([a-z]+)\s+(\d{4})", text)
        if m and m.group(1) in _MONTHS:
            dt = datetime(int(m.group(2)), _MONTHS[m.group(1)], 1)
    if dt is None:
        return None
    return max(0, int((utcnow() - dt) / timedelta(days=30.44)))


# ------------------------------------------------------------------ job-level pay

def extract_job_pay(limit: int | None = None, *, only_missing: bool = True) -> dict:
    """Regex pass over job descriptions filling is_internship / stipend / ppo.

    Free and deterministic — no model call. This is what gives company grading its
    evidence, so it runs before `grade_companies`.
    """
    init_db()
    stats = {"scanned": 0, "internships": 0, "stipends": 0, "ppos": 0}
    with get_session() as session:
        q = select(Job)
        if only_missing:
            q = q.where(Job.stipend_inr_month.is_(None), Job.ppo_lpa.is_(None))  # type: ignore[union-attr]
        if limit:
            q = q.limit(limit)
        for job in session.exec(q).all():
            stats["scanned"] += 1
            facts = norm.parse_pay(job.salary_raw, job.description)
            intern = norm.is_internship(job.title, job.description)
            job.is_internship = intern
            if intern:
                stats["internships"] += 1
            if facts.stipend_inr_month:
                job.stipend_inr_month = facts.stipend_inr_month
                stats["stipends"] += 1
            if facts.ppo_lpa:
                job.ppo_lpa = facts.ppo_lpa
                stats["ppos"] += 1
            session.add(job)
        session.commit()
    return stats


# ------------------------------------------------------------------ company grading

def evidence_for(session, company: Company) -> dict:
    """Everything we already know about one company, gathered from its own job rows."""
    jobs = session.exec(select(Job).where(Job.company_id == company.id)).all()
    stipend = stipend_ev = None
    ppo = ppo_ev = ppo_src = None
    for j in jobs:
        if j.stipend_inr_month and (stipend is None or j.stipend_inr_month > stipend):
            stipend = j.stipend_inr_month
            facts = norm.parse_pay(j.salary_raw, j.description)
            stipend_ev = facts.stipend_evidence or (j.salary_raw or None)
        if j.ppo_lpa and (ppo is None or j.ppo_lpa > ppo):
            ppo, ppo_src = j.ppo_lpa, "ppo"
            facts = norm.parse_pay(j.salary_raw, j.description)
            ppo_ev = facts.ppo_evidence
    if ppo is None:
        # no stated PPO: the best full-time band we scraped stands in for it, labelled as such
        bands = [(j.salary_max_lpa or j.salary_min_lpa, j) for j in jobs if not j.is_internship and (j.salary_max_lpa or j.salary_min_lpa)]
        if bands:
            value, j = max(bands, key=lambda b: b[0])
            ppo, ppo_src = float(value), "salary"
            ppo_ev = f"{j.title}: {j.salary_raw}" if j.salary_raw else f"{j.title}: ₹{value:g} LPA"
    eng_roles = sum(1 for j in jobs if norm.title_relevant(j.title or ""))
    locations = " ; ".join(filter(None, [j.location for j in jobs[:40]]))
    remote = any(j.remote for j in jobs)
    return {
        "stipend_inr_month": stipend, "stipend_evidence": stipend_ev,
        "ppo_lpa": ppo, "ppo_evidence": ppo_ev, "ppo_source": ppo_src,
        "region": region_of(company.hq_region, locations, "remote" if remote else None),
        "jobs": len(jobs), "eng_roles": eng_roles,
    }


def grade_company(session, company: Company) -> Grade:
    """Grade one company row from its own columns plus its jobs, and write the verdict."""
    ev = evidence_for(session, company)
    g = grade(
        company.name,
        region=company.hq_region or ev["region"],
        stipend_inr_month=company.stipend_inr_month or ev["stipend_inr_month"],
        stipend_evidence=company.stipend_evidence or ev["stipend_evidence"],
        ppo_lpa=company.ppo_lpa or ev["ppo_lpa"],
        ppo_evidence=company.ppo_evidence or ev["ppo_evidence"],
        ppo_source=ev["ppo_source"] if company.ppo_lpa is None else "ppo",
        funding_stage=company.funding_stage,
        funding_amount_usd_m=company.funding_amount_usd_m,
        funding_announced=company.funding_announced,
        funding_evidence=company.funding_evidence,
        team_size=company.team_size,
        eng_roles=ev["eng_roles"],
        hiring_status=company.hiring_status,
    )
    company.tier = g.tier
    company.tier_reason = g.reason
    company.underrated = g.underrated
    company.hype_reason = g.hype_reason
    company.hq_region = company.hq_region or g.region
    company.stipend_inr_month = g.stipend_inr_month
    company.stipend_evidence = g.stipend_evidence
    company.ppo_lpa = g.ppo_lpa
    company.ppo_evidence = g.ppo_evidence
    company.pay_basis = g.pay_basis
    company.pay_basis_evidence = g.pay_basis_evidence
    pp = pay_power(
        region=company.hq_region or g.region, ppo_lpa=g.ppo_lpa, ppo_evidence=g.ppo_evidence,
        stage=company.funding_stage, amount_usd_m=company.funding_amount_usd_m,
        announced=company.funding_announced, funding_evidence=company.funding_evidence,
        valuation_usd_m=company.valuation_usd_m, team_size=company.team_size,
    )
    company.pay_power = pp.score
    company.pay_power_band = pp.band
    company.pay_power_why = pp.why
    company.money_per_head_usd_k = pp.per_head_usd_k
    company.graded_at = utcnow()
    session.add(company)
    return g


def grade_companies(limit: int | None = None, *, regrade: bool = True) -> dict:
    """Grade every company in the registry. Idempotent; commits per company."""
    init_db()
    counts = {TIER1: 0, TIER2: 0, PROSPECT: 0, UNKNOWN: 0, REJECT: 0}
    hyped = 0
    with get_session() as session:
        q = select(Company)
        if not regrade:
            q = q.where(Company.tier.is_(None))  # type: ignore[union-attr]
        if limit:
            q = q.limit(limit)
        for company in session.exec(q).all():
            g = grade_company(session, company)
            counts[g.tier] = counts.get(g.tier, 0) + 1
            if g.underrated is False:
                hyped += 1
            session.commit()   # per-company: a kill mid-run loses nothing already decided
    return {**counts, "hyped_excluded": hyped}


def targets(tier: str | None = None, limit: int = 50) -> list[dict]:
    """The graded registry, best first — what the dashboard and the CLI both read."""
    init_db()
    order = TIER_ORDER
    with get_session() as session:
        rows = session.exec(select(Company)).all()
    out = []
    for c in rows:
        if tier and c.tier != tier:
            continue
        out.append({
            "id": c.id, "name": c.name, "tier": c.tier, "reason": c.tier_reason,
            "underrated": c.underrated,
            "description": c.description, "region": c.hq_region,
            "stipend_inr_month": c.stipend_inr_month, "ppo_lpa": c.ppo_lpa,
            "funding_stage": c.funding_stage, "funding_amount_usd_m": c.funding_amount_usd_m,
            "investors": json.loads(c.funding_investors) if c.funding_investors else [],
            "hiring_status": c.hiring_status, "careers_url": c.careers_url,
        })
    out.sort(key=lambda r: (order.get(r["tier"] or UNKNOWN, 4), -(r["ppo_lpa"] or 0), r["name"]))
    return out[:limit]


__all__ = [
    "Grade", "grade", "grade_company", "grade_companies", "extract_job_pay",
    "hype_check", "region_of", "targets",
    "TIER1", "TIER2", "PROSPECT", "REJECT", "UNKNOWN", "TIER_ORDER",
]
