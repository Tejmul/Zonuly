"""The company atlas: every scraped company placed on a staged map.

Columns of stages, nodes sized by how many companies sit in them, faint links between
columns. The stages are the questions this pipeline actually asks about a company, in
the order it asks them — so a company's position IS its progress through the funnel:

    L0 REGION    where are they                 (the currency-gap thesis, MOTIV §2)
    L1 GRADE     what can they pay              (targeting.py tiers)
    L2 FUNDING   how recently funded, how small
    L3 HIRING    is the hiring claim real       (hiring_verify.py)
    L4 ROLES     what are they hiring for
    L5 SKILLS    what do they ask for that we have
    L6 PEOPLE    who could refer us             (contacts/roles.py)
    SX CULTURE   what is it like to work there  — NOT COLLECTED YET, and shown as such

A link joins two nodes when the same companies sit in both, weighted by how many. So
"tier1 → hiring verified → founder reachable" is a path a company either completes or
falls out of, and the places companies fall out are visible as gaps rather than implied.

Two derived reading aids, defined here so the UI never invents them:

  CROWDED  a node holding at least half of everything in its own stage. Most of the
           pipeline piles up here, so if our judgement about that one node is wrong,
           most of the queue is wrong with it. (A first run has several — that is the
           finding, not a bug.)
  STUCK    a node whose own meaning is "cannot progress from here": hiring never
           checked, hiring claim unbacked, nobody found to ask. These are where
           companies stop moving, not merely the big nodes.

Layering: reads db + targeting + contacts.roles. No network, no model, no writes.
"""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field

from sqlmodel import select

from jobhunter import normalize as norm
from jobhunter.contacts import roles as roleclass
from jobhunter.db import Company, Contact, Job, get_session, init_db
from jobhunter.kg.sync import _FREEMAIL
from jobhunter.targeting import TIER_ORDER

log = logging.getLogger(__name__)

CROWDED_SHARE = 0.5      # of its own stage
STUCK_MIN = 3            # companies before a stalled stage is worth flagging

# Nodes that ARE the blockage: a company sitting here has nowhere to go next.
STUCK_NODES = {
    "hiring:unchecked", "hiring:not_authorized", "hiring:unreachable",
    "people:nobody", "grade:unknown",
}

STAGES: list[dict] = [
    {"key": "region",  "code": "S1", "label": "Region",   "blurb": "Where they are. The currency gap is the whole thesis."},
    {"key": "grade",   "code": "S2", "label": "Grade",    "blurb": "What they can pay, and whether they're underrated."},
    {"key": "funding", "code": "S3", "label": "Funding",  "blurb": "Recently funded and still small, or past it."},
    {"key": "hiring",  "code": "S4", "label": "Hiring",   "blurb": "Does their own careers page back the claim?"},
    {"key": "roles",   "code": "S5", "label": "Roles",    "blurb": "What they are actually hiring for."},
    {"key": "skills",  "code": "S6", "label": "Skills",   "blurb": "What they ask for that we already have."},
    {"key": "people",  "code": "S7", "label": "People",   "blurb": "Who at the company could refer us."},
    {"key": "culture", "code": "SX", "label": "Culture",  "blurb": "Reviews and work culture. Not collected yet."},
]

# Job titles → the family a person would actually name in conversation.
_ROLE_FAMILIES: list[tuple[str, re.Pattern[str]]] = [
    ("AI / ML", re.compile(r"\b(ai|ml|machine learning|llm|genai|nlp|deep learning|applied scientist|research engineer|research scientist)\b", re.I)),
    ("Backend", re.compile(r"\b(backend|back[- ]end|server|api|distributed)\b", re.I)),
    ("Full-stack", re.compile(r"\b(full[- ]?stack)\b", re.I)),
    ("Frontend", re.compile(r"\b(frontend|front[- ]end|ui engineer|web engineer|react)\b", re.I)),
    ("Infrastructure", re.compile(r"\b(infrastructure|platform|devops|sre|site reliability|cloud)\b", re.I)),
    ("Data", re.compile(r"\b(data engineer|data scientist|analytics|etl|warehouse)\b", re.I)),
    ("Security", re.compile(r"\b(security|appsec|infosec|cryptograph)\b", re.I)),
    ("Founding", re.compile(r"\b(founding)\b", re.I)),
    ("Mobile", re.compile(r"\b(mobile|ios|android|react native|flutter)\b", re.I)),
]

_HIRING_LABEL = {
    "verified": "Verified on their own page",
    "role_missing": "Hiring, but not this role",
    "not_authorized": "Claim not backed",
    "unreachable": "Could not check",
    "unchecked": "Not checked yet",
}
_GRADE_LABEL = {
    "tier1": "Tier 1 — ₹30 LPA+",
    "tier2": "Tier 2 — ₹24–30 LPA",
    "prospect": "Prospect — engineers + verified hiring",
    "unknown": "Pay not stated yet",
    "reject": "Rejected",
}


@dataclass
class Node:
    id: str
    layer: str
    label: str
    companies: set[int] = field(default_factory=set)
    note: str | None = None

    def as_dict(self, *, ready: set[int], stage_total: int) -> dict:
        can_go = self.companies & ready
        return {
            "id": self.id,
            "layer": self.layer,
            "label": self.label,
            "note": self.note,
            "count": len(self.companies),
            "ready": len(can_go),
            "blocked": len(self.companies) - len(can_go),
            "crowded": bool(stage_total and len(self.companies) >= stage_total * CROWDED_SHARE),
            "stuck": self.id in STUCK_NODES and len(self.companies) >= STUCK_MIN,
            "company_ids": sorted(self.companies),
        }


def _families(title: str) -> list[str]:
    hits = [name for name, pattern in _ROLE_FAMILIES if pattern.search(title or "")]
    return hits or (["Other engineering"] if norm.title_relevant(title or "") else [])


def _build_funnel(*, include_rejects: bool = False) -> dict:
    """The old atlas: pipeline stages as columns, buckets as nodes. Kept for reference —
    it answered "where does the pipeline pile up", which is a report, not a map."""
    init_db()
    with get_session() as session:
        companies = session.exec(select(Company)).all()
        jobs = session.exec(select(Job)).all()
        contacts = session.exec(select(Contact)).all()

    if not include_rejects:
        companies = [c for c in companies if (c.tier or "unknown") != "reject"]
    keep_ids = {c.id for c in companies}
    by_id = {c.id: c for c in companies}

    jobs_by_company: dict[int, list[Job]] = defaultdict(list)
    for j in jobs:
        if j.company_id in keep_ids:
            jobs_by_company[j.company_id].append(j)
    contacts_by_company: dict[int, list[Contact]] = defaultdict(list)
    for c in contacts:
        if c.company_id in keep_ids:
            contacts_by_company[c.company_id].append(c)

    nodes: dict[str, Node] = {}

    def put(layer: str, key: str, label: str, cid: int, note: str | None = None) -> None:
        nid = f"{layer}:{key}"
        node = nodes.get(nid)
        if node is None:
            node = nodes[nid] = Node(id=nid, layer=layer, label=label, note=note)
        node.companies.add(cid)

    # A company is outreach-ready when their own page backs the hiring AND there is at
    # least one person we could write to whose address does not contradict working there.
    ready: set[int] = set()
    no_people: set[int] = set()
    for c in companies:
        reachable = [
            p for p in contacts_by_company.get(c.id, [])
            if p.email and (p.referral_rank or 9) <= 6
            and (not c.domain or p.email.split("@")[-1].lower() == c.domain.lower()
                 or p.email.split("@")[-1].lower() in _FREEMAIL)
        ]
        if not reachable:
            no_people.add(c.id)
        if reachable and c.hiring_status in ("verified", "role_missing"):
            ready.add(c.id)

    for c in companies:
        cid = c.id
        put("region", c.hq_region or "unknown", (c.hq_region or "unstated").upper(), cid)
        grade = c.tier or "unknown"
        put("grade", grade, _GRADE_LABEL.get(grade, grade), cid)
        stage = (c.funding_stage or "unstated").lower()
        put("funding", stage, stage.title() if stage != "unstated" else "Stage unstated", cid)
        hiring = c.hiring_status or "unchecked"
        put("hiring", hiring, _HIRING_LABEL.get(hiring, hiring), cid,
            note=c.hiring_evidence)

        for j in jobs_by_company.get(cid, []):
            for fam in _families(j.title or ""):
                put("roles", fam.lower().replace(" ", "-"), fam, cid)

        if cid in no_people:
            put("people", "nobody", "Nobody found yet", cid,
                note="No contact we could write to — run find-contacts for this company.")
        for p in contacts_by_company.get(cid, []):
            if p.role_class:
                put("people", p.role_class, roleclass.LABELS.get(p.role_class, p.role_class), cid)

    # skills come from the graph's job→skill edges when they exist, else from the profile
    from jobhunter.kg.store import Graph

    try:
        with Graph() as g:
            mine = {e["dst"]: (g.get(e["dst"]) or {}).get("label")
                    for e in g.edges("profile:me", direction="out", rel="HAS_SKILL")}
            for job_node in g.nodes(kind="job", layer="data"):
                cid = None
                for e in g.edges(job_node["id"], direction="out", rel="POSTED_BY"):
                    cid = int(e["dst"].split(":")[1])
                if cid not in keep_ids:
                    continue
                for e in g.edges(job_node["id"], direction="out", rel="REQUIRES"):
                    label = mine.get(e["dst"])
                    if label:
                        put("skills", e["dst"].split(":", 1)[1], label, cid)
    except Exception as e:  # noqa: BLE001 — the atlas is still readable without skills
        log.warning("skills layer unavailable: %s", e)

    # LX stays empty on purpose: there is no reviews source yet, and an invented
    # culture score is exactly the thing MOTIV §6 forbids.
    culture = Node(id="culture:none", layer="culture", label="Not collected yet",
                   note="No reviews source is wired up. Nothing here is inferred.")

    total_targets = sum(1 for c in companies if (c.tier or "unknown") in ("tier1", "tier2", "prospect"))
    # a layer's total is the companies that appear anywhere in it, so concentration is
    # measured against the layer, not against the whole registry
    layer_totals: dict[str, int] = {}
    for n in nodes.values():
        layer_totals.setdefault(n.layer, set())
        layer_totals[n.layer] |= n.companies  # type: ignore[operator]
    layer_totals = {k: len(v) for k, v in layer_totals.items()}  # type: ignore[arg-type]

    out_nodes = [n.as_dict(ready=ready, stage_total=layer_totals.get(n.layer, 0)) for n in nodes.values()]
    out_nodes.append(culture.as_dict(ready=ready, stage_total=0))

    # edges: same company in two nodes of different layers, weighted by overlap
    order = {l["key"]: i for i, l in enumerate(STAGES)}
    edges: list[dict] = []
    by_layer: dict[str, list[Node]] = defaultdict(list)
    for n in nodes.values():
        by_layer[n.layer].append(n)
    layer_keys = [l["key"] for l in STAGES if by_layer.get(l["key"])]
    for a_key, b_key in zip(layer_keys, layer_keys[1:]):
        for a in by_layer[a_key]:
            for b in by_layer[b_key]:
                shared = a.companies & b.companies
                if shared:
                    edges.append({"source": a.id, "target": b.id, "weight": len(shared)})

    companies_out = [
        {
            "id": c.id, "name": c.name, "tier": c.tier, "region": c.hq_region,
            "description": c.description, "ppo_lpa": c.ppo_lpa,
            "stipend_inr_month": c.stipend_inr_month,
            "hiring_status": c.hiring_status, "website": c.website,
            "funding_stage": c.funding_stage,
            "jobs": len(jobs_by_company.get(c.id, [])),
            "contacts": len(contacts_by_company.get(c.id, [])),
            "ready": c.id in ready,
        }
        for c in companies
    ]
    companies_out.sort(key=lambda r: (TIER_ORDER.get(r["tier"] or "unknown", 9), -(r["ppo_lpa"] or 0), r["name"]))

    return {
        "stages": [
            {**l, "nodes": sum(1 for n in out_nodes if n["layer"] == l["key"])}
            for l in STAGES
        ],
        "nodes": sorted(out_nodes, key=lambda n: (order.get(n["layer"], 9), -n["count"])),
        "edges": edges,
        "companies": companies_out,
        "stats": {
            "stages": len(STAGES),
            "nodes": len(out_nodes),
            "links": len(edges),
            "companies": len(companies_out),
            "targets": total_targets,
            "crowded": sum(1 for n in out_nodes if n["crowded"]),
            "stuck": sum(1 for n in out_nodes if n["stuck"]),
            "ready": len(ready),
            "blocked": len(companies) - len(ready),
        },
        "definitions": {
            "crowded": f"A node holding ≥{int(CROWDED_SHARE * 100)}% of its own stage — most of the pipeline piles up here, so if our judgement about it is wrong, most of the queue is wrong with it.",
            "stuck": "A stage that is itself the blockage — hiring never checked, hiring claim unbacked, or nobody found to ask.",
            "ready": "Their own page backs the hiring AND there is someone we could write to.",
        },
    }


def company_detail(company_id: int) -> dict:
    """Everything known about one company, for the detail page."""
    init_db()
    with get_session() as session:
        c = session.get(Company, company_id)
        if c is None:
            return {"error": f"no company {company_id}"}
        jobs = session.exec(select(Job).where(Job.company_id == company_id)).all()
        people = session.exec(select(Contact).where(Contact.company_id == company_id)).all()

    from jobhunter import segments as seg
    from jobhunter.kg import hunt

    graph = hunt.why(f"company:{company_id}")
    reachable_ids = {p.id for p in _reachable(c, people)}
    fresher = sum(1 for j in jobs if not j.is_senior)
    anywhere = sum(1 for j in jobs if j.remote_anywhere)
    hiring_ok = c.hiring_status in ("verified", "role_missing")
    checks = {
        "hiring": hiring_ok, "fresher": fresher > 0,
        "location": bool(anywhere) or c.hq_region in ("india", "remote"),
        "lead": bool(reachable_ids),
    }
    primary, _ = seg.classify(c.description, c.notes, c.name)
    evidence, trust = _trust(c, jobs)
    # where the roles actually are — the locations the postings name, most common first
    loc_counts: dict[str, int] = {}
    for j in jobs:
        key = (j.location or ("Remote" if j.remote else "")).strip()
        if key:
            loc_counts[key] = loc_counts.get(key, 0) + 1
    locations = [k for k, _ in sorted(loc_counts.items(), key=lambda kv: -kv[1])[:5]]

    return {
        "id": c.id,
        "name": c.name,
        "description": c.description,
        "website": c.website,
        "domain": c.domain,
        "github_org": c.github_org,
        "region": c.hq_region,
        "locations": locations,
        "segment": {"id": primary.id, "label": primary.label, "layer": primary.layer},
        "trust": trust,
        "evidence": evidence,
        "story": c.story,
        "story_evidence": c.story_evidence,
        "valuation_usd_m": c.valuation_usd_m,
        "valuation_evidence": c.valuation_evidence,
        "team_size": c.team_size,
        "pay_basis": c.pay_basis,
        "pay_basis_evidence": c.pay_basis_evidence,
        "pay_power": {"score": c.pay_power, "band": c.pay_power_band, "why": c.pay_power_why,
                      "per_head_usd_k": c.money_per_head_usd_k},
        "checks": checks,
        "bet": all(checks.values()),
        "missing": [k for k, v in checks.items() if not v],
        "hiring_post": {
            "text": c.hiring_claim, "url": c.hiring_claim_url, "by": c.hiring_claim_by,
            "source": c.hiring_claim_source,
            "at": c.hiring_claim_at.isoformat() if c.hiring_claim_at else None,
        } if c.hiring_claim_url else None,
        "tier": c.tier,
        "tier_reason": c.tier_reason,
        "underrated": c.underrated,
        "hype_reason": c.hype_reason,
        "pay": {
            "stipend_inr_month": c.stipend_inr_month,
            "stipend_evidence": c.stipend_evidence,
            "ppo_lpa": c.ppo_lpa,
            "ppo_evidence": c.ppo_evidence,
        },
        "funding": {
            "stage": c.funding_stage,
            "amount_usd_m": c.funding_amount_usd_m,
            "announced": c.funding_announced,
            "investors": json.loads(c.funding_investors) if c.funding_investors else [],
            "evidence": c.funding_evidence,
        },
        "hiring": {
            "status": c.hiring_status,
            "evidence": c.hiring_evidence,
            "careers_url": c.careers_url,
            "roles_on_their_page": json.loads(c.hiring_roles) if c.hiring_roles else [],
            "checked_at": c.hiring_checked_at.isoformat() if c.hiring_checked_at else None,
        },
        "culture": {
            "collected": False,
            "why": "No reviews source is wired up yet, so nothing about work culture is "
                   "shown. It would have to be invented, and this pipeline does not invent.",
        },
        "jobs": [
            {
                "id": j.id, "title": j.title, "location": j.location, "remote": j.remote,
                "url": j.url, "source": j.source, "score": j.match_score,
                "salary_min_lpa": j.salary_min_lpa, "salary_max_lpa": j.salary_max_lpa,
                "is_internship": j.is_internship, "stipend_inr_month": j.stipend_inr_month,
                "is_senior": bool(j.is_senior), "remote_anywhere": bool(j.remote_anywhere),
                "posted_at": j.posted_at.isoformat() if j.posted_at else None,
            }
            # fresher roles first, then remote-from-anywhere, then by score
            for j in sorted(jobs, key=lambda j: (j.is_senior, not j.remote_anywhere, -(j.match_score or 0)))
        ],
        "people": [
            {
                "id": p.id, "name": p.name, "role": p.role, "role_class": p.role_class,
                "label": roleclass.LABELS.get(p.role_class or "", p.role_class),
                "rank": p.referral_rank, "email": p.email, "confidence": p.confidence,
                "github": p.github, "evidence": p.role_evidence,
                "reachable": p.id in reachable_ids,
                "source": p.source,
            }
            # the ones we can actually write to first, then by who can refer
            for p in sorted(people, key=lambda p: (p.id not in reachable_ids, p.referral_rank or 9, p.name or ""))
        ],
        "leads": len(reachable_ids),
        "graph": {
            "verdict": graph.get("verdict"),
            "skills_they_want_that_i_have": graph.get("skills_they_want_that_i_have", []),
            "who_to_ask": graph.get("who_to_ask", []),
        } if not graph.get("error") else None,
    }


# ------------------------------------------------------------------ company-first

def company_list(*, include_rejects: bool = False) -> dict:
    """One row per company — never one per role.

    The atlas answers "which companies are in this stage", which necessarily places a
    company in every role family it hires for. That is right for a map and wrong for a
    list: read down the atlas and Abridge appears six times, once per family, as though
    it were six companies. This is the other view, where the company is the unit and its
    roles and its referrers hang underneath it, grouped.
    """
    init_db()
    with get_session() as session:
        companies = session.exec(select(Company)).all()
        jobs = session.exec(select(Job)).all()
        contacts = session.exec(select(Contact)).all()

    if not include_rejects:
        companies = [c for c in companies if (c.tier or "unknown") != "reject"]
    keep = {c.id for c in companies}

    jobs_by: dict[int, list[Job]] = defaultdict(list)
    for j in jobs:
        if j.company_id in keep:
            jobs_by[j.company_id].append(j)
    people_by: dict[int, list[Contact]] = defaultdict(list)
    for p in contacts:
        if p.company_id in keep:
            people_by[p.company_id].append(p)

    out = []
    for c in companies:
        cjobs = jobs_by.get(c.id, [])

        # roles grouped by family, so "15 roles" reads as the shape of their team
        families: dict[str, list[Job]] = defaultdict(list)
        for j in cjobs:
            for fam in _families(j.title or "") or ["Other"]:
                families[fam].append(j)
        roles = [
            {
                "family": fam,
                "count": len(js),
                # fresher roles first, then by score — a senior title is kept, not chased
                "titles": [j.title for j in sorted(js, key=lambda j: (j.is_senior, -(j.match_score or 0)))[:6]],
                "best_score": max((j.match_score or 0) for j in js) or None,
                "internships": sum(1 for j in js if j.is_internship),
                "fresher": sum(1 for j in js if not j.is_senior),
                "anywhere": sum(1 for j in js if j.remote_anywhere),
            }
            for fam, js in sorted(families.items(), key=lambda kv: -len(kv[1]))
        ]

        cpeople = sorted(people_by.get(c.id, []), key=lambda p: (p.referral_rank or 9))
        referrers = _reachable(c, cpeople)
        best = referrers[0] if referrers else None
        evidence, trust = _trust(c, cjobs)

        out.append({
            "id": c.id,
            "name": c.name,
            "trust": trust,
            "evidence": evidence,
            "pay_basis": c.pay_basis,
            "team_size": c.team_size,
            "pay_power": {"score": c.pay_power, "band": c.pay_power_band, "why": c.pay_power_why,
                          "per_head_usd_k": c.money_per_head_usd_k},
            "description": c.description,
            "website": c.website,
            "region": c.hq_region,
            "tier": c.tier,
            "tier_reason": c.tier_reason,
            "underrated": c.underrated,
            "ppo_lpa": c.ppo_lpa,
            "stipend_inr_month": c.stipend_inr_month,
            "funding_stage": c.funding_stage,
            "hiring_status": c.hiring_status,
            "hiring_evidence": c.hiring_evidence,
            "careers_url": c.careers_url,
            # "where did you get this?" — the post itself, who made it, and where
            "hiring_post": {
                "text": c.hiring_claim, "url": c.hiring_claim_url, "by": c.hiring_claim_by,
                "source": c.hiring_claim_source, "at": c.hiring_claim_at,
            } if c.hiring_claim_url else None,
            # the two things a company is actually made of, for our purposes
            "roles": {
                "total": len(cjobs),
                "fresher": sum(1 for j in cjobs if not j.is_senior),
                # the company says, in the posting, that it hires from any country
                "anywhere": sum(1 for j in cjobs if j.remote_anywhere),
                "internships": sum(1 for j in cjobs if j.is_internship),
                "families": roles,
            },
            "referrals": {
                "total": len(cpeople),
                "reachable": len(referrers),
                "best": {
                    "name": best.name, "role_class": best.role_class,
                    "label": roleclass.LABELS.get(best.role_class or "", best.role_class),
                    "email": best.email, "confidence": best.confidence,
                } if best else None,
            },
            "ready": bool(referrers) and c.hiring_status in ("verified", "role_missing"),
        })

    # what we can trust first, then remote-from-anywhere (the currency-gap thesis only
    # pays there), then fresher roles
    out.sort(key=lambda r: (
        _TRUST_ORDER.get(r["trust"], 9),
        -(r["pay_power"]["score"] or 0),
        TIER_ORDER.get(r["tier"] or "unknown", 9),
        0 if r["roles"]["anywhere"] else 1,
        -(r["roles"]["fresher"]),
        -(r["roles"]["total"]),
        -(r["ppo_lpa"] or 0),
        r["name"],
    ))
    return {
        "companies": out,
        "stats": {
            "companies": len(out),
            "roles": sum(r["roles"]["total"] for r in out),
            "fresher_roles": sum(r["roles"]["fresher"] for r in out),
            "anywhere_roles": sum(r["roles"]["anywhere"] for r in out),
            "with_anywhere_roles": sum(1 for r in out if r["roles"]["anywhere"]),
            "with_roles": sum(1 for r in out if r["roles"]["total"]),
            "with_fresher_roles": sum(1 for r in out if r["roles"]["fresher"]),
            "complete": sum(1 for r in out if r["trust"] == "complete"),
            "partial": sum(1 for r in out if r["trust"] == "partial"),
            "bare": sum(1 for r in out if r["trust"] == "bare"),
            "with_referrals": sum(1 for r in out if r["referrals"]["reachable"]),
            "ready": sum(1 for r in out if r["ready"]),
        },
    }


# ------------------------------------------------------------------ trust

_TRUST_ORDER = {"complete": 0, "partial": 1, "bare": 2}


def _trust(c: Company, jobs: list[Job]) -> tuple[dict, str]:
    """How much of this company have we actually read, and from where.

    Tejmul, 2026-09-03: a row that is only a name cannot be trusted, and must not sit
    next to one whose own pages we have read. So every company carries what evidence
    exists — description, pay, funding, hiring proof, the hiring post — and a level:
      complete  description + pay evidence + hiring proven on their own site
      partial   some of it
      bare      a name and a link, nothing read yet → "needs research"
    Nothing here is inferred; a missing field is missing.
    """
    evidence = {
        "description": bool(c.description and len(c.description.strip()) >= 25),
        # stated pay, or a funding round strong enough to stand in for it (an estimate,
        # and shown as one) — Tejmul's "based on valuation we decide"
        "pay": bool(c.ppo_lpa or c.stipend_inr_month or any(j.salary_min_lpa for j in jobs)
                    or c.pay_basis == "funding-strong"
                    or (c.pay_power or 0) >= 65),   # Pay Power: funded or better
        "funding": bool(c.funding_stage or c.funding_amount_usd_m),
        "story": bool(c.story),
        "hiring": c.hiring_status in ("verified", "role_missing"),
        "post": bool(c.hiring_claim_url),
    }
    if evidence["description"] and evidence["pay"] and evidence["hiring"]:
        trust = "complete"
    elif evidence["description"] or evidence["hiring"] or evidence["pay"]:
        trust = "partial"
    else:
        trust = "bare"
    return evidence, trust


# ------------------------------------------------------------------ the market map

def _reachable(c: Company, people: list[Contact]) -> list[Contact]:
    """Leads we could actually write to: an address, a referring role, and a domain that
    does not contradict working there."""
    domain = (c.domain or "").lower()
    return [
        p for p in people
        if p.email and (p.referral_rank or 9) <= 6
        and (not domain or p.email.split("@")[-1].lower() == domain
             or p.email.split("@")[-1].lower() in _FREEMAIL)
    ]


def build(*, include_rejects: bool = False) -> dict:
    """The atlas as a market map: industry-chain layers, segments as nodes.

    A node is a segment ("Fintech & payments", "LLM inference & AI infrastructure"),
    sized by the fitting companies filed under it. Click → those companies, best first.

    Two marks, defined here so the UI never invents them:

      CHOKEPOINT  a segment holding at least one *bet* — a company where everything
                  lines up: hiring proven on its own site, a fresher role, remote from
                  anywhere (or India on-site), and a lead with a usable email. Where
                  the 25 emails a day should go.
      BOTTLENECK  a segment with *near-bets* but no bets: companies that clear hiring +
                  fresher + remote and lack only a lead (or the hiring check). That is
                  the machine's work queue — find-contacts / verify — not yours.

    Links join a company's primary segment to the other segments it also matches, so a
    "Healthcare & bio" node that leans on "AI agents" shows it.
    """
    from jobhunter import segments as seg

    init_db()
    with get_session() as session:
        companies = session.exec(select(Company)).all()
        jobs = session.exec(select(Job)).all()
        contacts = session.exec(select(Contact)).all()

    if not include_rejects:
        companies = [c for c in companies if (c.tier or "unknown") != "reject"]
    keep_ids = {c.id for c in companies}

    jobs_by: dict[int, list[Job]] = defaultdict(list)
    for j in jobs:
        if j.company_id in keep_ids:
            jobs_by[j.company_id].append(j)
    people_by: dict[int, list[Contact]] = defaultdict(list)
    for p in contacts:
        if p.company_id in keep_ids:
            people_by[p.company_id].append(p)

    # ---- per-company facts the map is built from
    facts: dict[int, dict] = {}
    for c in companies:
        cjobs = jobs_by.get(c.id, [])
        leads = _reachable(c, people_by.get(c.id, []))
        fresher = sum(1 for j in cjobs if not j.is_senior)
        anywhere = sum(1 for j in cjobs if j.remote_anywhere)
        hiring_ok = c.hiring_status in ("verified", "role_missing")
        # remote-from-India for foreign companies; on-site is fine in India (MOTIV §2)
        location_ok = bool(anywhere) or c.hq_region in ("india", "remote")
        checks = {"hiring": hiring_ok, "fresher": fresher > 0, "location": location_ok, "lead": bool(leads)}
        bet = all(checks.values())
        near = (not bet) and checks["fresher"] and checks["location"] and (checks["hiring"] or checks["lead"])
        primary, hits = seg.classify(c.description, c.notes, c.name)
        facts[c.id] = {
            "segment": primary.id, "segments": [h.id for h in hits] or [primary.id],
            "leads": len(leads), "fresher": fresher, "anywhere": anywhere, "roles": len(cjobs),
            "checks": checks, "bet": bet, "near": near,
            "missing": [k for k, v in checks.items() if not v],
        }

    # ---- nodes: one per segment that has companies
    nodes: dict[str, dict] = {}
    for c in companies:
        f = facts[c.id]
        s = seg.segment(f["segment"])
        n = nodes.setdefault(s.id, {
            "id": s.id, "layer": s.layer, "label": s.label, "note": s.blurb,
            "count": 0, "bets": 0, "near": 0, "leads": 0, "fresher": 0, "anywhere": 0, "roles": 0,
            "company_ids": [],
        })
        n["count"] += 1
        n["bets"] += int(f["bet"])
        n["near"] += int(f["near"])
        n["leads"] += int(f["leads"] > 0)
        n["fresher"] += int(f["fresher"] > 0)
        n["anywhere"] += int(f["anywhere"] > 0)
        n["roles"] += f["roles"]
        n["company_ids"].append(c.id)
    for n in nodes.values():
        n["chokepoint"] = n["bets"] > 0
        n["bottleneck"] = n["bets"] == 0 and n["near"] >= 3

    # ---- links: primary segment → every other segment the same company matches
    pair: dict[tuple[str, str], int] = defaultdict(int)
    for f in facts.values():
        for other in f["segments"]:
            if other != f["segment"] and other in nodes:
                a, b = sorted((f["segment"], other))
                pair[(a, b)] += 1
    edges = [{"source": a, "target": b, "weight": w} for (a, b), w in pair.items() if w >= 2]

    # ---- companies for the panel, best first
    def rank(c: Company) -> tuple:
        f = facts[c.id]
        return (0 if f["bet"] else 1 if f["near"] else 2, TIER_ORDER.get(c.tier or "unknown", 9),
                -f["anywhere"], -f["fresher"], -f["leads"], c.name)

    companies_out = [
        {
            "id": c.id, "name": c.name, "tier": c.tier, "region": c.hq_region,
            "description": c.description, "website": c.website, "funding_stage": c.funding_stage,
            "ppo_lpa": c.ppo_lpa, "stipend_inr_month": c.stipend_inr_month,
            "hiring_status": c.hiring_status,
            "segment": facts[c.id]["segment"], "segment_label": seg.segment(facts[c.id]["segment"]).label,
            "roles": facts[c.id]["roles"], "fresher": facts[c.id]["fresher"], "anywhere": facts[c.id]["anywhere"],
            "leads": facts[c.id]["leads"],
            "bet": facts[c.id]["bet"], "near": facts[c.id]["near"], "missing": facts[c.id]["missing"],
            "hiring_post": {"url": c.hiring_claim_url, "by": c.hiring_claim_by, "source": c.hiring_claim_source}
            if c.hiring_claim_url else None,
        }
        for c in sorted(companies, key=rank)
    ]

    order = {l["key"]: i for i, l in enumerate(seg.LAYERS)}
    out_nodes = sorted(nodes.values(), key=lambda n: (order.get(n["layer"], 9), -n["bets"], -n["count"]))
    bets = sum(1 for f in facts.values() if f["bet"])
    near = sum(1 for f in facts.values() if f["near"])
    return {
        "stages": [{**l, "nodes": sum(1 for n in out_nodes if n["layer"] == l["key"])} for l in seg.LAYERS],
        "nodes": out_nodes,
        "edges": edges,
        "companies": companies_out,
        "stats": {
            "layers": len(seg.LAYERS), "nodes": len(out_nodes), "links": len(edges),
            "companies": len(companies_out), "bets": bets, "near": near,
            "chokepoints": sum(1 for n in out_nodes if n["chokepoint"]),
            "bottlenecks": sum(1 for n in out_nodes if n["bottleneck"]),
        },
        "definitions": {
            "chokepoint": "A segment holding at least one bet: hiring proven on their own site, a fresher role, "
                          "remote from anywhere (or India on-site), and a lead with a usable email. "
                          "Where the 25 emails a day should go.",
            "bottleneck": "A segment with near-bets but no bets — companies that clear hiring, fresher and remote "
                          "and lack only a lead (or the hiring check). The machine's queue, not yours.",
            "bet": "All four line up: hiring · fresher role · remote-from-anywhere or India · a lead.",
            "near": "Three of four; what is missing is named on the company.",
        },
    }


__all__ = ["build", "company_detail", "company_list", "STAGES"]
