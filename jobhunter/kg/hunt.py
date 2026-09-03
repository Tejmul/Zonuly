"""The graph as the hunting surface — decisions, not decoration.

A knowledge graph that only mirrors tables is a second copy of the database with worse
ergonomics. What makes it worth having is the questions that are *traversals* here and
joins-you-would-never-write in SQL:

    "Why this company, and who do I ask?"      company → funding → investors → tier →
                                               hiring verdict → jobs → skills I have →
                                               contacts ranked by who can refer
    "Who else did that investor fund?"         company → investor → company, two hops.
                                               Companies that share an investor with a
                                               target are underrated by construction.
    "Which of my skills do they actually ask   profile:me -HAS_SKILL-> skill <-REQUIRES- job
     for?"                                     — the free, explainable half of matching
    "Am I allowed to write to this person?"    contact → email → sent_at, and the same
                                               for everyone else at their company.
                                               MOTIV §6's rules, enforced by traversal.

Everything here is read-only over the graph. Nothing writes to the pipeline's tables;
the graph is rebuilt from them by `kg build`.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from jobhunter import CONFIG
from jobhunter.kg.store import Graph

log = logging.getLogger(__name__)

PROFILE_ID = "profile:me"
_OUTREACH = CONFIG.get("outreach") or {}
COOLDOWN_DAYS = int(_OUTREACH.get("contact_cooldown_days", 30))

# The queue order from targeting.py, mirrored here so the graph can sort without
# importing the grader (kg sits above targeting, never the other way round).
_TIER_RANK = {"tier1": 0, "tier2": 1, "prospect": 2, "unknown": 3, "reject": 4}


def _prop(node: dict | None, key: str, default: Any = None) -> Any:
    return (node or {}).get("props", {}).get(key, default)


def resolve(g: Graph, ref: str) -> dict | None:
    """Accept a node id, a company name, or a bare number. People type names, not ids."""
    if not ref:
        return None
    node = g.get(ref)
    if node:
        return node
    if ref.isdigit():
        return g.get(f"company:{ref}")
    for kind in ("company", "contact", "investor", "job"):
        node = g.get(f"{kind}:{ref}")
        if node:
            return node
    hits = [h for h in g.search(ref, kinds=["company"], limit=8)]
    if not hits:
        hits = [h for h in g.search(ref, limit=8)]
    exact = [h for h in hits if (h.get("label") or "").lower() == ref.lower()]
    return (exact or hits or [None])[0]


# ------------------------------------------------------------------ why this company

def why(ref: str, *, max_contacts: int = 10) -> dict:
    """The whole evidence chain for one company, assembled by traversal.

    This is the question the pipeline actually asks before writing to anyone: what do
    they do, are they underrated, can they pay, are they really hiring, which of their
    roles want what I have, and who at the company can refer me.
    """
    with Graph() as g:
        node = resolve(g, ref)
        if node is None or node["kind"] != "company":
            return {"error": f"no company matching {ref!r}"}
        cid = node["id"]
        props = node.get("props", {})

        mine = {e["dst"] for e in g.edges(PROFILE_ID, direction="out", rel="HAS_SKILL")}

        jobs, contacts, investors = [], [], []
        for e in g.edges(cid):
            other = e["dst"] if e["src"] == cid else e["src"]
            kind = other.split(":", 1)[0]
            if kind == "investor":
                inv = g.get(other)
                if inv:
                    investors.append({"id": other, "name": inv["label"]})

        for e in g.edges(cid, direction="in", rel="POSTED_BY"):
            job = g.get(e["src"])
            if not job:
                continue
            wanted = {x["dst"] for x in g.edges(job["id"], direction="out", rel="REQUIRES")}
            overlap = sorted((g.get(s) or {}).get("label", s) for s in (wanted & mine))
            jobs.append({
                "id": job["id"], "title": _prop(job, "title") or job["label"],
                "location": _prop(job, "location"), "remote": _prop(job, "remote"),
                "status": _prop(job, "status"), "score": _prop(job, "score"),
                "salary_lpa": _prop(job, "salary_max_lpa") or _prop(job, "salary_min_lpa"),
                "skills_i_have": overlap, "skill_overlap": len(overlap),
                "url": _prop(job, "url"),
            })
        jobs.sort(key=lambda j: (-(j["skill_overlap"]), -(j["score"] or 0)))

        for e in g.edges(cid, direction="in", rel="WORKS_AT"):
            person = g.get(e["src"])
            if not person:
                continue
            contacts.append({
                "id": person["id"], "name": person["label"],
                "role": _prop(person, "role"), "role_class": _prop(person, "role_class"),
                "rank": _prop(person, "referral_rank") or 9,
                "email": _prop(person, "email"), "confidence": _prop(person, "confidence"),
                "evidence": _prop(person, "role_evidence"),
                "employment": _prop(person, "employment"),
                "employment_why": _prop(person, "employment_why"),
                "guardrail": _contact_guardrail(g, person["id"]),
            })
        conf = {"verified": 0, "pattern-guessed": 1, "scraped": 2}
        contacts.sort(key=lambda c: (c["rank"], conf.get(c["confidence"] or "", 3)))

        all_skills = sorted({s for j in jobs for s in j["skills_i_have"]})
        verdict = _verdict(props, jobs, contacts)

    return {
        "company": node["label"],
        "id": cid,
        "what_they_do": props.get("description"),
        "region": props.get("region"),
        "website": props.get("website"),
        "tier": props.get("tier"),
        "tier_reason": props.get("tier_reason"),
        "underrated": props.get("underrated"),
        "hype_reason": props.get("hype_reason"),
        "pay": {
            "stipend_inr_month": props.get("stipend_inr_month"),
            "stipend_evidence": props.get("stipend_evidence"),
            "ppo_lpa": props.get("ppo_lpa"),
            "ppo_evidence": props.get("ppo_evidence"),
        },
        "funding": {
            "stage": props.get("funding_stage"),
            "amount_usd_m": props.get("funding_amount_usd_m"),
            "investors": investors,
            "evidence": props.get("funding_evidence"),
        },
        "hiring": {
            "status": props.get("hiring_status"),
            "evidence": props.get("hiring_evidence"),
            "careers_url": props.get("careers_url"),
            "roles_on_their_page": props.get("hiring_roles") or [],
        },
        "skills_they_want_that_i_have": all_skills,
        "open_roles": jobs[:12],
        "who_to_ask": contacts[:max_contacts],
        "verdict": verdict,
    }


def _verdict(props: dict, jobs: list[dict], contacts: list[dict]) -> dict:
    """The one-line answer, with the reason it is that answer. Never a bare score."""
    tier = props.get("tier")
    hiring = props.get("hiring_status")
    # A person whose address is on another company's domain is not "a contact at this
    # company" — recommending them is how an invented claim gets written.
    reachable = [c for c in contacts
                 if c["email"] and c["rank"] <= 6 and c["guardrail"]["ok"]
                 and c.get("employment") != "contradicted"]
    contradicted = [c for c in contacts if c.get("employment") == "contradicted"]

    if tier == "reject":
        return {"act": "skip", "why": props.get("tier_reason") or "graded reject"}
    if hiring == "not_authorized":
        return {"act": "skip", "why": f"hiring claim unbacked: {props.get('hiring_evidence') or 'their own pages do not say they are hiring'}"}
    if not reachable:
        blocked = [c for c in contacts if not c["guardrail"]["ok"]]
        if blocked:
            return {"act": "wait", "why": blocked[0]["guardrail"]["why"]}
        if contradicted:
            return {"act": "find people", "why": (
                f"{len(contradicted)} contact(s) on file do not appear to work here — "
                f"{contradicted[0]['employment_why']}. Re-run find-contacts."
            )}
        return {"act": "find people", "why": "no reachable contact who could refer — run find-contacts"}
    if hiring in (None, "unchecked"):
        return {"act": "verify hiring first", "why": "nobody has checked whether their own careers page backs this"}
    if hiring == "unreachable":
        return {"act": "verify hiring first", "why": props.get("hiring_evidence") or "we could not read their site"}
    best = reachable[0]
    caveat = ""
    if best.get("employment") == "unproven":
        caveat = " — but their employment here is unproven, so the email must not claim it"
    if contradicted:
        caveat += f"; {len(contradicted)} other contact(s) on file do not work here"
    return {
        "act": "write",
        "why": (f"{tier or 'ungraded'}, hiring {hiring}, {len(jobs)} role(s) open — "
                f"ask {best['name']} ({best['role_class']}){caveat}"),
    }


# ------------------------------------------------------------------ guardrails

def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def _contact_guardrail(g: Graph, contact_id: str) -> dict:
    """MOTIV §6 as a traversal: have we written to them, or to their colleague, recently?

    One follow-up never two, and never twice into the same company inside the cooldown —
    two near-identical emails from the same college a week apart reads as spam and burns
    the company for both of us.
    """
    cutoff = datetime.now() - timedelta(days=COOLDOWN_DAYS)

    for e in g.edges(contact_id, direction="in", rel="SENT_TO"):
        email = g.get(e["src"])
        if not email:
            continue
        sent = _parse(_prop(email, "sent_at"))
        status = _prop(email, "status")
        if status in ("sent", "replied") and sent and sent > cutoff:
            return {"ok": False, "why": f"already wrote to them on {sent:%Y-%m-%d} — inside the {COOLDOWN_DAYS}-day cooldown"}
        if status in ("draft", "approved"):
            return {"ok": False, "why": f"a {status} email to them is already in the queue"}

    company = next((e["dst"] for e in g.edges(contact_id, direction="out", rel="WORKS_AT")), None)
    if company:
        for e in g.edges(company, direction="in", rel="TARGETS"):
            email = g.get(e["src"])
            if not email:
                continue
            sent = _parse(_prop(email, "sent_at"))
            if _prop(email, "status") in ("sent", "replied") and sent and sent > cutoff:
                to = _prop(email, "to")
                return {"ok": False, "why": f"someone at this company ({to}) was written to on {sent:%Y-%m-%d}"}
    return {"ok": True, "why": "clear"}


def guardrails(ref: str) -> dict:
    """Answer 'may I write to this person?' for one contact, or for a whole company."""
    with Graph() as g:
        node = resolve(g, ref)
        if node is None:
            return {"error": f"nothing matching {ref!r}"}
        if node["kind"] == "contact":
            return {"contact": node["label"], **_contact_guardrail(g, node["id"])}
        if node["kind"] != "company":
            return {"error": f"{node['id']} is a {node['kind']}, not a contact or company"}
        out = []
        for e in g.edges(node["id"], direction="in", rel="WORKS_AT"):
            person = g.get(e["src"])
            if not person:
                continue
            row = {"contact": person["label"], "id": person["id"],
                   "employment": _prop(person, "employment"),
                   "employment_why": _prop(person, "employment_why"),
                   **_contact_guardrail(g, person["id"])}
            # The cooldown is not the only reason not to write to someone. Being clear of
            # it while not actually working there is the worst of both: allowed, and wrong.
            if row["employment"] == "contradicted":
                row["ok"] = False
                row["why"] = row["employment_why"]
            out.append(row)
        return {"company": node["label"], "contacts": out,
                "clear": sum(1 for c in out if c["ok"]), "blocked": sum(1 for c in out if not c["ok"])}


# ------------------------------------------------------------------ warm expansion

def expand(ref: str, *, limit: int = 25) -> dict:
    """Companies two hops away through a shared investor.

    An investor who wrote a seed cheque into one company we like has a portfolio of
    companies at the same stage, the same size and the same obscurity — which is the
    definition of the target. This is how the registry grows without a funding feed.
    """
    with Graph() as g:
        node = resolve(g, ref)
        if node is None:
            return {"error": f"nothing matching {ref!r}"}

        if node["kind"] == "investor":
            investors = [node]
        elif node["kind"] == "company":
            investors = [g.get(e["dst"]) for e in g.edges(node["id"], direction="out", rel="FUNDED_BY")]
            investors = [i for i in investors if i]
        else:
            return {"error": f"{node['id']} is a {node['kind']} — pass a company or an investor"}

        if not investors:
            return {"seed": node["label"], "investors": [], "companies": [],
                    "hint": "no investor edges yet — run `targets enrich` to extract funding, then `kg build`"}

        seen: dict[str, dict] = {}
        for inv in investors:
            for e in g.edges(inv["id"], direction="in", rel="FUNDED_BY"):
                other = g.get(e["src"])
                if not other or other["id"] == node["id"]:
                    continue
                row = seen.setdefault(other["id"], {
                    "id": other["id"], "name": other["label"],
                    "tier": _prop(other, "tier"), "region": _prop(other, "region"),
                    "what_they_do": _prop(other, "description"),
                    "hiring": _prop(other, "hiring_status"),
                    "via": [],
                })
                row["via"].append(inv["label"])

        rows = sorted(seen.values(), key=lambda r: (-len(r["via"]), _TIER_RANK.get(r["tier"] or "unknown", 9)))
    return {"seed": node["label"], "investors": [i["label"] for i in investors],
            "companies": rows[:limit]}


# ------------------------------------------------------------------ the shortlist

def shortlist(limit: int = 20, *, tiers: tuple[str, ...] = ("tier1", "tier2", "prospect")) -> list[dict]:
    """Rank companies by everything the graph knows, not by one column.

    Ordering is: grade first (that is the pay thesis), then whether their own site backs
    the hiring, then how many of our skills their open roles actually ask for, then
    whether there is a reachable person who could refer us. A company we cannot write to
    is not a lead, however well it pays.
    """
    with Graph() as g:
        mine = {e["dst"] for e in g.edges(PROFILE_ID, direction="out", rel="HAS_SKILL")}
        rows = []
        for node in g.nodes(kind="company", layer="data"):
            tier = _prop(node, "tier")
            if tier not in tiers:
                continue
            cid = node["id"]

            skills: set[str] = set()
            open_roles = 0
            for e in g.edges(cid, direction="in", rel="POSTED_BY"):
                open_roles += 1
                skills |= {x["dst"] for x in g.edges(e["src"], direction="out", rel="REQUIRES")}
            overlap = skills & mine

            reachable = 0
            best = None
            for e in g.edges(cid, direction="in", rel="WORKS_AT"):
                person = g.get(e["src"])
                if not person or not _prop(person, "email"):
                    continue
                rank = _prop(person, "referral_rank") or 9
                if (rank <= 6 and _prop(person, "employment") != "contradicted"
                        and _contact_guardrail(g, person["id"])["ok"]):
                    reachable += 1
                    if best is None or rank < best["rank"]:
                        best = {"name": person["label"], "rank": rank,
                                "role_class": _prop(person, "role_class")}

            hiring = _prop(node, "hiring_status")
            rows.append({
                "id": cid, "name": node["label"], "tier": tier,
                "what_they_do": _prop(node, "description"),
                "region": _prop(node, "region"),
                "ppo_lpa": _prop(node, "ppo_lpa"),
                "hiring": hiring,
                "open_roles": open_roles,
                "skill_overlap": len(overlap),
                "skills": sorted((g.get(s) or {}).get("label", s) for s in overlap)[:8],
                "reachable_people": reachable,
                "ask_first": best,
            })

    hiring_rank = {"verified": 0, "role_missing": 1, "unchecked": 2, None: 2, "unreachable": 3, "not_authorized": 4}
    rows.sort(key=lambda r: (
        _TIER_RANK.get(r["tier"], 9),
        hiring_rank.get(r["hiring"], 2),
        -r["skill_overlap"],
        -r["reachable_people"],
    ))
    return rows[:limit]


# ------------------------------------------------------------------ audit

def audit(limit_per_check: int = 20) -> dict:
    """Contradictions between what different parts of the graph believe.

    Each table is individually consistent; it is the *relationships* that go wrong, which
    is precisely what a graph can see and a row cannot. Every finding here is something
    that would otherwise reach a human as a confident, wrong sentence in an email.
    """
    findings: dict[str, list[dict]] = {}

    def add(check: str, row: dict) -> None:
        findings.setdefault(check, [])
        if len(findings[check]) < limit_per_check:
            findings[check].append(row)

    with Graph() as g:
        companies = {n["id"]: n for n in g.nodes(kind="company", layer="data")}

        for e in g.all_edges(rel="WORKS_AT"):
            if (e.get("props") or {}).get("employment") == "contradicted":
                person = g.get(e["src"])
                company = companies.get(e["dst"])
                add("contact_not_at_company", {
                    "contact": (person or {}).get("label"), "id": e["src"],
                    "company": (company or {}).get("label"),
                    "why": (e.get("props") or {}).get("employment_why"),
                })

        for cid, node in companies.items():
            tier = _prop(node, "tier")
            hiring = _prop(node, "hiring_status")
            jobs = g.edges(cid, direction="in", rel="POSTED_BY")

            if tier == "reject":
                high = [j for j in jobs if _prop(g.get(j["src"]), "status") == "high_match"]
                if high:
                    add("scored_a_company_we_rejected", {
                        "company": node["label"], "high_match_jobs": len(high),
                        "why": _prop(node, "tier_reason"),
                    })
            if hiring == "not_authorized":
                drafts = [e for e in g.edges(cid, direction="in", rel="TARGETS")]
                if drafts:
                    add("drafted_against_an_unbacked_claim", {
                        "company": node["label"], "emails": len(drafts),
                        "why": _prop(node, "hiring_evidence"),
                    })
            if jobs and not tier:
                add("ungraded_company_with_jobs", {"company": node["label"], "jobs": len(jobs)})
            if tier in ("tier1", "tier2", "prospect") and hiring in (None, "unchecked"):
                add("target_never_hiring_checked", {"company": node["label"], "tier": tier,
                                                    "jobs": len(jobs)})

        # which of our skills does the market we target never ask for?
        for node in g.nodes(kind="skill", layer="data"):
            if not g.edges(node["id"], direction="in", rel="REQUIRES"):
                add("skill_no_target_job_asks_for", {"skill": node["label"]})

    return {
        "checks": {k: len(v) for k, v in findings.items()},
        "findings": findings,
    }


__all__ = ["why", "expand", "guardrails", "shortlist", "audit", "resolve"]
