"""Project the pipeline's tables into the graph's data layer.

Mirrors, not copies: each node keeps a `ref_table`/`ref_id` back to its row and only
carries what is useful to search or traverse (no job descriptions, no email bodies).
Edges follow the funnel:

    profile:me -HIGH_MATCH-> job -POSTED_BY-> company <-WORKS_AT- contact <-SENT_TO- email <-REPLIES_TO- reply
    job -SOURCED_FROM-> source        company -USES_ATS-> ats        contact -FOUND_VIA-> channel
    email -ABOUT_JOB-> job            email -FOLLOWS_UP-> email      profile:me -HAS_SKILL-> skill

Idempotent — re-running upserts and prunes rows that vanished upstream.
"""

from __future__ import annotations

import json
import logging
import re

from sqlmodel import select

from jobhunter import CONFIG, ROOT
from jobhunter.db import Company, Contact, Email, Job, Reply, get_session, init_db
from jobhunter.kg.store import Graph, slugify

log = logging.getLogger(__name__)

PROFILE_ID = "profile:me"

# Contact.source -> the context feature that produced it. This is the bridge between the
# data layer and the context layer: "who found this person" resolves to a design decision.
CHANNEL_FEATURE = {
    "github": "feature:github-commit-mining",
    "site": "feature:team-page-scrape",
    "pattern": "feature:email-pattern-inference",
    "hunter": "feature:hunter-free-tier-for-patterns",
}


def _loads(text: str | None, default):
    if not text:
        return default
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return default


def _iso(dt) -> str | None:
    return dt.isoformat(timespec="seconds") if dt else None


def sync_companies(g: Graph, companies: list[Company]) -> int:
    """Company nodes carry the four things we actually decide on: what they do, the
    stipend, what a PPO pays, and the grade that follows from those (targeting.py)."""
    keep: set[str] = set()
    ats_seen: set[str] = set()
    tiers_seen: set[str] = set()
    regions_seen: set[str] = set()
    for c in companies:
        nid = f"company:{c.id}"
        keep.add(nid)
        pay_bits = []
        if c.stipend_inr_month:
            pay_bits.append(f"stipend ₹{c.stipend_inr_month:,}/month")
        if c.ppo_lpa:
            pay_bits.append(f"PPO ₹{c.ppo_lpa:g} LPA")
        summary = " · ".join(filter(None, [
            c.description or c.notes,
            ", ".join(pay_bits) or None,
            f"[{c.tier}] {c.tier_reason}" if c.tier else None,
        ])) or None
        g.upsert_node(
            nid, "company", c.name,
            summary=summary,
            props={
                "website": c.website, "domain": c.domain, "github_org": c.github_org,
                "ats": c.ats, "ats_slug": c.ats_slug, "email_pattern": c.email_pattern,
                "contacts_found_at": _iso(c.contacts_found_at),
                # --- targeting: why this company is or is not a target
                "description": c.description,
                "tier": c.tier, "tier_reason": c.tier_reason,
                "underrated": c.underrated, "hype_reason": c.hype_reason,
                "region": c.hq_region,
                "stipend_inr_month": c.stipend_inr_month, "stipend_evidence": c.stipend_evidence,
                "ppo_lpa": c.ppo_lpa, "ppo_evidence": c.ppo_evidence,
                "funding_stage": c.funding_stage, "funding_amount_usd_m": c.funding_amount_usd_m,
                "funding_investors": _loads(c.funding_investors, []),
                "funding_evidence": c.funding_evidence,
                "graded_at": _iso(c.graded_at),
                # --- hiring authenticity: does their own site back the claim?
                "careers_url": c.careers_url, "hiring_status": c.hiring_status,
                "hiring_evidence": c.hiring_evidence, "hiring_roles": _loads(c.hiring_roles, []),
                "hiring_claim": c.hiring_claim, "hiring_claim_url": c.hiring_claim_url,
                "hiring_checked_at": _iso(c.hiring_checked_at),
            },
            layer="data", ref_table="company", ref_id=c.id,
            body=" ".join(filter(None, [
                c.name, c.domain, c.github_org, c.ats, c.notes, c.description,
                c.tier, c.tier_reason, c.hype_reason, c.hq_region,
                c.stipend_evidence, c.ppo_evidence, c.funding_stage, c.funding_evidence,
                c.hiring_status, c.hiring_evidence,
            ])),
        )
        if c.ats:
            aid = f"ats:{c.ats}"
            if aid not in ats_seen:
                g.upsert_node(aid, "ats", c.ats, props={}, layer="data")
                ats_seen.add(aid)
            g.upsert_edge(nid, "USES_ATS", aid, layer="data")
        # the grade and the region become nodes, so "every tier1 company in Germany"
        # is a two-hop traversal rather than a scan
        if c.tier:
            tid = f"tier:{c.tier}"
            if tid not in tiers_seen:
                g.upsert_node(tid, "tier", c.tier, props={}, layer="data")
                tiers_seen.add(tid)
            g.upsert_edge(nid, "GRADED", tid, props={"reason": c.tier_reason}, layer="data")
        if c.hq_region:
            rid = f"region:{c.hq_region}"
            if rid not in regions_seen:
                g.upsert_node(rid, "region", c.hq_region, props={}, layer="data")
                regions_seen.add(rid)
            g.upsert_edge(nid, "BASED_IN", rid, layer="data")
    g.prune("company", keep)
    for kind in ("tier", "region"):
        g.prune(kind, {n for n in (tiers_seen | regions_seen) if n.startswith(kind + ":")})
    return len(keep)


def _skill_matcher(skills: list[str]) -> tuple[re.Pattern[str], dict[str, str]] | None:
    """One alternation over every profile skill — 1,200 job descriptions in one pass each."""
    lookup = {s.lower(): s for s in skills if len(s) > 1}
    if not lookup:
        return None
    # longest first so "machine learning" wins over "learning"
    alts = sorted((re.escape(k) for k in lookup), key=len, reverse=True)
    return re.compile(r"(?<![a-z0-9])(" + "|".join(alts) + r")(?![a-z0-9])", re.I), lookup


def sync_jobs(g: Graph, jobs: list[Job], skills: list[str] | None = None) -> int:
    keep: set[str] = set()
    sources_seen: set[str] = set()
    matcher = _skill_matcher(skills or [])
    for j in jobs:
        nid = f"job:{j.id}"
        keep.add(nid)
        parsed = _loads(j.match_reasons, {})
        reasons = parsed.get("reasons", []) if isinstance(parsed, dict) else []
        verdict = parsed.get("verdict") if isinstance(parsed, dict) else (j.match_reasons or None)
        gaps = _loads(j.skill_gaps, [])
        salary = None
        if j.salary_min_lpa or j.salary_max_lpa:
            salary = f"₹{j.salary_min_lpa or '?'}–{j.salary_max_lpa or '?'} LPA"
        summary = verdict or " · ".join(filter(None, [j.location, salary]))
        g.upsert_node(
            nid, "job", f"{j.title} — {j.company_name}",
            summary=summary,
            props={
                "title": j.title, "company": j.company_name, "location": j.location, "remote": j.remote,
                "url": j.url, "source": j.source, "status": j.status,
                "score": j.match_score, "embed_sim": j.embed_sim,
                "salary_min_lpa": j.salary_min_lpa, "salary_max_lpa": j.salary_max_lpa, "salary_raw": j.salary_raw,
                "posted_at": _iso(j.posted_at), "scraped_at": _iso(j.scraped_at), "scored_at": _iso(j.scored_at),
                "reasons": reasons, "gaps": gaps,
            },
            layer="data", ref_table="job", ref_id=j.id,
            body=" ".join(filter(None, [j.title, j.company_name, j.location or "", verdict or ""] + list(reasons) + list(gaps))),
        )
        if j.company_id:
            g.upsert_edge(nid, "POSTED_BY", f"company:{j.company_id}", layer="data")
        sid = f"source:{j.source}"
        if sid not in sources_seen:
            if g.get(sid) is None:  # context.yaml normally defines sources; fall back to a bare node
                g.upsert_node(sid, "source", j.source, props={}, layer="data")
            sources_seen.add(sid)
        g.upsert_edge(nid, "SOURCED_FROM", sid, layer="data")
        # job -REQUIRES-> skill, for the skills we actually have. This is the free,
        # explainable half of matching: profile:me -HAS_SKILL-> skill <-REQUIRES- job is
        # a path, so "why this job" and "which jobs want Python + RAG" are traversals.
        if matcher:
            pattern, lookup = matcher
            text = " ".join(filter(None, [j.title, (j.description or "")[:8000]]))
            hit = {lookup[m.group(1).lower()] for m in pattern.finditer(text) if m.group(1).lower() in lookup}
            for skill in hit:
                g.upsert_edge(nid, "REQUIRES", f"skill:{slugify(skill)}", layer="data")
        if j.status == "high_match":
            g.upsert_edge(PROFILE_ID, "HIGH_MATCH", nid, props={"score": j.match_score}, layer="data")
        elif j.status in ("applied",):
            g.upsert_edge(PROFILE_ID, "APPLIED", nid, layer="data")
    g.prune("job", keep)
    return len(keep)


def sync_investors(g: Graph, companies: list[Company]) -> int:
    """company -FUNDED_BY-> investor.

    The point is not the investor: it is that two companies sharing one become two hops
    apart. A referral that worked at one Accel-backed startup tells you something about
    the next one, and "who else did they fund" is the cheapest way to grow the target
    list with companies that are underrated by construction.
    """
    keep: set[str] = set()
    for c in companies:
        investors = _loads(c.funding_investors, [])
        if not isinstance(investors, list):
            continue
        for name in investors:
            if not isinstance(name, str) or not name.strip():
                continue
            iid = f"investor:{slugify(name)}"
            keep.add(iid)
            g.upsert_node(iid, "investor", name.strip(), props={}, layer="data",
                          body=name.strip())
            g.upsert_edge(f"company:{c.id}", "FUNDED_BY", iid,
                          props={"stage": c.funding_stage, "amount_usd_m": c.funding_amount_usd_m},
                          layer="data")
    g.prune("investor", keep)
    return len(keep)


# Addresses that prove nothing either way: everyone has one, at every employer.
_FREEMAIL = {
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "yahoo.com",
    "icloud.com", "me.com", "proton.me", "protonmail.com", "pm.me", "fastmail.com",
    "yandex.ru", "qq.com", "163.com", "users.noreply.github.com",
}


def employment_evidence(email: str | None, company_domain: str | None) -> tuple[str, str]:
    """(verdict, why) — does this address actually place the person at this company?

    This exists because it caught a real one: Affirm's GitHub org carries forked Apache
    Flink repositories, so commit mining harvested Flink committers on @confluent.io and
    filed twenty of them as Affirm employees. Writing "I saw your work at Affirm" to a
    Confluent engineer is the fabrication MOTIV §6 forbids, and it burns a contact we
    only get to use once. An address on another company's domain is not weak evidence of
    employment — it is evidence against it.
    """
    domain = (email or "").split("@")[-1].strip().lower()
    company_domain = (company_domain or "").strip().lower()
    if not domain or "@" not in (email or ""):
        return "unproven", "no address to check"
    if not company_domain:
        return "unproven", "no company domain on file to compare against"
    if domain == company_domain or domain.endswith("." + company_domain):
        return "confirmed", f"address is on {company_domain}"
    if domain in _FREEMAIL:
        return "unproven", f"personal address ({domain}) — says nothing about who they work for"
    return "contradicted", f"address is on {domain}, not {company_domain}"


def sync_contacts(g: Graph, contacts: list[Contact], company_domains: dict[int, str] | None = None) -> int:
    keep: set[str] = set()
    channels: set[str] = set()
    company_domains = company_domains or {}
    for c in contacts:
        nid = f"contact:{c.id}"
        keep.add(nid)
        works_at, works_why = employment_evidence(c.email, company_domains.get(c.company_id))
        research = _loads(c.research_notes, None)
        hook = research.get("hook") if isinstance(research, dict) else None
        notes = research.get("notes") if isinstance(research, dict) else c.research_notes
        g.upsert_node(
            nid, "contact", c.name or c.email or f"contact {c.id}",
            summary=" · ".join(filter(None, [
                c.role, c.role_class, f"rank {c.referral_rank}" if c.referral_rank else None,
                f"hook: {hook}" if hook and hook != "null" else None,
            ])) or None,
            props={
                "email": c.email, "role": c.role, "github": c.github, "linkedin": c.linkedin,
                "source": c.source, "confidence": c.confidence, "is_recruiter": c.is_recruiter,
                "employment": works_at, "employment_why": works_why,
                "role_class": c.role_class, "seniority": c.seniority,
                "referral_rank": c.referral_rank, "role_evidence": c.role_evidence,
                "researched_at": _iso(c.researched_at), "research": research,
            },
            layer="data", ref_table="contact", ref_id=c.id,
            body=" ".join(filter(None, [c.name, c.email, c.role, c.confidence, c.source,
                                        c.role_class, c.seniority, c.role_evidence,
                                        notes if isinstance(notes, str) else ""])),
        )
        g.upsert_edge(nid, "WORKS_AT", f"company:{c.company_id}",
                      props={"confidence": c.confidence, "role_class": c.role_class,
                             "referral_rank": c.referral_rank,
                             "employment": works_at, "employment_why": works_why}, layer="data")
        if c.role_class:
            rc = f"role:{c.role_class}"
            g.upsert_node(rc, "role", c.role_class, props={}, layer="data")
            g.upsert_edge(nid, "CLASSIFIED_AS", rc, layer="data")
        ch = f"channel:{c.source}"
        if ch not in channels:
            g.upsert_node(ch, "channel", c.source, props={}, layer="data")
            feat = CHANNEL_FEATURE.get(c.source)
            if feat and g.get(feat) is not None:
                g.upsert_edge(ch, "PROVIDED_BY", feat, layer="data")
            channels.add(ch)
        g.upsert_edge(nid, "FOUND_VIA", ch, layer="data")
    g.prune("contact", keep)
    return len(keep)


def sync_emails(g: Graph, emails: list[Email]) -> int:
    keep: set[str] = set()
    for e in emails:
        nid = f"email:{e.id}"
        keep.add(nid)
        g.upsert_node(
            nid, "email", f"[{e.status}] {e.subject}",
            summary=e.body[:240] if e.body else None,
            props={
                "to": e.to_email, "kind": e.kind, "status": e.status, "error": e.error,
                "gmail_thread_id": e.gmail_thread_id,
                "created_at": _iso(e.created_at), "approved_at": _iso(e.approved_at), "sent_at": _iso(e.sent_at),
                "followup_sent": e.followup_sent,
            },
            layer="data", ref_table="email", ref_id=e.id,
            body=" ".join(filter(None, [e.subject, e.to_email, e.status, e.kind, (e.body or "")[:600]])),
        )
        g.upsert_edge(nid, "SENT_TO", f"contact:{e.contact_id}", layer="data")
        g.upsert_edge(nid, "TARGETS", f"company:{e.company_id}", layer="data")
        if e.job_id:
            g.upsert_edge(nid, "ABOUT_JOB", f"job:{e.job_id}", layer="data")
        if e.parent_email_id:
            g.upsert_edge(nid, "FOLLOWS_UP", f"email:{e.parent_email_id}", layer="data")
    g.prune("email", keep)
    return len(keep)


def sync_replies(g: Graph, replies: list[Reply]) -> int:
    keep: set[str] = set()
    for r in replies:
        nid = f"reply:{r.id}"
        keep.add(nid)
        g.upsert_node(
            nid, "reply", f"[{r.sentiment or 'unclassified'}] from {r.from_addr}",
            summary=r.sentiment_reason,
            props={"from": r.from_addr, "sentiment": r.sentiment, "received_at": _iso(r.received_at), "body": (r.body or "")[:600]},
            layer="data", ref_table="reply", ref_id=r.id,
            body=" ".join(filter(None, [r.from_addr, r.sentiment, r.sentiment_reason, (r.body or "")[:600]])),
        )
        g.upsert_edge(nid, "REPLIES_TO", f"email:{r.email_id}", layer="data")
    g.prune("reply", keep)
    return len(keep)


def _skill_leaves(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _skill_leaves(v)]
    if isinstance(value, (list, tuple)):
        return [s for v in value for s in _skill_leaves(v)]
    return []


def sync_profile(g: Graph) -> list[str]:
    """The candidate as a node, with skills as their own nodes so gaps and jobs can point at them.

    Returns the skill list, because the job sync needs it: a skill node that only ever
    links to profile:me is a label, not a graph. Linking jobs to the same nodes is what
    turns "what do they want that I have?" into a two-hop traversal.
    """
    path = ROOT / CONFIG["profile_path"]
    if not path.exists():
        g.upsert_node(PROFILE_ID, "profile", "candidate (no profile.json yet)", props={}, layer="data")
        return []
    p = json.loads(path.read_text(encoding="utf-8"))
    skills = sorted({s.strip() for s in _skill_leaves(p.get("skills")) if s and s.strip()})
    g.upsert_node(
        PROFILE_ID, "profile", p.get("name") or "candidate",
        summary=p.get("headline"),
        props={
            "email": p.get("email"), "links": p.get("links"), "years_experience": p.get("years_experience"),
            "education": p.get("education"), "experience": p.get("experience"), "projects": p.get("projects"),
            "strengths": p.get("strengths"), "target_titles": p.get("target_titles"), "summary": p.get("summary"),
            "skills": skills,
        },
        layer="data",
        body=" ".join(filter(None, [p.get("name"), p.get("headline"), p.get("summary")] + skills)),
    )
    keep: set[str] = set()
    for s in skills:
        sid = f"skill:{slugify(s)}"
        keep.add(sid)
        g.upsert_node(sid, "skill", s, props={}, layer="data")
        g.upsert_edge(PROFILE_ID, "HAS_SKILL", sid, layer="data")
    g.prune("skill", keep)
    return skills


def sync_all() -> dict:
    """Mirror every table into the data layer. Safe to run after any pipeline stage."""
    init_db()
    # Read everything first, then write. The graph write is one large transaction on the
    # same file; holding a SQLAlchemy reader open across it deadlocks on "database is locked".
    with get_session() as session:
        companies = session.exec(select(Company)).all()
        jobs = session.exec(select(Job)).all()
        contacts = session.exec(select(Contact)).all()
        emails = session.exec(select(Email)).all()
        replies = session.exec(select(Reply)).all()
    out: dict = {}
    with Graph() as g:
        skills = sync_profile(g)
        out["skills"] = len(skills)
        out["companies"] = sync_companies(g, companies)
        out["investors"] = sync_investors(g, companies)
        out["jobs"] = sync_jobs(g, jobs, skills)
        out["contacts"] = sync_contacts(g, contacts, {c.id: c.domain for c in companies if c.domain})
        out["emails"] = sync_emails(g, emails)
        out["replies"] = sync_replies(g, replies)
    log.info("kg sync: %s", out)
    return out
