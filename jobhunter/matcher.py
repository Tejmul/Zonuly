"""Match scoring: a free lexical gate, then a paid rubric pass on the survivors.

The gate used to be an embedding cosine. OpenRouter serves no embedding models, so
it is rare-term-weighted vocabulary overlap now (`jobhunter.fit`) — which costs
nothing, needs no provider, and is inspectable term by term. Only what survives it
reaches the `judge` alias, which is the part that bills.

`Job.embed_sim` still holds that gate score; the column name is historical.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from sqlmodel import col, func, select

from jobhunter import CONFIG
from jobhunter import normalize as norm
from jobhunter import resume as resume_mod
from jobhunter.db import Job, get_session, get_setting, init_db, set_setting, utcnow

log = logging.getLogger(__name__)

_M = CONFIG["matching"]
# The floor is a share of the JD's distinctive weight, not a cosine, so it sits an
# order of magnitude lower than the old value and the percentile does the gating.
PREFILTER = float(_M.get("fit_prefilter", 0.02))
PREFILTER_PCT = _M.get("fit_prefilter_percentile", 70)
HIGH_MATCH = int(_M.get("high_match_threshold", 65))
MAX_YOE = int(CONFIG["search"].get("max_yoe", 3))
MIN_LPA = float(CONFIG["search"].get("min_lpa", 24))


# ---------------------------------------------------------------- lexical gate

#: bump when the scoring method changes so stale scores on a different scale are
#: recomputed instead of being silently compared against a percentile
PREFILTER_METHOD = "lexical-v1"
_METHOD_KEY = "prefilter_method"


def job_text(job: Job) -> str:
    bits = [job.title, job.company_name, job.location or "", (job.description or "")[:4000]]
    return "\n".join(b for b in bits if b)


def prefilter(limit: int = 400, *, recompute: bool = False) -> int:
    """Score jobs that have no gate score yet. Free — no model, no network.

    Switching scoring method invalidates every existing score: a percentile over a
    mix of cosine and lexical values would be arithmetic on two different scales.
    So a method change clears the column and redoes the corpus, which is cheap now.
    """
    init_db()
    from jobhunter import fit

    with get_session() as session:
        if recompute or get_setting(session, _METHOD_KEY) != PREFILTER_METHOD:
            stale = session.exec(select(Job).where(col(Job.embed_sim).is_not(None))).all()
            for job in stale:
                job.embed_sim = None
                session.add(job)
            session.commit()
            set_setting(session, _METHOD_KEY, PREFILTER_METHOD)
            if stale:
                log.info("prefilter method -> %s; cleared %d stale scores", PREFILTER_METHOD, len(stale))
            limit = max(limit, len(stale))

    fit.refresh()
    resume_terms = fit.resume_profile_terms(force=True)
    done = 0
    with get_session() as session:
        rows = session.exec(
            select(Job).where(col(Job.embed_sim).is_(None)).order_by(col(Job.scraped_at).desc()).limit(limit)
        ).all()
        for job in rows:
            job.embed_sim = fit.score_job(job, resume_terms)
            session.add(job)
            done += 1
        session.commit()
    log.info("prefilter: scored %d jobs lexically", done)
    return done


# ---------------------------------------------------------------- rubric scoring

RUBRIC_SYSTEM = """You are a hiring-funnel realist. You estimate the probability that THIS candidate
gets shortlisted (first recruiter/technical screen) for THIS specific job.

Be honest and calibrated, not encouraging:
- A job demanding more years than the candidate has is a real barrier, not a formality.
- Overlapping tech stack and shipped, relevant projects raise the odds a lot.
- Explicitly new-grad / early-career / "founding engineer at a small startup" postings favour this candidate.
- Senior/Staff/Principal titles for a candidate with under 3 years should score low, whatever the skill overlap.

Reply with JSON only."""

RUBRIC_PROMPT = """CANDIDATE
{profile}

JOB
Title: {title}
Company: {company}
Location: {location}{remote}
Salary: {salary}
Description (excerpt):
---
{description}
---

Score the shortlist probability 0-100 using this rubric, then combine:
1. skills_overlap (0-100): how much of the required stack the candidate demonstrably has
2. experience_fit (0-100): required years/seniority vs the candidate's {yoe} years
3. early_career_friendly (0-100): does the posting welcome new grads / early-career engineers
4. domain_fit (0-100): relevance of the candidate's projects and past work to this role

Reply with exactly:
{{
  "score": <int 0-100, the overall shortlist probability>,
  "skills_overlap": <int>,
  "experience_fit": <int>,
  "early_career_friendly": <int>,
  "domain_fit": <int>,
  "reasons": [<2-4 short strings, each citing something concrete from the job AND the resume>],
  "gaps": [<1-3 short strings: what the candidate is missing for this role>],
  "verdict": "<one sentence, plain and honest>"
}}"""


@dataclass
class Score:
    score: int
    reasons: list[str]
    gaps: list[str]
    verdict: str
    breakdown: dict


def score_job(job: Job, profile_text: str, yoe: float) -> Score | None:
    from jobhunter import llm

    salary = "not stated"
    if job.salary_min_lpa:
        salary = f"~{job.salary_min_lpa}-{job.salary_max_lpa} LPA (INR equivalent)"

    data = llm.chat_json(
        RUBRIC_PROMPT.format(
            profile=profile_text,
            title=job.title,
            company=job.company_name,
            location=job.location or "unspecified",
            remote=" (remote)" if job.remote else "",
            salary=salary,
            description=(job.description or "")[:4500],
            yoe=yoe,
        ),
        RUBRIC_SYSTEM,
        temperature=0.2,
        num_predict=700,
        alias="judge",          # the one call in the pipeline worth a reasoning model
        purpose="rubric",
        default=None,
    )
    if not isinstance(data, dict) or not isinstance(data.get("score"), (int, float)):
        return None

    raw = int(max(0, min(100, data["score"])))
    breakdown = {
        k: int(data[k]) for k in ("skills_overlap", "experience_fit", "early_career_friendly", "domain_fit")
        if isinstance(data.get(k), (int, float))
    }
    final = _apply_hard_rules(raw, job)
    return Score(
        score=final,
        reasons=[str(r)[:300] for r in (data.get("reasons") or [])][:4],
        gaps=[str(g)[:200] for g in (data.get("gaps") or [])][:3],
        verdict=str(data.get("verdict") or "")[:400],
        breakdown=breakdown,
    )


def _apply_hard_rules(score: int, job: Job) -> int:
    """Deterministic corrections the LLM is unreliable about."""
    if norm.is_senior(job.title):
        score = min(score, 35)   # a sub-3-year candidate is not getting a Staff screen
    if job.salary_max_lpa and job.salary_max_lpa < MIN_LPA:
        score = min(score, 40)   # below the user's floor, however good the fit
    if not job.description or len(job.description) < 200:
        score = min(score, 60)   # too little signal to be confident
    return int(max(0, min(100, score)))


def prefilter_threshold() -> float:
    """Effective cutoff: the configured percentile of the live distribution, floored by the absolute."""
    if not PREFILTER_PCT:
        return PREFILTER
    with get_session() as session:
        sims = sorted(session.exec(select(Job.embed_sim).where(col(Job.embed_sim).is_not(None))).all())
    if len(sims) < 20:
        return PREFILTER  # too few samples for a percentile to mean anything
    idx = min(len(sims) - 1, int(len(sims) * float(PREFILTER_PCT) / 100))
    return max(PREFILTER, round(sims[idx], 4))


def score_pending(limit: int = 40, *, min_sim: float | None = None) -> dict:
    """Rubric-score the highest-similarity unscored jobs."""
    init_db()
    threshold = prefilter_threshold() if min_sim is None else min_sim
    profile = resume_mod.load_profile()
    profile_text = resume_mod.profile_summary(profile)
    yoe = profile.get("years_experience") or 1

    stats = {"scored": 0, "skipped": 0, "high_match": 0, "failed": 0}
    with get_session() as session:
        rows = session.exec(
            select(Job)
            .where(col(Job.match_score).is_(None), col(Job.embed_sim).is_not(None))
            .order_by(col(Job.embed_sim).desc())
            .limit(limit)
        ).all()

        for job in rows:
            if (job.embed_sim or 0) < threshold:
                job.status = "ignored"
                job.match_score = 0
                job.match_reasons = f"Below similarity prefilter ({job.embed_sim:.2f} < {threshold})"
                job.scored_at = utcnow()
                session.add(job)
                stats["skipped"] += 1
                continue

            result = score_job(job, profile_text, yoe)
            if result is None:
                stats["failed"] += 1
                continue

            job.match_score = result.score
            job.match_reasons = json.dumps(
                {"reasons": result.reasons, "verdict": result.verdict, "breakdown": result.breakdown}
            )
            job.skill_gaps = json.dumps(result.gaps)
            job.scored_at = utcnow()
            job.status = "high_match" if result.score >= HIGH_MATCH else "scored"
            if job.status == "high_match":
                stats["high_match"] += 1
            session.add(job)
            stats["scored"] += 1
            session.commit()  # commit per job — scoring is slow and interruptible

        session.commit()

    log.info("scoring: %s", stats)
    return stats


def rescore_all() -> int:
    """Clear scores (e.g. after the resume changed) so the next pass redoes everything."""
    init_db()
    with get_session() as session:
        rows = session.exec(select(Job)).all()
        for job in rows:
            job.match_score = None
            job.match_reasons = None
            job.skill_gaps = None
            job.embed_sim = None
            job.scored_at = None
            job.notified = False
            if job.status in ("scored", "high_match", "ignored"):
                job.status = "new"
            session.add(job)
        session.commit()
    from jobhunter import fit

    fit.refresh()
    fit.resume_profile_terms(force=True)
    log.info("cleared scores on %d jobs", len(rows))
    return len(rows)


def counts() -> dict:
    with get_session() as session:
        total = session.exec(select(func.count()).select_from(Job)).one()
        scored = session.exec(
            select(func.count()).select_from(Job).where(col(Job.match_score).is_not(None))
        ).one()
        high = session.exec(
            select(func.count()).select_from(Job).where(Job.status == "high_match")
        ).one()
        embedded = session.exec(
            select(func.count()).select_from(Job).where(col(Job.embed_sim).is_not(None))
        ).one()
    return {"jobs": total, "embedded": embedded, "scored": scored, "high_match": high}
