"""Lexical fit — the free, model-free gate in front of the paid rubric.

FINAL-PLAN-V3 §1 replaces the embedding prefilter with "rare-term-weighted
vocabulary overlap, resume vs JD". This is that, and it exists because OpenRouter
serves no embedding models: the wide end of the funnel now costs nothing at all.

The idea is one line: **a term is worth what it is rare.** "engineer" appears in
almost every posting and separates nothing; "pytorch", "langgraph", "rag" appear
in a handful and separate a lot. So each JD term is weighted by its inverse
document frequency over the postings already in the database, and the score is the
share of the JD's *weight* that the resume can actually answer for.

    score = Σ idf(t) for t in JD ∩ resume  /  Σ idf(t) for t in JD

0.0 means the resume matches nothing that makes this posting distinctive; 1.0
means it covers every distinctive thing the posting asks for. It is a cheap,
explainable filter, not a judgement — the judgement is the `judge` rubric on
whatever survives.
"""

from __future__ import annotations

import json
import logging
import math
import re
from collections import Counter

from sqlmodel import col, select

from jobhunter import CONFIG
from jobhunter import resume as resume_mod
from jobhunter.db import Job, get_session, get_setting, set_setting

log = logging.getLogger(__name__)

_IDF_KEY = "lexical_idf"
_IDF_DOCS_KEY = "lexical_idf_docs"
MIN_DOCS_FOR_IDF = 30      # below this the corpus says nothing about rarity

# Words that appear in every posting and separate nothing. Kept short on purpose:
# the IDF weighting already flattens common terms, this list only removes the ones
# that would otherwise waste a slot in a short JD.
STOPWORDS = frozenset("""
a about above after again against all am an and any are as at be because been before being below
between both but by can cannot could did do does doing down during each few for from further had
has have having he her here hers him his how i if in into is it its itself just me more most my no
nor not of off on once only or other our ours out over own same she should so some such than that
the their them then there these they this those through to too under until up very was we were what
when where which while who whom why will with would you your yours
role roles job jobs work working works team teams company companies position positions opportunity
we you your our us they will can must should would like new great good strong excellent looking
join build building help helping across within using use used based including etc using
experience years year skills skill ability able knowledge understanding strong plus bonus nice
required requirement requirements responsibilities qualifications benefits apply application
candidate candidates ideal successful passionate exciting dynamic fast paced environment culture
remote hybrid onsite office full time part contract permanent salary compensation equity
""".split())

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9+#.\-]{1,29}")
# tech terms whose punctuation carries meaning — never split these apart
_KEEP = {"c++", "c#", ".net", "node.js", "next.js", "ci/cd", "f#", "objective-c"}


def tokenize(text: str) -> set[str]:
    """Distinct meaningful terms in a blob of text.

    A set, not a bag: a JD repeating "Python" nine times is not nine times more
    about Python, and term frequency is exactly the signal a keyword-stuffed
    posting can manipulate.
    """
    if not text:
        return set()
    lowered = text.lower()
    out: set[str] = set()
    for raw in _TOKEN_RE.findall(lowered):
        term = raw.strip(".-")
        if not term or term in STOPWORDS:
            continue
        if len(term) < 2 or (term.isdigit() and len(term) < 4):
            continue
        out.add(term)
    for kept in _KEEP:
        if kept in lowered:
            out.add(kept)
    return out


# ---------------------------------------------------------------- idf corpus


def build_idf(*, force: bool = False, min_docs: int = MIN_DOCS_FOR_IDF) -> dict[str, float]:
    """Document frequencies over every posting in the database, cached in Setting.

    Rebuilt when the corpus has grown by more than 20% — rarity drifts as new
    postings arrive, but not fast enough to justify recomputing it every run.
    """
    with get_session() as session:
        total = len(session.exec(select(Job.id)).all())
        if not force:
            cached = get_setting(session, _IDF_KEY)
            prev = int(get_setting(session, _IDF_DOCS_KEY, "0") or 0)
            if cached and prev and total < prev * 1.2:
                try:
                    return json.loads(cached)
                except json.JSONDecodeError:
                    pass

        if total < min_docs:
            log.info("only %d jobs in the corpus — using unweighted overlap until there are %d", total, min_docs)
            return {}

        df: Counter[str] = Counter()
        rows = session.exec(select(Job.title, Job.company_name, Job.description)).all()
        for title, company, description in rows:
            df.update(tokenize(_job_blob(title, company, description)))

        # A term in nearly every posting is noise; a term in one is probably a typo
        # or a company name. Both ends are dropped rather than weighted.
        idf = {
            term: round(math.log(total / count), 4)
            for term, count in df.items()
            if 1 < count < total * 0.6
        }
        set_setting(session, _IDF_KEY, json.dumps(idf))
        set_setting(session, _IDF_DOCS_KEY, str(total))
        log.info("lexical idf: %d terms over %d postings", len(idf), total)
        return idf


def _job_blob(title: str | None, company: str | None, description: str | None) -> str:
    return "\n".join(x for x in (title, company, (description or "")[:6000]) if x)


_idf_cache: dict[str, float] | None = None


def idf() -> dict[str, float]:
    global _idf_cache
    if _idf_cache is None:
        _idf_cache = build_idf()
    return _idf_cache


def refresh() -> int:
    """Force an IDF rebuild — call after a big scrape or a resume change."""
    global _idf_cache
    _idf_cache = build_idf(force=True)
    return len(_idf_cache)


# ---------------------------------------------------------------- scoring


def score(jd_text: str, resume_terms: set[str], weights: dict[str, float] | None = None) -> float:
    """Share of the JD's distinctive weight that the resume can answer for, 0..1."""
    jd_terms = tokenize(jd_text)
    if not jd_terms or not resume_terms:
        return 0.0
    weights = idf() if weights is None else weights

    if not weights:
        # cold corpus: unweighted overlap, honest but blunt
        return round(len(jd_terms & resume_terms) / len(jd_terms), 4)

    total = matched = 0.0
    for term in jd_terms:
        w = weights.get(term)
        if w is None:
            continue           # not distinctive in this corpus, or unseen
        total += w
        if term in resume_terms:
            matched += w
    if total <= 0:
        return round(len(jd_terms & resume_terms) / len(jd_terms), 4)
    return round(matched / total, 4)


def explain(jd_text: str, resume_terms: set[str] | None = None, top: int = 12) -> dict:
    """Same score, plus which terms carried it and which were missed.

    The rubric pass is a paid opinion; this is a free one that can be checked by
    eye, so it is worth being able to see why a posting was let through or dropped.
    """
    resume_terms = resume_profile_terms() if resume_terms is None else resume_terms
    jd_terms = tokenize(jd_text)
    weights = idf()
    hits = sorted(
        ((t, weights.get(t, 0.0)) for t in jd_terms & resume_terms if t in weights),
        key=lambda x: x[1],
        reverse=True,
    )
    misses = sorted(
        ((t, weights.get(t, 0.0)) for t in jd_terms - resume_terms if t in weights),
        key=lambda x: x[1],
        reverse=True,
    )
    return {
        "score": score(jd_text, resume_terms, weights),
        "matched": [{"term": t, "weight": w} for t, w in hits[:top]],
        "missing": [{"term": t, "weight": w} for t, w in misses[:top]],
        "jd_terms": len(jd_terms),
        "corpus_terms": len(weights),
    }


_resume_terms: set[str] | None = None


def resume_profile_terms(*, force: bool = False) -> set[str]:
    """The resume as a term set, computed once per process."""
    global _resume_terms
    if _resume_terms is None or force:
        _resume_terms = tokenize(resume_mod.embedding_text())
        log.debug("resume vocabulary: %d terms", len(_resume_terms))
    return _resume_terms


def score_job(job: Job, resume_terms: set[str] | None = None) -> float:
    return score(
        _job_blob(job.title, job.company_name, job.description),
        resume_profile_terms() if resume_terms is None else resume_terms,
    )


def stats() -> dict:
    weights = idf()
    with get_session() as session:
        scored = len(session.exec(select(Job.id).where(col(Job.embed_sim).is_not(None))).all())
        total = len(session.exec(select(Job.id)).all())
    return {
        "corpus_terms": len(weights),
        "resume_terms": len(resume_profile_terms()),
        "jobs_scored": scored,
        "jobs_total": total,
        "cost": "zero — no model, no network",
    }
