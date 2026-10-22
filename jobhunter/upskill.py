"""Upskill — what is missing from the profile, ranked by what learning it is worth (2026-10-23).

Tejmul: "create a pipeline [of] what's missing from your profile [that I] need to improve …
you will already have information about companies and you can add the missing things in my
resume so that I can upskill and learn."

THE GAPS — read from the companies you are actually targeting, not the market at large
    Every shortlisted company (decide.py: Bengaluru, Hyderabad, Remote) contributes its best
    roles' needs briefs (needs.py — stored, or built deterministically on the fly). A need
    names skills; a skill is
        learn   missing from the resume altogether (the need's missing_skills)
        prove   on the resume only as a word in the skills list — no shipped bullet or project
                behind it, so the brief counts it half (status partial)

WHAT IT IS WORTH — the brain's own arithmetic, not a guess
    For each company that asks for a skill, the skills term of its best role is recomputed as
    if the skill were covered by a shipped project (the same (got+1)/(tot+2) the role score
    uses, blended as confidence.term_skills blends it), and the change goes through the brain's
    formulas: the screen's logit moves by screen.skills × Δ, the loop's by loop.fit × Δ, and
    P(offer) by the ratio of the new call and loop to the old. Summed over the shortlist, that
    is "expected offers gained" — the ranking. It is an UPPER estimate (the skill counted as fully
    covered on each company's best role): adding Observability on a copy of the DB moved the
    shortlist's expected offers by +0.19 against +0.32 estimated. DSA is the one row computed differently: the
    loop is recomputed at a higher `me.dsa_level`, and the parked companies that would return
    are counted.

ADDING IT TO THE RESUME — only with proof (guarantee:no-fabrication)
    A skill is marked learning / done; "done" needs a link and a line in your words. Only
    then can it be added to profile/profile.json, as a project naming the skill, with a dated
    backup of the profile first. The resume PDF is yours to update; the line is given ready to
    paste. After an addition the role scores and the brain are re-run, so the gain shows up.

Layering: db / needs / confidence / decide below; api / CLI above. No network, no model.
"""

from __future__ import annotations

import json
import math
import shutil
import time
from datetime import datetime

from sqlmodel import col, select

from jobhunter import ROOT, decide
from jobhunter.db import Company, CompanyDecision, Job, UpskillItem, get_session, init_db, utcnow

PROFILE = ROOT / "profile" / "profile.json"
ROLES_PER_COMPANY = 3
_CACHE: dict = {"at": 0.0, "key": None, "data": None}
CACHE_S = 600


def _sig(x: float) -> float:
    return 1 / (1 + math.exp(-x))


def _logit(p: float) -> float:
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))


def _brief(job: Job, company: Company | None) -> dict:
    from jobhunter import needs

    if job.needs_brief:
        try:
            return json.loads(job.needs_brief)
        except json.JSONDecodeError:
            pass
    return needs.build_brief(job, company, context=None, synth=False)


def _det(stated: list[dict], learned: str | None = None) -> float | None:
    """The deterministic part of confidence.term_skills, with `learned` counted as shipped."""
    tot = got = 0.0
    for n in stated:
        if not n.get("skills"):
            continue
        tot += n["weight"]
        missing = [s for s in n.get("missing_skills") or [] if s != learned]
        status = n["status"]
        if learned and learned in (n.get("skills") or []):
            status = "covered" if not missing else "partial"
        got += n["weight"] * (1 if status == "covered" else 0.5 if status == "partial" else 0)
    return (got + 1.0) / (tot + 2.0) if tot else None


def _offer_after(d: CompanyDecision, dv: float) -> float:
    """P(offer) if the best role's skills term rose by dv — through the brain's own formulas."""
    s0 = d.p_screen
    s1 = _sig(_logit(s0) + decide.SCREEN["skills"] * dv)
    orr = math.log(decide.REFERRAL_OR)
    call = lambda s: d.p_reach * _sig(_logit(s) + orr) + (1 - d.p_reach) * s  # noqa: E731
    l1 = _sig(_logit(d.p_loop) + decide.LOOP["fit"] * dv)
    return d.p_offer * (call(s1) / max(call(s0), 1e-9)) * (l1 / max(d.p_loop, 1e-9))


def analyse(refresh: bool = False) -> dict:
    init_db()
    with get_session() as session:
        decs = session.exec(select(CompanyDecision).where(col(CompanyDecision.rank).is_not(None))).all()
        key = (len(decs), max((d.at for d in decs), default=None))
        if not refresh and _CACHE["data"] and _CACHE["key"] == key and time.time() - _CACHE["at"] < CACHE_S:
            return _CACHE["data"]
        comps = {c.id: c for c in session.exec(select(Company).where(col(Company.id).in_([d.company_id for d in decs] or [-1]))).all()}
        jobs: dict[int, list[Job]] = {}
        for j in session.exec(select(Job).where(col(Job.company_id).in_(list(comps) or [-1]),
                                                col(Job.selection_market).in_(["india", "remote"]))
                              .order_by(col(Job.selection_score).desc())).all():
            if len(jobs.setdefault(j.company_id, [])) < ROLES_PER_COMPANY:
                jobs[j.company_id].append(j)
        items = {i.skill: i for i in session.exec(select(UpskillItem)).all()}

        skills: dict[str, dict] = {}
        for d in decs:
            c = comps.get(d.company_id)
            roles = jobs.get(d.company_id) or []
            if c is None or not roles:
                continue
            briefs = [(j, _brief(j, c)) for j in roles]
            best_job, best = briefs[0]
            stated = [n for n in best.get("stated") or [] if n.get("skills")]
            try:
                v0 = next(t["value"] for t in json.loads(best_job.selection_why or "{}").get("terms", []) if t["key"] == "skills")
            except StopIteration:
                v0 = None
            base = _det(stated)
            blend = (0.65 if best_job.embed_sim is not None else 1.0) * (0.6 if best_job.match_score is not None else 1.0)
            seen_here: set[str] = set()
            for j, b in briefs:
                for n in b.get("stated") or []:
                    for kind, names in (("learn", n.get("missing_skills") or []),
                                        ("prove", n.get("covered_skills") or [] if n.get("status") == "partial" and not n.get("missing_skills") else [])):
                        for sk in names:
                            e = skills.setdefault(sk, {"skill": sk, "kind": kind, "companies": {}, "quotes": [], "required": 0,
                                                       "gain": 0.0, "pools": {}})
                            if kind == "learn":
                                e["kind"] = "learn"          # missing anywhere outranks "only in the skills list"
                            if n.get("section") == "required":
                                e["required"] += 1
                            if len(e["quotes"]) < 4 and c.id not in {q["company_id"] for q in e["quotes"]}:
                                e["quotes"].append({"company_id": c.id, "company": c.name, "role": j.title, "quote": n.get("quote"),
                                                    "url": n.get("url"), "section": n.get("section")})
                            if sk in seen_here:
                                continue
                            seen_here.add(sk)
                            gain = 0.0
                            if base is not None and v0 is not None and j is best_job:
                                after = _det(stated, sk)
                                dv = min(1.0 - v0, max(0.0, (after - base) * blend))
                                gain = max(0.0, _offer_after(d, dv) - d.p_offer)
                            e["gain"] += gain
                            e["companies"][c.id] = {"company_id": c.id, "name": c.name, "pool": d.pool, "rank": d.rank,
                                                    "p_offer": d.p_offer, "gain": round(gain, 5)}
                            e["pools"][d.pool] = e["pools"].get(d.pool, 0) + 1

        out = []
        n_cos = len({d.company_id for d in decs if jobs.get(d.company_id)})
        for e in skills.values():
            cos = sorted(e["companies"].values(), key=lambda x: (-x["gain"], -x["p_offer"]))
            it = items.get(e["skill"])
            out.append({"skill": e["skill"], "kind": e["kind"], "companies_n": len(cos), "share": round(len(cos) / max(n_cos, 1), 3),
                        "required_mentions": e["required"], "expected_offers_gained": round(e["gain"], 4), "pools": e["pools"],
                        "companies": cos[:12], "quotes": e["quotes"],
                        "status": it.status if it else "todo", "proof_url": it.proof_url if it else None,
                        "proof_note": it.proof_note if it else None,
                        "in_profile": bool(it and it.added_to_profile_at)})
        out.sort(key=lambda x: (-x["expected_offers_gained"], -x["companies_n"]))
        data = {"skills": out, "dsa": dsa_outlook(session), "companies_read": n_cos,
                "expected_offers_now": round(sum(d.p_offer for d in decs), 3), "generated_at": utcnow().isoformat()}
    _CACHE.update(at=time.time(), key=key, data=data)
    return data


def dsa_outlook(session, levels: tuple[float, ...] = (0.5, 0.65, 0.8)) -> dict:
    """What raising your DSA level is worth: the shortlist's loop answers at each level, and the
    India companies parked for DSA that would come back."""
    decs = session.exec(select(CompanyDecision).where(
        col(CompanyDecision.rank).is_not(None) | (CompanyDecision.verdict == "after_dsa"))).all()
    now = decide.DSA_LEVEL
    rows = []
    for lv in levels:
        if lv <= now:
            continue
        gain, back = 0.0, []
        for d in decs:
            why = json.loads(d.why or "{}")
            # only the ability term moves; the skills-fit part of the loop's logit is unchanged
            l1 = _sig(_logit(d.p_loop) + decide.LOOP["slope"] * (lv - now))
            if d.verdict == "after_dsa":
                if not decide.parked_for_dsa(d.dsa, bool(why.get("high_pay")), lv):
                    back.append({"company_id": d.company_id, "pool": d.pool, "dsa": d.dsa})
                    gain += d.p_offer * l1 / max(d.p_loop, 1e-9)
            else:
                gain += d.p_offer * (l1 / max(d.p_loop, 1e-9) - 1)
        if back:
            names = {c.id: c.name for c in session.exec(select(Company).where(col(Company.id).in_([b["company_id"] for b in back]))).all()}
            for b in back:
                b["name"] = names.get(b["company_id"])
        rows.append({"level": lv, "expected_offers_gained": round(gain, 4), "companies_back": back})
    return {"level_now": now, "levels": rows, "status": _item_status(session, "DSA")}


def _item_status(session, skill: str) -> str:
    it = session.get(UpskillItem, skill)
    return it.status if it else "todo"


# ------------------------------------------------------------------ your progress

def mark(skill: str, status: str, proof_url: str | None = None, proof_note: str | None = None) -> dict:
    if status not in ("todo", "learning", "done"):
        raise ValueError("status must be todo, learning or done")
    if status == "done" and not (proof_url and proof_note):
        raise ValueError("done needs proof: a link and a line in your own words about what you built")
    init_db()
    with get_session() as session:
        it = session.get(UpskillItem, skill) or UpskillItem(skill=skill)
        it.status, it.updated_at = status, utcnow()
        if proof_url is not None:
            it.proof_url = proof_url.strip() or None
        if proof_note is not None:
            it.proof_note = proof_note.strip() or None
        session.add(it)
        session.commit()
        out = {"skill": it.skill, "status": it.status, "proof_url": it.proof_url, "proof_note": it.proof_note,
               "resume_line": resume_line(it) if it.status == "done" else None}
    _CACHE["data"] = None
    return out


def resume_line(it: UpskillItem) -> str:
    """The line to paste into the resume PDF — your words, the skill named, the proof linked."""
    note = (it.proof_note or "").strip().rstrip(".")
    named = it.skill.lower() in note.lower()
    return f"{note}{'' if named else f' ({it.skill})'} — {it.proof_url}"


def add_to_profile(skill: str) -> dict:
    """Add a DONE skill to profile/profile.json as a project, after a dated backup. Then the
    role scores and the brain run again so the gain shows."""
    from jobhunter import confidence

    with get_session() as session:
        it = session.get(UpskillItem, skill)
        if it is None or it.status != "done" or not (it.proof_url and it.proof_note):
            raise ValueError("only a skill marked done, with a proof link and your line, can go into the profile")
        if it.added_to_profile_at:
            return {"skill": skill, "already": it.added_to_profile_at.isoformat()}
        backup = PROFILE.with_name(f"profile-{datetime.now():%Y-%m-%d-%H%M}-before-upskill.json")
        shutil.copy2(PROFILE, backup)
        prof = json.loads(PROFILE.read_text())
        prof.setdefault("projects", []).append({"name": f"{skill} — upskill", "description": it.proof_note.strip(),
                                                "tech": [skill], "url": it.proof_url, "_source": "upskill"})
        PROFILE.write_text(json.dumps(prof, indent=1, ensure_ascii=False))
        it.added_to_profile_at = utcnow()
        line = resume_line(it)
        session.add(it)
        session.commit()
    _reload_profile()
    remapped = remap_stored_briefs()
    confidence.recompute(force=True)
    decide.run()
    _CACHE["data"] = None
    return {"skill": skill, "backup": str(backup.relative_to(ROOT)), "resume_line": line, "briefs_remapped": remapped}


def remap_stored_briefs() -> int:
    """Re-check every stored needs brief against the profile as it is now. The role scores
    read stored briefs, so without this a new skill would not move P(offer) until each brief
    expired. Only the gap mapping and coverage change; the needs, quotes and any synthesised
    prose stay as they were."""
    from dataclasses import fields

    from jobhunter import needs

    names = {f.name for f in fields(needs.Need)}
    n = 0
    with get_session() as session:
        for job in session.exec(select(Job).where(col(Job.needs_brief).is_not(None))).all():
            try:
                brief = json.loads(job.needs_brief)
            except json.JSONDecodeError:
                continue
            ns = [needs.Need(**{k: v for k, v in d.items() if k in names})
                  for d in (brief.get("stated") or []) + (brief.get("implied") or [])]
            for x in ns:   # the mapping is recomputed from scratch, not layered on the old one
                x.status, x.evidence, x.evidence_where, x.evidence_id = "uncovered", None, None, None
                x.covered_skills, x.missing_skills, x.evidence_skills, x.more_evidence = [], [], [], []
            ns = needs.map_gaps(ns)
            brief["stated"] = [x.as_dict() for x in ns if x.kind == "stated"]
            brief["implied"] = [x.as_dict() for x in ns if x.kind == "implied"]
            brief["coverage"] = needs.coverage(ns)
            job.needs_brief = json.dumps(brief, default=str)[:60000]   # needs_brief_at kept: the brief's age is its JD's age
            session.add(job)
            n += 1
        session.commit()
    return n


def _reload_profile() -> None:
    """The resume evidence is cached per process; a changed profile must be read again."""
    from jobhunter import needs

    for fn in (getattr(needs, "_default_evidence", None),):
        if fn is not None and hasattr(fn, "cache_clear"):
            fn.cache_clear()


__all__ = ["analyse", "dsa_outlook", "mark", "add_to_profile", "resume_line"]
