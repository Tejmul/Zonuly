"""Selection confidence — how likely is it that THIS role selects Tejmul, 0–100, and why.

One engine, two weight profiles chosen by the role's market:

    india    Bengaluru / Hyderabad, on-site or hybrid. Fresher-eligibility, skills, ₹20 L+ pay
             evidence, company size/stage (smaller = fewer applicants), posting recency, a
             ready referrer, the city.
    remote   remote-anywhere / remote-from-India. The remote-anywhere language itself, IST
             overlap, async/written culture, skills, evidence they already hire across borders,
             stage, a ready referrer, competition (famous → lower).
    abroad   on-site outside India: scored on the remote profile and capped by the
             "on-site elsewhere" blocker — kept visible, never ranked with the two above.

Every term is a value 0..1 times a weight from config.yaml (`confidence.india.weights`,
`confidence.remote.weights`, each summing to 100), and every term says why — with the
quote and URL it was read from wherever there is one. Hard blockers (visa / citizenship /
clearance, remote restricted to another country, on-site elsewhere, 5+ years, a senior
title, an overrated company, stated pay under the floor) CAP the score and say so. The
band (High / Medium / Low / Long shot) is a threshold table in config.

Deterministic first: skills come from the shared skill dictionary and the free lexical
fit (fit.py); the judge rubric's match_score, where one exists, only refines the skills
term. Gap coverage comes from the needs brief (needs.py) — stored when there is one,
built deterministically on the fly when not. No model call happens here.

Layering: db / normalize / admission / guardrails / needs / network below; api above.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from sqlmodel import col, select

from jobhunter import CONFIG, admission
from jobhunter import normalize as norm
from jobhunter.db import Company, Contact, Job, get_session, init_db, utcnow

log = logging.getLogger(__name__)

_C = CONFIG.get("confidence") or {}

DEFAULT_WEIGHTS = {
    "india": {"fresher": 22, "skills": 16, "gap": 8, "pay": 12, "stage": 8, "recency": 7,
              "referral": 15, "city": 6, "hiring": 6},
    "remote": {"remote_terms": 14, "timezone": 8, "async": 4, "skills": 15, "gap": 8, "seniority": 13,
               "intl_hiring": 6, "stage": 6, "referral": 13, "competition": 5, "recency": 4, "hiring": 4},
}
DEFAULT_CAPS = {
    "visa": 10, "onsite_elsewhere": 20, "remote_country": 15, "india_other_city": 20, "years_5plus": 30,
    "years_3to4": 60, "senior_title": 35, "overrated": 30, "below_pay": 35, "language": 25,
    "not_authorized": 40, "stale_posting": 40, "low_stipend": 30, "inactive_company": 10,
    "off_target_title": 40, "gig": 45,
}
DEFAULT_BANDS = {"high": 65, "medium": 48, "low": 30}
BAND_LABEL = {"high": "High", "medium": "Medium", "low": "Low", "long_shot": "Long shot"}
MARKETS = ("india", "remote", "abroad")
# bump when the scoring logic (not just the config) changes, so every role is re-scored
ENGINE_VERSION = "selection-v2"


def weights(market: str) -> dict[str, float]:
    prof = "india" if market == "india" else "remote"
    w = ((_C.get(prof) or {}).get("weights") or DEFAULT_WEIGHTS[prof])
    return {k: float(v) for k, v in w.items()}


CAPS = {**DEFAULT_CAPS, **{k: int(v) for k, v in (_C.get("caps") or {}).items()}}
BANDS = {**DEFAULT_BANDS, **{k: int(v) for k, v in (_C.get("bands") or {}).items()}}
PAY_FLOOR_LPA = float(((CONFIG.get("targeting") or {}).get("india") or {}).get("min_lpa", 20))
STIPEND_MIN = int((CONFIG.get("targeting") or {}).get("stipend_min_inr_month", 50000))
_TICK = _C.get("tick") or {}

LABELS = {
    "fresher": "Fresher-eligible", "seniority": "Seniority fit", "skills": "Skills match", "gap": "Needs covered",
    "pay": "Pay ≥ ₹20 L", "stage": "Company size / stage", "recency": "Posting age", "referral": "Referrer ready",
    "city": "City", "hiring": "Hiring proven", "remote_terms": "Remote-anywhere terms", "timezone": "IST overlap",
    "async": "Async / written culture", "intl_hiring": "Hires across borders", "competition": "Competition",
}


def band(score: int | None) -> str | None:
    if score is None:
        return None
    if score >= BANDS["high"]:
        return "high"
    if score >= BANDS["medium"]:
        return "medium"
    if score >= BANDS["low"]:
        return "low"
    return "long_shot"


# ------------------------------------------------------------------ the result

@dataclass
class Term:
    key: str
    weight: float
    value: float
    why: str
    quote: str | None = None
    url: str | None = None

    @property
    def points(self) -> float:
        return round(self.weight * self.value, 1)

    def as_dict(self) -> dict:
        return {**asdict(self), "label": LABELS.get(self.key, self.key), "points": self.points}


@dataclass
class Blocker:
    kind: str
    cap: int
    why: str
    quote: str | None = None
    url: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Selection:
    market: str
    score: int
    raw: float
    band: str
    terms: list[Term] = field(default_factory=list)
    blockers: list[Blocker] = field(default_factory=list)
    cap: int | None = None
    city: str | None = None
    notes: list[str] = field(default_factory=list)

    def top_reasons(self, n: int = 3) -> list[str]:
        good = sorted((t for t in self.terms if t.value >= 0.7), key=lambda t: -t.points)
        return [f"{LABELS.get(t.key, t.key)}: {t.why}" for t in good[:n]]

    def main_blocker(self) -> str | None:
        if self.blockers:
            b = min(self.blockers, key=lambda b: b.cap)
            return f"{b.why} (capped at {b.cap})"
        weak = sorted((t for t in self.terms if t.value < 0.3 and t.weight >= 8), key=lambda t: -(t.weight * (1 - t.value)))
        return f"{LABELS.get(weak[0].key, weak[0].key)}: {weak[0].why}" if weak else None

    def as_dict(self) -> dict:
        return {"market": self.market, "score": self.score, "raw": self.raw, "band": self.band,
                "band_label": BAND_LABEL[self.band], "cap": self.cap, "city": self.city,
                "terms": [t.as_dict() for t in sorted(self.terms, key=lambda t: -t.weight)],
                "blockers": [b.as_dict() for b in sorted(self.blockers, key=lambda b: b.cap)],
                "top_reasons": self.top_reasons(), "main_blocker": self.main_blocker(), "notes": self.notes,
                "weights": weights(self.market), "bands": BANDS}


# ------------------------------------------------------------------ market

_REMOTE_LOC = re.compile(r"\b(remote|anywhere|worldwide|world ?wide|distributed|work from home|wfh|global)\b", re.I)
_OK_REGION = re.compile(r"\b(india|apac|asia|asia[- ]pacific|anywhere|worldwide|world ?wide|global(?:ly)?|any (?:country|location|time ?zone)|"
                        r"international|ist)\b", re.I)
_OTHER_REGION = re.compile(
    r"\b(us|u\.s\.?|usa|united states|america|americas|north america|canada|uk|u\.k\.|united kingdom|england|britain|europe|eu|emea|"
    r"latam|latin america|south america|germany|france|spain|portugal|netherlands|poland|ireland|australia|new zealand|brazil|"
    r"mexico|argentina|colombia|israel|singapore|japan|nigeria|kenya|south africa|philippines|switzerland|sweden|"
    r"[a-z ]+, (?:ca|ny|tx|wa|ma|co|il|ga|fl|nc|va|or|ut|az|pa|nj|mn|oh|mi|md|dc))\b", re.I)
_RESTRICT_DESC = re.compile(
    r"((?:must|should|need to|required to) (?:be )?(?:currently )?(?:based|located|reside|residing|live|living) "
    r"(?:in|within) (?:the )?([A-Za-z .]{2,30}?)(?=[,.;:)]|\s+(?:and|or|with|time|to)\b)|"
    r"\b(?:us|u\.s\.|usa|uk|eu|canada)[- ](?:based|only)\b|"
    r"(?:open|available|remote) (?:only )?(?:to|in|within) (?:candidates in )?(?:the )?(?:us|u\.s\.|usa|united states|uk|canada|europe|eu)(?: only)?\b)",
    re.I)
_VISA = re.compile(
    r"(must (?:be|have) (?:a )?(?:us|u\.s\.|uk|eu|canadian|australian) (?:citizen|national|citizenship|permanent resident)|"
    r"(?:us|u\.s\.) citizenship (?:is )?required|citizens? only|green card holders?|security clearance|clearance (?:is )?required|"
    r"\bts/sci\b|(?:must|need to|required to) (?:be|currently be) (?:legally )?(?:authori[sz]ed|eligible) to work in (?:the )?"
    r"(?:us|u\.s\.|usa|united states|uk|united kingdom|canada|eu|europe|germany|australia)|"
    r"work authori[sz]ation (?:in|for) (?:the )?(?:us|u\.s\.|united states|uk|canada))", re.I)
_NO_SPONSOR = re.compile(r"((?:not|unable to|cannot|can't|do not|don't|won't|will not) (?:be able to )?(?:offer|provide|support|sponsor)\w*"
                         r"[^.]{0,20}(?:visa|sponsorship)|no (?:visa )?sponsorship)", re.I)
_LANGUAGE = re.compile(r"(fluen(?:t|cy)|native|proficien(?:t|cy)|business[- ]level|c1|c2)[^.]{0,40}\b(german|french|japanese|spanish|"
                       r"dutch|portuguese|mandarin|chinese|korean|italian|hebrew|arabic|polish|swedish|turkish)\b", re.I)
_GIG = re.compile(r"\b(freelance|freelancer|task auditor|annotator|annotation|ai tutor|ai trainer|contract(?:or)? (?:role|position)|"
                  r"part[- ]time|gig)\b", re.I)
_ENG_TITLE = re.compile(r"\b(engineer(?:ing|s)?|developer|dev|sde|swe|programmer|architect|scientist|ml|machine learning|"
                        r"devops|sre|full[- ]?stack|back[- ]?end|front[- ]?end|data|technical|tech|intern(?:ship)?|software|"
                        r"research|platform|infrastructure|security|founding)\b", re.I)
_APPLICANTS = re.compile(r"(\d[\d,]*)\+?\s+(?:applicants|people (?:have )?applied)", re.I)


def _quote_at(text: str, m: re.Match, limit: int = 220) -> str:
    from jobhunter.needs import _sentence_around

    return _sentence_around(text, m.start(), m.end(), limit)


def market_of(job: Job, company: Company | None = None) -> tuple[str, dict]:
    """(india | remote | abroad, detail). The location field speaks first; the description
    and the company's HQ only when the location says nothing."""
    loc = (job.location or "").strip()
    desc = (job.description or "")[:4000]
    city = admission.india_city(loc)
    remote_loc = bool(_REMOTE_LOC.search(loc))
    detail: dict[str, Any] = {"location": loc or None}
    if city and not remote_loc:
        return "india", {**detail, "city": city}
    if remote_loc or (not loc and (job.remote or job.remote_anywhere)):
        return "remote", {**detail, "city": city}
    if job.remote_anywhere:
        # the description says remote-first, the location names one place: remote only
        # when the text really means any country (or names India / APAC)
        if _TRULY_ANYWHERE.search(desc) or _OK_REGION.search(loc):
            return "remote", {**detail, "city": city}
        if not city and not re.search(r"\bindia\b", loc, re.I):
            return "abroad", {**detail, "note": "remote-first wording, but the location is " + loc}
    if re.search(r"\bindia\b", loc, re.I):
        other = admission.india_other_city(loc)
        return "india", {**detail, "city": None, "other_city": other}
    other = admission.india_other_city(loc)
    if other and not _OTHER_REGION.search(loc):
        return "india", {**detail, "city": None, "other_city": other}
    if not loc:
        dcity = admission.india_city(desc)
        if dcity:
            return "india", {**detail, "city": dcity, "from": "description"}
        hq = (company.hq_region if company else None) or ""
        if hq == "india":
            return "india", {**detail, "city": company.city if company else None, "from": "company HQ"}
        if hq == "remote":
            return "remote", {**detail, "from": "company HQ"}
    return "abroad", detail


_REGION_ANYWHERE = re.compile(r"remote\s*[\(\-–—:,]?\s*\(?(emea|latam|europe|americas|us|u\.s\.|usa|uk|eu|canada)\)?", re.I)
_TRULY_ANYWHERE = re.compile(r"(anywhere in the world|work from anywhere|any country|worldwide|world[- ]wide|globally distributed|"
                             r"time ?zone[- ]agnostic|hire (?:from )?anywhere)", re.I)


def remote_restriction(job: Job) -> tuple[str, str] | None:
    """(why, quote) when a remote role is limited to a country / region India is not in.

    normalize's remote-anywhere flag counts "Remote (LATAM)" and "Remote (EMEA)" as
    anywhere; from India they are not, and neither is a location field that names only
    another country ("Argentina", "U.S. / Europe")."""
    loc = job.location or ""
    desc = (job.description or "")[:6000]
    if _OTHER_REGION.search(loc) and not _OK_REGION.search(re.sub(r"(?i)remote", "", loc)):
        if not re.search(r"\b(anywhere|worldwide|world ?wide|global)\b", loc, re.I):
            return f"remote is limited to {_OTHER_REGION.search(loc).group(0).strip()}", loc
    m = _REGION_ANYWHERE.search(loc) or _REGION_ANYWHERE.search(desc)
    if m and not _TRULY_ANYWHERE.search(f"{loc} {desc}") and not _OK_REGION.search(m.group(0)):
        src = loc if _REGION_ANYWHERE.search(loc) else desc
        return f"remote within {m.group(1).upper()} only", _quote_at(src, m) if src is desc else loc
    for m in _RESTRICT_DESC.finditer(desc):
        q = _quote_at(desc, m)
        if _OK_REGION.search(q):
            continue
        return "the posting limits where you may live", q
    return None


# ------------------------------------------------------------------ the terms

def _age_days(job: Job) -> tuple[int | None, str]:
    when, what = (job.posted_at, "posted") if job.posted_at else (job.scraped_at, "first seen")
    if not when:
        return None, what
    return max(0, (utcnow() - when).days), what


def term_recency(job: Job, w: float) -> Term:
    age, what = _age_days(job)
    if age is None:
        return Term("recency", w, 0.4, "posting date unknown")
    v = 1.0 if age <= 7 else 0.85 if age <= 14 else 0.6 if age <= 30 else 0.35 if age <= 60 else 0.15
    return Term("recency", w, v, f"{what} {age} day{'s' if age != 1 else ''} ago", url=job.url)


def term_seniority(job: Job, key: str, w: float, exp) -> Term:
    title = job.title or ""
    if norm.is_internship(title):
        return Term(key, w, 1.0, f"an internship ({title[:60]}) — the fresher door", url=job.url)
    if exp.fresher and (exp.min_years is None or exp.min_years <= 1):
        return Term(key, w, 1.0, "open to freshers / new grads", quote=exp.fresher_quote, url=job.url)
    if norm.is_senior(title):
        return Term(key, w, 0.1, f"senior title: “{title[:70]}”", quote=title, url=job.url)
    if exp.min_years is not None:
        y = exp.min_years
        v = 1.0 if y <= 1 else 0.8 if y <= 2 else 0.4 if y <= 3 else 0.2 if y <= 4 else 0.05
        return Term(key, w, v, f"asks {y:g}{'+' if not exp.max_years else '–' + format(exp.max_years, 'g')} years",
                    quote=exp.quote, url=job.url)
    if re.search(r"\b(junior|jr\.?|associate|entry|graduate|new grad|sde ?(?:1|i)\b|engineer i\b|level 1)\b", title, re.I):
        return Term(key, w, 0.9, f"junior-level title: “{title[:70]}”", quote=title, url=job.url)
    return Term(key, w, 0.6, "no experience bar stated, title not senior")


def term_skills(job: Job, w: float, brief: dict, lexical: float | None) -> Term:
    stated = brief.get("stated") or []
    tot = sum(n["weight"] for n in stated if n.get("skills"))
    got = sum(n["weight"] * (1 if n["status"] == "covered" else 0.5 if n["status"] == "partial" else 0)
              for n in stated if n.get("skills"))
    # a prior of two half-covered needs: "100% of the one skill named" is weak evidence
    det = (got + 1.0) / (tot + 2.0) if tot else None
    lex = min(1.0, (lexical or 0) / 0.12) if lexical is not None else None
    parts = []
    if tot:
        parts.append(f"{round(got / tot * 100)}% of the stated stack on the resume")
    if lex is not None:
        parts.append(f"lexical fit {lexical:.3f}")
    if det is None and lex is None:
        return Term("skills", w, 0.3, "no stack named in the posting and no lexical score yet")
    v = det if lex is None else lex if det is None else 0.65 * det + 0.35 * lex
    if job.match_score is not None:
        v = 0.6 * v + 0.4 * (job.match_score / 100)
        parts.append(f"rubric {job.match_score}")
    have = sorted({s for n in stated if n["status"] == "covered" for s in n.get("evidence_skills") or n.get("covered_skills") or []})
    miss = sorted({s for n in stated for s in n.get("missing_skills") or []})
    why = "; ".join(parts)
    if have:
        why += " — has " + ", ".join(have[:5])
    if miss:
        why += " — missing " + ", ".join(miss[:4])
    return Term("skills", w, round(max(0.0, min(1.0, v)), 3), why, url=job.url)


def term_gap(w: float, brief: dict) -> Term:
    cov = brief.get("coverage") or {}
    share = cov.get("share")
    if share is None:
        return Term("gap", w, 0.4, "no mappable needs read yet")
    src = "needs brief" if brief.get("stored", True) and brief.get("built_at") and brief.get("context", {}).get("at") else "the posting"
    return Term("gap", w, share, f"{cov.get('covered', 0)} covered, {cov.get('partial', 0)} partly, "
                                 f"{cov.get('uncovered', 0)} uncovered of {cov.get('total', 0)} needs (from {src})")


def term_referral(w: float, ref: dict) -> Term:
    if ref.get("ready_verified"):
        return Term("referral", w, 1.0, f"{ref['ready']} ready referrer(s); best: {ref.get('best')}", quote=ref.get("best_evidence"))
    if ref.get("ready"):
        return Term("referral", w, 0.8, f"{ref['ready']} ready referrer(s) with an address; best: {ref.get('best')}",
                    quote=ref.get("best_evidence"))
    if ref.get("people"):
        return Term("referral", w, 0.15, f"{ref['people']} people found, none confirmed current with a usable address")
    return Term("referral", w, 0.0, "nobody found at the company yet")


_OWN_BOARD = {"greenhouse", "lever", "ashby", "workable", "recruitee", "bamboohr", "breezy", "careers"}


def term_hiring(job: Job, company: Company | None, w: float) -> Term:
    hs = company.hiring_status if company else None
    if hs == "verified":
        return Term("hiring", w, 1.0, "hiring confirmed on their own careers page",
                    quote=(company.hiring_evidence or "")[:220] or None, url=company.careers_url)
    if job.source in _OWN_BOARD:
        return Term("hiring", w, 0.8, f"posted on their own board ({job.source})", url=job.url)
    if hs == "careers_only":
        return Term("hiring", w, 0.6, "their careers page lists roles", url=company.careers_url)
    if hs == "role_missing":
        return Term("hiring", w, 0.35, "careers page answers but this role is not on it", url=company.careers_url)
    if hs == "not_authorized":
        return Term("hiring", w, 0.05, "the hiring claim is not backed by their own site", url=company.careers_url)
    return Term("hiring", w, 0.4, f"not verified on their own site ({hs or 'unchecked'})")


def _stage_value(company: Company | None) -> tuple[float, str, str | None]:
    if company is None:
        return 0.5, "company unknown", None
    if company.team_size:
        n = company.team_size
        v = 1.0 if n <= 50 else 0.75 if n <= 200 else 0.45 if n <= 1000 else 0.2
        return v, f"team of ~{n}", company.team_size_evidence
    st = admission.normalize_stage(company.funding_stage)
    v = {"Pre-seed": 0.9, "Seed": 0.9, "Series A": 0.8, "Series B": 0.6, "Series C": 0.45, "Series D+": 0.25,
         "Public": 0.1, "Bootstrapped": 0.6}.get(st or "", 0.5)
    return v, (f"{st}" + (f" ${company.funding_amount_usd_m:g}M" if company.funding_amount_usd_m else "") if st else "stage unknown"), \
        (company.funding_evidence or "")[:220] or None


def term_stage(job: Job, company: Company | None, w: float) -> Term:
    v, why, q = _stage_value(company)
    m = _APPLICANTS.search(job.description or "")
    if m:
        n = int(m.group(1).replace(",", ""))
        if n >= 200:
            v *= 0.5
            why += f"; {n}+ applicants already"
    return Term("stage", w, round(v, 3), why + (" — smaller means fewer applicants" if v >= 0.75 else ""), quote=q)


def term_competition(job: Job, company: Company | None, w: float) -> Term:
    if company is None:
        return Term("competition", w, 0.5, "company unknown")
    if company.overrated:
        return Term("competition", w, 0.0, f"overrated: {company.overrated_reason or 'famous'}")
    v = 0.8 if company.underrated else 0.6
    why = "underrated, recently funded" if company.underrated else "not a famous name"
    if company.valuation_usd_m and company.valuation_usd_m >= 1000:
        v, why = 0.3, f"valued ${company.valuation_usd_m:g}M — a crowded inbox"
    if job.source in ("hn", "wwr", "remoteok", "wellfound", "linkedin-jobs"):
        v *= 0.85
        why += f"; posted on a public board ({job.source})"
    m = _APPLICANTS.search(job.description or "")
    if m and int(m.group(1).replace(",", "")) >= 200:
        v *= 0.5
        why += f"; {m.group(1)}+ applicants"
    return Term("competition", w, round(v, 3), why)


def term_pay_india(job: Job, company: Company | None, w: float) -> tuple[Term, Blocker | None]:
    floor = PAY_FLOOR_LPA
    stated = job.salary_max_lpa or job.salary_min_lpa
    if job.is_internship and job.stipend_inr_month:
        if job.ppo_lpa and job.ppo_lpa >= floor:
            return Term("pay", w, 0.95, f"PPO ₹{job.ppo_lpa:g} L stated", quote=job.salary_raw, url=job.url), None
        if job.stipend_inr_month < STIPEND_MIN:
            return (Term("pay", w, 0.0, f"stipend ₹{job.stipend_inr_month:,}/month", url=job.url),
                    Blocker("low_stipend", CAPS["low_stipend"], f"stipend ₹{job.stipend_inr_month:,}/month is under the ₹{STIPEND_MIN:,} bar",
                            quote=job.salary_raw, url=job.url))
        return Term("pay", w, 0.6, f"stipend ₹{job.stipend_inr_month:,}/month clears the bar", quote=job.salary_raw, url=job.url), None
    if stated and not job.is_internship:
        if stated >= floor:
            return Term("pay", w, 1.0, f"the posting states up to ₹{stated:g} L", quote=job.salary_raw, url=job.url), None
        if stated >= 3:   # below 3 is a misparse (india.implausible_below_lpa), not evidence
            return (Term("pay", w, 0.0, f"the posting states ₹{stated:g} L", quote=job.salary_raw, url=job.url),
                    Blocker("below_pay", CAPS["below_pay"], f"stated pay ₹{stated:g} L is under the ₹{floor:g} L floor",
                            quote=job.salary_raw, url=job.url))
    if company is not None and company.ppo_lpa and company.ppo_lpa >= floor:
        if (company.hq_region or "india") == "india":
            return Term("pay", w, 0.85, f"the company states ₹{company.ppo_lpa:g} L elsewhere", quote=company.ppo_evidence), None
        return Term("pay", w, 0.6, f"pays ₹{company.ppo_lpa:g} L for a {company.hq_region.upper()} role; the India band is not stated",
                    quote=company.ppo_evidence), None
    if company is not None and company.pay_power_band in ("pays", "deep_pockets"):
        return Term("pay", w, 0.6, f"pay power: {company.pay_power_why or company.pay_power_band}"), None
    return Term("pay", w, 0.35, "no pay figure stated"), None


def term_city(w: float, detail: dict) -> Term:
    if detail.get("city"):
        return Term("city", w, 1.0, f"{detail['city']}" + (f" (from the {detail['from']})" if detail.get("from") else ""))
    if detail.get("other_city"):
        return Term("city", w, 0.0, f"{detail['other_city']} — not Bengaluru or Hyderabad")
    return Term("city", w, 0.6, "India, city not stated")


_TZ_GOOD = re.compile(r"\b(ist\b|india(?:n)? (?:time|hours|standard)|apac|asia[- ]pacific|any time ?zone|time ?zone[- ]agnostic|"
                      r"all time ?zones|flexible (?:hours|time ?zone))", re.I)
_TZ_EU = re.compile(r"\b(cet|cest|gmt|bst|utc ?[+-]? ?[0-3]\b|emea|european (?:hours|time))", re.I)
_TZ_EAST = re.compile(r"\b(est|edt|eastern (?:time|standard)|us east)\b", re.I)
_TZ_US = re.compile(r"\b(pst|pdt|pacific (?:time|standard)|us (?:business )?hours|overlap with (?:the )?(?:us|u\.s\.|america)|"
                    r"cst|central time|mst|us time ?zones?)\b", re.I)


def term_timezone(job: Job, w: float) -> Term:
    text = f"{job.location or ''}\n{(job.description or '')[:6000]}"
    m = _TZ_GOOD.search(text)
    if m:
        return Term("timezone", w, 1.0, "IST / APAC / any time zone works", quote=_quote_at(text, m), url=job.url)
    found = []
    for rx, v, name in ((_TZ_US, 0.2, "US Pacific/Central hours"), (_TZ_EAST, 0.35, "US Eastern hours"), (_TZ_EU, 0.65, "European hours")):
        m = rx.search(text)
        if m:
            found.append((v, name, _quote_at(text, m)))
    if found:
        v, name, q = min(found)
        return Term("timezone", w, v, f"expects {name}", quote=q, url=job.url)
    return Term("timezone", w, 0.5, "no time-zone requirement stated")


_ASYNC = re.compile(r"\b(async(?:hronous)?(?:[- ]first)?|written communication|write (?:things )?down|documentation[- ]first|"
                    r"design docs?|rfcs?|remote[- ]first|fully[- ]remote|distributed (?:team|company)|no meetings?)\b", re.I)


def term_async(job: Job, w: float) -> Term:
    text = (job.description or "")[:8000]
    hits = list(_ASYNC.finditer(text))
    if not hits:
        return Term("async", w, 0.3, "no async / written-culture signal")
    kinds = sorted({h.group(1).lower() for h in hits})
    return Term("async", w, min(1.0, 0.4 + 0.3 * len(kinds)), "says " + ", ".join(kinds[:4]),
                quote=_quote_at(text, hits[0]), url=job.url)


_INTL = re.compile(r"\b(contractors?|eor\b|employer of record|deel|remote\.com|oyster|rippling global|"
                   r"hire (?:from )?(?:anywhere|globally|worldwide|internationally)|hiring (?:globally|worldwide|internationally)|"
                   r"(?:team|teammates|people|employees) (?:across|in|from) \d+\+? (?:countries|time ?zones)|"
                   r"across \d+\+? (?:countries|time ?zones)|international team)\b", re.I)


def term_intl(job: Job, company: Company | None, w: float, anywhere_roles: int) -> Term:
    for text, url in ((job.description or "")[:8000], job.url), ((company.description or "") if company else "", company.website if company else None):
        m = _INTL.search(text or "")
        if m:
            return Term("intl_hiring", w, 1.0, "they already hire across borders", quote=_quote_at(text, m), url=url)
    if anywhere_roles >= 2:
        return Term("intl_hiring", w, 0.75, f"{anywhere_roles} remote-anywhere roles open at the company")
    if anywhere_roles == 1:
        return Term("intl_hiring", w, 0.5, "this is their one remote-anywhere role")
    return Term("intl_hiring", w, 0.2, "no evidence of hiring outside their country")


def term_remote(job: Job, w: float, detail: dict, restricted) -> Term:
    text = f"{job.location or ''}\n{(job.description or '')[:6000]}"
    if restricted:
        return Term("remote_terms", w, 0.0, restricted[0], quote=restricted[1], url=job.url)
    m = norm._ANYWHERE.search(text)
    if job.remote_anywhere and m:
        return Term("remote_terms", w, 1.0, "the posting says it can be done from anywhere", quote=_quote_at(text, m), url=job.url)
    if detail.get("city") or re.search(r"remote[^.\n]{0,20}\b(india|apac|asia)\b|\b(india|apac)[^.\n]{0,10}remote", text, re.I):
        return Term("remote_terms", w, 1.0, "remote from India / APAC", quote=job.location, url=job.url)
    if job.remote_anywhere:
        return Term("remote_terms", w, 0.9, "marked remote-anywhere", quote=job.location, url=job.url)
    return Term("remote_terms", w, 0.5, "remote, no country stated either way", quote=job.location, url=job.url)


# ------------------------------------------------------------------ blockers

def blockers_for(job: Job, company: Company | None, market: str, detail: dict, exp, restricted) -> list[Blocker]:
    out: list[Blocker] = []
    desc = (job.description or "")[:8000]
    m = _VISA.search(desc)
    if m and market != "india":
        out.append(Blocker("visa", CAPS["visa"], "needs local work authorisation / citizenship / clearance", _quote_at(desc, m), job.url))
    if market == "abroad":
        m2 = _NO_SPONSOR.search(desc)
        where = job.location or "outside India"
        out.append(Blocker("onsite_elsewhere", CAPS["visa"] if m2 else CAPS["onsite_elsewhere"],
                           f"on-site in {where}" + (" and no visa sponsorship" if m2 else " — a visa move for a new grad"),
                           _quote_at(desc, m2) if m2 else job.location, job.url))
    if market == "remote" and restricted:
        out.append(Blocker("remote_country", CAPS["remote_country"], restricted[0], restricted[1], job.url))
    if market == "india" and detail.get("other_city") and not detail.get("city"):
        out.append(Blocker("india_other_city", CAPS["india_other_city"], f"India role in {detail['other_city']}, not Bengaluru / Hyderabad",
                           job.location, job.url))
    if exp.min_years is not None and not exp.fresher:
        if exp.min_years >= 5:
            out.append(Blocker("years_5plus", CAPS["years_5plus"], f"asks {exp.min_years:g}+ years", exp.quote, job.url))
        elif exp.min_years >= 3:
            out.append(Blocker("years_3to4", CAPS["years_3to4"], f"asks {exp.min_years:g}+ years", exp.quote, job.url))
    if norm.is_senior(job.title or "") and not norm.is_internship(job.title or ""):
        low_bar = exp.min_years is not None and exp.min_years <= 2
        out.append(Blocker("senior_title", 55 if low_bar else CAPS["senior_title"],
                           f"senior-level title" + (" (but asks ≤ 2 years)" if low_bar else ""), job.title, job.url))
    title = job.title or ""
    if _GIG.search(title):
        out.append(Blocker("gig", CAPS["gig"], "freelance / task work, not a role that converts", title, job.url))
    elif not _ENG_TITLE.search(title):
        out.append(Blocker("off_target_title", CAPS["off_target_title"], "not an engineering title", title, job.url))
    m = _LANGUAGE.search(desc)
    if m and not re.search(r"\benglish\b", m.group(0), re.I):
        out.append(Blocker("language", CAPS["language"], f"asks for {m.group(2).title()}", _quote_at(desc, m), job.url))
    if company is not None:
        if company.overrated and not company.list_key:
            out.append(Blocker("overrated", CAPS["overrated"], f"overrated company: {company.overrated_reason or 'famous'}"))
        if company.status in ("quarantined", "merged") or company.do_not_contact:
            out.append(Blocker("inactive_company", CAPS["inactive_company"],
                               f"company is {company.status or 'do-not-contact'}: {company.status_reason or company.dnc_reason or ''}".strip(": ")))
        elif company.status == "below_pay_bar":
            out.append(Blocker("below_pay", CAPS["below_pay"], company.status_reason or "company is under the India pay floor"))
        if company.hiring_status == "not_authorized":
            out.append(Blocker("not_authorized", CAPS["not_authorized"], "their own site does not back the hiring claim",
                               (company.hiring_evidence or "")[:200] or None, company.careers_url))
    age, what = _age_days(job)
    if age is not None and age > 120:
        out.append(Blocker("stale_posting", CAPS["stale_posting"], f"{what} {age} days ago — likely filled", url=job.url))
    return out


# ------------------------------------------------------------------ the engine

def compute(job: Job, company: Company | None, *, ref: dict | None = None, brief: dict | None = None,
            anywhere_roles: int = 0) -> Selection:
    """The selection score for one role. Pure: everything it needs is passed in."""
    from jobhunter import needs as needs_mod

    market, detail = market_of(job, company)
    w = weights(market)
    exp = needs_mod.parse_experience(job.description)
    if brief is None:
        brief = needs_mod.build_brief(job, company, context=None, synth=False)
    ref = ref or {}
    restricted = remote_restriction(job) if market == "remote" else None
    terms: list[Term] = []
    blockers = blockers_for(job, company, market, detail, exp, restricted)
    for key, wt in w.items():
        if key in ("fresher", "seniority"):
            terms.append(term_seniority(job, key, wt, exp))
        elif key == "skills":
            terms.append(term_skills(job, wt, brief, job.embed_sim))
        elif key == "gap":
            terms.append(term_gap(wt, brief))
        elif key == "pay":
            t, b = term_pay_india(job, company, wt)
            terms.append(t)
            if b:
                blockers.append(b)
        elif key == "stage":
            terms.append(term_stage(job, company, wt))
        elif key == "recency":
            terms.append(term_recency(job, wt))
        elif key == "referral":
            terms.append(term_referral(wt, ref))
        elif key == "city":
            terms.append(term_city(wt, detail))
        elif key == "hiring":
            terms.append(term_hiring(job, company, wt))
        elif key == "remote_terms":
            terms.append(term_remote(job, wt, detail, restricted))
        elif key == "timezone":
            terms.append(term_timezone(job, wt))
        elif key == "async":
            terms.append(term_async(job, wt))
        elif key == "intl_hiring":
            terms.append(term_intl(job, company, wt, anywhere_roles))
        elif key == "competition":
            terms.append(term_competition(job, company, wt))
    total_w = sum(w.values()) or 100.0
    raw = round(sum(t.weight * t.value for t in terms) * 100.0 / total_w, 1)
    cap = min((b.cap for b in blockers), default=None)
    score = int(round(min(raw, cap) if cap is not None else raw))
    sel = Selection(market=market, score=score, raw=raw, band=band(score), terms=terms, blockers=blockers,
                    cap=cap, city=detail.get("city"))
    if job.match_score is None:
        sel.notes.append("no judge rubric yet — skills from the dictionary and lexical fit only")
    return sel


# ------------------------------------------------------------------ batch: referrers, inputs, recompute

def referral_index(session) -> dict[int, dict]:
    """Per company: how many leads we could actually write to (network._reachable)."""
    from jobhunter import network

    people: dict[int, list[Contact]] = {}
    for c in session.exec(select(Contact).where(col(Contact.email).is_not(None))).all():
        people.setdefault(c.company_id, []).append(c)
    companies = {c.id: c for c in session.exec(select(Company).where(col(Company.id).in_(list(people)))).all()} if people else {}
    out: dict[int, dict] = {}
    for cid, ps in people.items():
        comp = companies.get(cid)
        if comp is None:
            continue
        ps = [p for p in ps if not getattr(p, "do_not_contact", None)]
        try:
            ready = network._reachable(comp, ps)
        except Exception:  # noqa: BLE001 — a malformed row must not stop the batch
            ready = []
        ready.sort(key=lambda p: ((p.referral_rank or 9), p.affiliation != "verified-current", p.confidence != "verified"))
        best = ready[0] if ready else None
        out[cid] = {
            "people": len(ps), "ready": len(ready),
            "ready_verified": sum(1 for p in ready if p.affiliation == "verified-current" and p.confidence == "verified"),
            "best": f"{best.name or best.handle or best.email} ({best.role_class or best.role or 'role unknown'}, {best.confidence})" if best else None,
            "best_evidence": (best.affiliation_evidence or "")[:200] if best else None,
            "best_id": best.id if best else None,
        }
    return out


def inputs_hash(job: Job, company: Company | None, ref: dict, anywhere: int) -> str:
    age, _ = _age_days(job)
    parts = [
        job.title, job.location, len(job.description or ""), job.remote_anywhere, job.remote, job.salary_min_lpa,
        job.salary_max_lpa, job.stipend_inr_month, job.ppo_lpa, job.is_internship, job.match_score,
        round(job.embed_sim or 0, 3), job.needs_brief_at, (age or 0) // 7 if age is not None and age <= 60 else (age or 0) // 30,
        anywhere, ref.get("ready"), ref.get("ready_verified"), ref.get("people"),
    ]
    if company is not None:
        parts += [company.hiring_status, company.overrated, company.status, company.team_size, company.funding_stage,
                  company.funding_amount_usd_m, company.ppo_lpa, company.pay_power_band, company.valuation_usd_m,
                  company.underrated, company.hq_region, company.city, getattr(company, "do_not_contact", None),
                  company.needs_context_at, len(company.description or "")]
    from jobhunter.needs import BRIEF_VERSION

    parts.append(json.dumps({"w": {m: weights(m) for m in ("india", "remote")}, "c": CAPS, "b": BANDS,
                             "v": ENGINE_VERSION, "n": BRIEF_VERSION}, sort_keys=True))
    return hashlib.sha1(repr(parts).encode()).hexdigest()[:16]


def _stored_brief(job: Job) -> dict | None:
    if not job.needs_brief:
        return None
    try:
        return json.loads(job.needs_brief)
    except json.JSONDecodeError:
        return None


def recompute(limit: int | None = None, *, force: bool = False, job_ids: list[int] | None = None,
              budget_s: float | None = None) -> dict:
    """Score every role whose inputs changed (all of them with `force`), then roll up the
    best role per company. Commits in batches; a kill loses only the current batch."""
    init_db()
    t0 = time.time()
    out = {"seen": 0, "changed": 0, "scored": 0, "companies": 0, "by_market": {}, "by_band": {}}
    with get_session() as session:
        companies = {c.id: c for c in session.exec(select(Company)).all()}
        refs = referral_index(session)
        anywhere: dict[int, int] = {}
        for cid, ra in session.exec(select(Job.company_id, Job.remote_anywhere)).all():
            if cid and ra:
                anywhere[cid] = anywhere.get(cid, 0) + 1
        q = select(Job)
        if job_ids:
            q = q.where(col(Job.id).in_(job_ids))
        rows = session.exec(q).all()
        dirty = 0
        for job in rows:
            out["seen"] += 1
            comp = companies.get(job.company_id) if job.company_id else None
            ref = refs.get(job.company_id or -1, {})
            any_n = anywhere.get(job.company_id or -1, 0)
            h = inputs_hash(job, comp, ref, any_n)
            if not force and job.selection_inputs == h and job.selection_score is not None:
                continue
            out["changed"] += 1
            if limit is not None and out["scored"] >= limit:
                continue
            if budget_s is not None and time.time() - t0 > budget_s:
                continue
            sel = compute(job, comp, ref=ref, brief=_stored_brief(job), anywhere_roles=any_n)
            job.selection_score, job.selection_band, job.selection_market = sel.score, sel.band, sel.market
            job.selection_why = json.dumps(sel.as_dict(), default=str)[:30000]
            job.selection_inputs, job.selection_at = h, utcnow()
            session.add(job)
            out["scored"] += 1
            dirty += 1
            if dirty >= 300:
                session.commit()
                dirty = 0
        session.commit()
        out["companies"] = rollup(session)
        for job in session.exec(select(Job.selection_market, Job.selection_band)).all():
            m, b = job
            out["by_market"][m or "-"] = out["by_market"].get(m or "-", 0) + 1
            out["by_band"][b or "-"] = out["by_band"].get(b or "-", 0) + 1
    out["seconds"] = round(time.time() - t0, 1)
    log.info("selection: %s", out)
    return out


def rollup(session) -> int:
    """Each company's best India/Remote role (abroad roles only when it has nothing else)."""
    best: dict[int, tuple] = {}
    for jid, cid, score, bnd, market in session.exec(
            select(Job.id, Job.company_id, Job.selection_score, Job.selection_band, Job.selection_market)
            .where(col(Job.selection_score).is_not(None), col(Job.company_id).is_not(None))).all():
        key = (0 if market in ("india", "remote") else 1, -(score or 0))
        if cid not in best or key < best[cid][0]:
            best[cid] = (key, jid, score, bnd, market)
    changed = 0
    for c in session.exec(select(Company)).all():
        b = best.get(c.id)
        new = (b[2], b[3], b[1], b[4]) if b else (None, None, None, None)
        if (c.best_selection_score, c.best_selection_band, c.best_selection_job_id, c.best_selection_market) != new:
            c.best_selection_score, c.best_selection_band, c.best_selection_job_id, c.best_selection_market = new
            session.add(c)
            changed += 1
    session.commit()
    return changed


# ------------------------------------------------------------------ briefs for the best bets

def build_briefs(*, market: str | None = None, top: int = 50, synth: int = 0, web: int = 0,
                 refresh: bool = False, budget_s: float | None = None) -> dict:
    """Stored needs briefs for the top roles by selection score.

    `synth` = how many model syntheses may be attempted (the per-tick / per-run model
    budget); `web` = how many companies may have public context fetched. Both stop early on
    the model's daily ceiling / a dead search channel, and the brief is stored either way —
    deterministic, with synthesis marked pending."""
    from jobhunter import needs as needs_mod

    init_db()
    t0 = time.time()
    out = {"briefs": 0, "synthesised": 0, "pending": 0, "rejected": 0, "deterministic": 0, "context_fetched": 0,
           "skipped_fresh": 0, "ids": []}
    markets = [market] if market else ["india", "remote"]
    with get_session() as session:
        ids: list[int] = []
        for m in markets:
            ids += [j.id for j in session.exec(
                select(Job).where(Job.selection_market == m, col(Job.selection_score).is_not(None))
                .order_by(col(Job.selection_score).desc(), col(Job.id)).limit(top)).all()]
        profile = None
        for jid in ids:
            if budget_s is not None and time.time() - t0 > budget_s:
                break
            job = session.get(Job, jid)
            comp = session.get(Company, job.company_id) if job.company_id else None
            existing = _stored_brief(job)
            fresh = existing and job.needs_brief_at and (utcnow() - job.needs_brief_at).days < needs_mod.BRIEF_TTL_DAYS
            want_synth = synth > 0 and (not existing or existing.get("synthesis") in ("none", "pending"))
            if fresh and not refresh and not want_synth:
                out["skipped_fresh"] += 1
                continue
            ctx = None
            if comp is not None:
                allow_net = web > 0 and (comp.needs_context_at is None or refresh)
                ctx = needs_mod.context_for(session, comp, network=allow_net)
                if allow_net:
                    web -= 1
                    out["context_fetched"] += 1
            brief = needs_mod.build_brief(job, comp, context=ctx, synth=want_synth, profile=profile)
            st = brief.get("synthesis")
            if want_synth:
                synth -= 1
                if st == "done":
                    out["synthesised"] += 1
                elif st == "pending":
                    out["pending"] += 1
                    synth = 0          # the ceiling (or a dead chain): every later call would fail the same way
                elif st == "rejected":
                    out["rejected"] += 1
                else:
                    out["deterministic"] += 1
            else:
                if existing and existing.get("synthesis") == "done" and existing.get("model"):
                    # keep a good synthesis when only the deterministic part is refreshed
                    brief["synthesis"], brief["model"] = "done", existing["model"]
                    if existing.get("helps_model"):
                        brief["helps_model"] = existing["helps_model"]
                else:
                    brief["synthesis"] = "pending" if st == "none" else st
                out["deterministic"] += 1
            needs_mod.store_brief(session, job, brief)
            session.commit()
            out["briefs"] += 1
            out["ids"].append(jid)
    if out["ids"]:
        recompute(job_ids=out["ids"], force=True)
    out["seconds"] = round(time.time() - t0, 1)
    out["ids"] = out["ids"][:20]
    return out


def tick() -> dict:
    """The tick's selection step: re-score changed roles, then a few briefs within budget."""
    res = {"recompute": recompute(limit=int(_TICK.get("recompute_per_tick", 3000)),
                                  budget_s=float(_TICK.get("recompute_seconds", 240)))}
    n = int(_TICK.get("briefs_per_tick", 4))
    if n:
        res["briefs"] = build_briefs(top=int(_TICK.get("brief_top", 50)), synth=int(_TICK.get("synth_per_tick", 2)),
                                     web=int(_TICK.get("context_per_tick", 2)),
                                     budget_s=float(_TICK.get("brief_seconds", 120)))
        res["briefs"].pop("ids", None)
    rc = res["recompute"]
    res["recompute"] = {k: rc[k] for k in ("seen", "changed", "scored", "companies", "seconds")}
    return res


# ------------------------------------------------------------------ reading

def role_row(job: Job, company: Company | None = None) -> dict:
    why = {}
    if job.selection_why:
        try:
            why = json.loads(job.selection_why)
        except json.JSONDecodeError:
            why = {}
    return {
        "id": job.id, "title": job.title, "company_id": job.company_id,
        "company_name": company.name if company else job.company_name,
        "location": job.location, "url": job.url, "source": job.source,
        "posted_at": job.posted_at.isoformat() if job.posted_at else None,
        "scraped_at": job.scraped_at.isoformat() if job.scraped_at else None,
        "salary_min_lpa": job.salary_min_lpa, "salary_max_lpa": job.salary_max_lpa, "salary_raw": job.salary_raw,
        "market": job.selection_market, "score": job.selection_score, "band": job.selection_band,
        "band_label": BAND_LABEL.get(job.selection_band or "", None), "city": why.get("city"),
        "top_reasons": why.get("top_reasons") or [], "main_blocker": why.get("main_blocker"),
        "cap": why.get("cap"), "has_brief": bool(job.needs_brief), "synthesis": job.needs_synthesis,
        "match_score": job.match_score, "remote_anywhere": job.remote_anywhere,
    }


def distribution() -> dict:
    with get_session() as session:
        rows = session.exec(select(Job.selection_market, Job.selection_band, Job.selection_score)
                            .where(col(Job.selection_score).is_not(None))).all()
    out: dict[str, dict] = {}
    for m, b, s in rows:
        d = out.setdefault(m or "-", {"n": 0, "bands": {}, "scores": []})
        d["n"] += 1
        d["bands"][b] = d["bands"].get(b, 0) + 1
        d["scores"].append(s)
    for d in out.values():
        sc = sorted(d.pop("scores"))
        d["p50"] = sc[len(sc) // 2]
        d["p90"] = sc[int(len(sc) * 0.9)]
        d["max"] = sc[-1]
    return out


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


__all__ = ["compute", "market_of", "remote_restriction", "recompute", "rollup", "build_briefs", "tick", "band",
           "weights", "role_row", "distribution", "Selection", "Term", "Blocker", "BANDS", "CAPS", "MARKETS"]
