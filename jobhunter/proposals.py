"""Email PROPOSALS — how the pipeline would write to the best targets, shown before anything
is drafted into the review queue (the user's 2026-10-14 ask: "show me the proposal for how
you will write emails").

For ~10 of the best targets, spread across the structures (proof-of-work + opinion,
referral to an engineer, founder, recruiter) and both markets (India, Remote), a proposal
holds side by side: the recipient and company evidence used (the hook and its source, the
covered need → resume proof with the posting's quote), the structure and why, every
subject option, the email exactly as it would be sent (signature, resume link, opt-out
line), the quality score with its breakdown, the gate's flags, and what was deliberately
NOT claimed. A few targets whose contract could not be filled are shown too, with what
was missing — that is the pipeline refusing to write a generic email.

Proposals are rows in `email_proposal`, never `email`: no status, no approval, no sender
reads them. `build()` replaces the previous run. Nothing here sends or approves; a proposal
becomes a draft in the review queue only when the user asks (`to_draft`), and `latest()`
shows, per proposal, the draft it became and that draft's state in plain words.

Layering: db, needs (briefs) and outreach (batch, drafter, compose) below; api above.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections import Counter

from sqlmodel import col, delete, select

from jobhunter import admission
from jobhunter.db import Company, Contact, Email, EmailProposal, Job, get_session, init_db, utcnow

log = logging.getLogger(__name__)

STRUCTURE_ORDER = ["direct_intro", "advice_first", "give_first", "fit_question", "recruiter_direct"]


def ensure_brief(session, job: Job) -> bool:
    """A stored needs brief for this role — deterministic, from the cached company context
    (no network, no model). True when one was written."""
    from jobhunter import needs

    if job.needs_brief:
        return False
    company = session.get(Company, job.company_id) if job.company_id else None
    brief = needs.brief_for(job, company)
    brief.pop("stored", None)
    brief["synthesis"] = brief.get("synthesis") if brief.get("synthesis") not in (None, "none") else "pending"
    needs.store_brief(session, job, brief)
    session.commit()
    return True


def ensure_briefs(job_ids: list[int]) -> int:
    n = 0
    with get_session() as session:
        for jid in dict.fromkeys(j for j in job_ids if j):
            job = session.get(Job, jid)
            if job is not None and ensure_brief(session, job):
                n += 1
    return n


def _recruiters(company_ids: list[int] | None = None) -> dict[int, list[int]]:
    """Ready recruiters (confirmed current, with an address, not suppressed) per company —
    at the given companies, or anywhere when None."""
    from jobhunter import guardrails

    out: dict[int, list[int]] = {}
    with get_session() as session:
        stmt = select(Contact).where(Contact.role_class.in_(["tech_recruiter", "recruiter"]))  # type: ignore[attr-defined]
        if company_ids is not None:
            stmt = stmt.where(Contact.company_id.in_(company_ids))  # type: ignore[attr-defined]
        for p in session.exec(stmt).all():
            if p.email and not p.do_not_contact and guardrails.is_ready(p):
                co = session.get(Company, p.company_id)
                if co is None or co.do_not_contact or co.status or (co.overrated and not co.list_key):
                    continue
                if not co.list_key and admission.body_shop(co.name, industry=co.description):
                    continue   # a staffing agency or body shop is not a company to ask for a referral
                out.setdefault(p.company_id, []).append(p.id)
    return out


def _recruiter_targets(skip: set[int]) -> list[dict]:
    """Companies outside the plan's pool where a ready recruiter is the way in."""
    from jobhunter.outreach.drafter import pick_job

    out = []
    with get_session() as session:
        for cid, people in _recruiters(None).items():
            if cid in skip:
                continue
            job = pick_job(session, cid)
            if job is None:
                continue
            co = session.get(Company, cid)
            out.append({"company_id": cid, "company": co.name if co else None, "contact_id": people[0],
                        "alternate_ids": people[1:3], "job_id": job.id})
    return out


def _market(g: dict) -> str:
    from jobhunter.outreach import patterns

    return patterns.market(g.get("job"), g.get("company"))


def _payload(g: dict, r: dict, rank: int) -> dict:
    p, co, j = g["person"], g["company"], g["job"] or {}
    from jobhunter.outreach import compose

    c = r["contract"]
    out = {
        "rank": rank,
        "recipient": {"name": p.get("name"), "first_name": c.get("first_name"), "role": c.get("recipient_role"),
                      "role_class": p.get("role_class"), "email": p.get("email"), "address": p.get("confidence"),
                      "affiliation": p.get("affiliation"), "affiliation_evidence": p.get("affiliation_evidence"),
                      "profile": p.get("linkedin") or p.get("github")},
        "company": {"id": co.get("id"), "name": co.get("name"), "website": co.get("website"),
                    "description": co.get("description"), "funding": " ".join(
                        x for x in [co.get("funding_stage"), f"${co['funding_amount_usd_m']:g}M"
                                    if co.get("funding_amount_usd_m") else None] if x) or None,
                    "team_size": co.get("team_size"), "list": co.get("list_key"), "small": c.get("small")},
        "role": {"id": j.get("id"), "title": j.get("title"), "url": j.get("url"), "location": j.get("location"),
                 "remote_anywhere": j.get("remote_anywhere"), "selection_score": j.get("selection_score")},
        "market": _market(g),
        "structure": {"id": r["structure"], **compose.STRUCTURES.get(r["structure"] or "", {}),
                      "chosen_because": r["structure_why"]},
        "hook": c.get("hook"), "hook_alternatives": c.get("hook_alternatives"),
        "needs": c.get("proofs"),
        "not_claimed": r.get("not_claimed"),
        "complete": not r.get("incomplete"),
        "missing": r.get("missing_labels") or [],
    }
    if r.get("incomplete"):
        return out
    out.update({
        "subjects": r["subjects"], "subject": r["subject"], "subject_pattern": r["subject_pattern"],
        "body": r["body"], "message": r["message"], "words": r["words"],
        "quality": r["quality"], "flags": r["flags"], "writer": r["writer"], "model_note": r.get("model_note"),
        "template_body": r["template"]["body"], "template_quality": r["template"]["quality"]["score"],
        "model_body": (r.get("model") or {}).get("body"),
        "model_quality": ((r.get("model") or {}).get("quality") or {}).get("score"),
        "followup": compose_followup(r, c),
    })
    return out


def compose_followup(r: dict, c: dict) -> dict:
    """The follow-up this email would get after five silent days: one NEW piece of value
    (a proof the first email did not show), not "just bumping this" (playbook §6)."""
    shown = {p.get("proof") for p in (c.get("record") or [])} if c.get("structure") == "direct_intro" else \
        {(c.get("proofs") or [{}])[0].get("proof")}
    first, role = c.get("first_name"), c.get("target_role")
    extra = next((p["proof"] for p in c.get("proofs") or [] if p.get("proof") and p["proof"] not in shown), None)
    if extra:
        body = (f"Hi {first},\n\nA short note on my message from last week about the {role} role. One thing I left out: "
                f"I also {extra}. If it isn't something you can help with, I completely understand. Thank you for reading.")
    else:
        body = (f"Hi {first},\n\nA short note on my message from last week about the {role} role. I know inboxes get "
                f"busy, so this is the last time I'll write. If it isn't something you can help with, I completely "
                f"understand, and thank you for reading.")
    return {"subject": "Re: " + r["subject"], "body": body, "after_days": 5,
            "new_value": bool(extra), "after_yes": r.get("forward_note")}


def build(n: int = 10, *, use_model: bool = True, incomplete_examples: int = 3, pool: int = 60) -> dict:
    """Regenerate the proposals: the best targets (batch.plan's order), every ready
    referrer at each, the contract run for each; then ~n picked round-robin across
    structures and markets, written (model polish when available), and stored."""
    from jobhunter.outreach import batch, drafter

    init_db()
    plan = batch.plan(limit=pool)
    targets = plan["targets"]
    targets = targets + _recruiter_targets({t["company_id"] for t in targets})
    ensure_briefs([t["job_id"] for t in targets])

    # pass 1, cheap: the contract for every ready person at the top companies (no network, no model)
    complete: list[tuple[dict, dict, dict]] = []
    incomplete: list[tuple[dict, dict, dict]] = []
    recruiters = _recruiters([t["company_id"] for t in targets])
    for t in targets:
        found = False
        for cid in [t["contact_id"], *t.get("alternate_ids", [])]:
            g = drafter.gather(cid, t["job_id"], research=False)
            if g.get("error"):
                continue
            r = drafter.write(g, use_model=False)
            if r.get("incomplete"):
                if not found and len(incomplete) < incomplete_examples * 3:
                    incomplete.append((t, g, r))
                continue
            complete.append((t, g, r))
            found = True
            break           # one ask per company
        # a recruiter at the same company is a different structure — offered as an alternative
        for cid in recruiters.get(t["company_id"], [])[:1]:
            if cid == t["contact_id"] or cid in t.get("alternate_ids", []):
                continue
            g = drafter.gather(cid, t["job_id"], research=False)
            if not g.get("error"):
                r = drafter.write(g, use_model=False)
                if not r.get("incomplete"):
                    complete.append((t, g, r))

    # pick: round-robin over structure x market so every structure and both markets show
    buckets: dict[tuple, list] = {}
    for item in complete:
        key = (item[2]["structure"], _market(item[1]))
        buckets.setdefault(key, []).append(item)
    # India and Remote are the targets; an on-site-abroad role fills in only if they run out
    def _ord(keys):
        return sorted(keys, key=lambda k: (STRUCTURE_ORDER.index(k[0]) if k[0] in STRUCTURE_ORDER else 9, k[1]))

    chosen: list = []
    used: set[int] = set()
    for order in (_ord([k for k in buckets if k[1] in ("india", "remote")]),
                  _ord([k for k in buckets if k[1] not in ("india", "remote")])):
        while len(chosen) < n and any(buckets[k] for k in order):
            for k in order:
                while buckets[k] and buckets[k][0][0]["company_id"] in used:
                    buckets[k].pop(0)          # one proposal per company
                if buckets[k] and len(chosen) < n:
                    item = buckets[k].pop(0)
                    used.add(item[0]["company_id"])
                    chosen.append(item)

    # every structure shown at least once, even when its only example is abroad
    have = {it[2]["structure"] for it in chosen}
    for st in STRUCTURE_ORDER:
        if st in have:
            continue
        extra = next((it for k, items in buckets.items() if k[0] == st for it in items
                      if it[0]["company_id"] not in used), None)
        if extra:
            used.add(extra[0]["company_id"])
            chosen.append(extra)

    # pass 2: the chosen ones written properly — public repos read, the model polish tried
    run_id = utcnow().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
    rows, model_ok = [], use_model
    for rank, (t, _g, _r) in enumerate(chosen, 1):
        g = drafter.gather(_g["person"]["id"], t["job_id"], research=True)
        if g.get("error"):
            g = _g
        r = drafter.write(g, use_model=model_ok)
        if r.get("incomplete"):
            r = drafter.write(_g, use_model=model_ok)
        if r.get("budget"):
            model_ok = False
        elif use_model and not model_ok and not r.get("incomplete"):
            r["model_note"] = "model ceiling reached earlier in this run: the template version, marked for polish"
        rows.append((t, g, r))
    seen_co = {t["company_id"] for t, _, _ in rows}
    for t, g, r in incomplete:
        if sum(1 for x in rows if x[2].get("incomplete")) >= incomplete_examples:
            break
        if t["company_id"] in seen_co:
            continue
        seen_co.add(t["company_id"])
        rows.append((t, g, r))

    with get_session() as session:
        session.exec(delete(EmailProposal))
        for rank, (t, g, r) in enumerate(rows, 1):
            payload = _payload(g, r, rank)
            session.add(EmailProposal(run_id=run_id, rank=rank, contact_id=g["person"]["id"],
                                      company_id=g["company"]["id"], job_id=(g["job"] or {}).get("id"),
                                      market=payload["market"], structure=r["structure"],
                                      complete=not r.get("incomplete"), payload=json.dumps(payload, default=str)))
        session.commit()

    scores = [r["quality"]["score"] for _, _, r in rows if not r.get("incomplete")]
    out = {"run_id": run_id, "proposals": len(rows), "complete": len(scores),
           "incomplete_shown": len(rows) - len(scores), "candidates_checked": len(complete) + len(incomplete),
           "complete_available": len(complete),
           "structures": dict(Counter(r["structure"] for _, _, r in rows if not r.get("incomplete"))),
           "markets": dict(Counter(_market(g) for _, g, r in rows if not r.get("incomplete"))),
           "writers": dict(Counter(r["writer"] for _, _, r in rows if not r.get("incomplete"))),
           "quality": {"min": min(scores) if scores else None, "max": max(scores) if scores else None,
                       "mean": round(sum(scores) / len(scores), 1) if scores else None,
                       "bands": dict(Counter(r["quality"]["verdict"] for _, _, r in rows if not r.get("incomplete")))},
           "missing_reasons": dict(Counter(m for _, _, r in incomplete for m in r.get("missing") or [])),
           "skipped_at_plan": plan["skipped"]}
    log.info("proposals: %s", out)
    return out


# What each email state means, in the user's words — the proposal page and the queue
# show these instead of the raw status (2026-10-16: "is it approved or not? where is the
# option to approve?").
STATE_LABEL = {
    "none": "No draft yet — this is only a proposal",
    "draft": "Waiting for your review",
    "approved": "Approved — sends in the next slot",
    "approved_pattern": "Auto-approved by a pattern — sends in the next slot unless you undo it",
    "sending": "Sending now",
    "sent": "Sent",
    "replied": "Sent — they replied",
    "rejected": "Rejected — will not be sent",
    "incomplete": "Not written — evidence missing",
    "failed": "Send failed",
    "stale": "Too old to approve — write it again",
}


def draft_state(e: Email | None) -> dict:
    """The draft behind a proposal (or none), with its state in plain words."""
    from jobhunter.outreach import patterns

    if e is None:
        return {"state": "none", "label": STATE_LABEL["none"]}
    stale = bool(e.status == "draft" and e.expires_at and e.expires_at < utcnow())
    state = ("stale" if stale else "approved_pattern" if e.status == "approved" and e.approved_by == "pattern"
             else e.status if e.status in STATE_LABEL else "draft")
    try:
        flags = json.loads(e.review_flags or "[]")
    except json.JSONDecodeError:
        flags = []
    return {"state": state, "label": STATE_LABEL[state], "id": e.id, "status": e.status, "subject": e.subject,
            "body": e.body, "approved_by": e.approved_by, "pattern_id": e.pattern_id, "pattern_note": e.pattern_note,
            "segment": e.segment, "segment_label": patterns.segment_label(e.segment) if e.segment else None,
            "quality": e.quality, "review_flags": flags, "to_email": e.to_email, "stale": stale,
            "created_at": e.created_at.isoformat() if e.created_at else None,
            "approved_at": e.approved_at.isoformat() if e.approved_at else None,
            "sent_at": e.sent_at.isoformat() if e.sent_at else None}


def _draft_for_contact(session, contact_id: int | None) -> Email | None:
    """The newest cold email to this person — what the proposal turned into, if anything."""
    if not contact_id:
        return None
    return session.exec(select(Email).where(Email.contact_id == contact_id, Email.kind == "cold")
                        .order_by(col(Email.id).desc())).first()


def to_draft(proposal_id: int) -> dict:
    """Put this proposal into the review queue as a draft — the exact email shown, re-gated
    against the same facts. Never approves: the user does that, here or in the queue."""
    from jobhunter.outreach import drafter

    init_db()
    with get_session() as session:
        p = session.get(EmailProposal, proposal_id)
        if p is None:
            return {"error": f"no proposal {proposal_id}"}
        if not p.complete:
            return {"error": "this proposal was not written (evidence missing), so there is nothing to draft"}
        payload = json.loads(p.payload)
        contact_id, job_id = p.contact_id, p.job_id
    if job_id:
        ensure_briefs([job_id])
    r = drafter.draft_for(contact_id, job_id, research=False, use_model=False,
                          text={"subject": payload.get("subject"), "body": payload.get("body"),
                                "writer": payload.get("writer")})
    with get_session() as session:
        r["draft"] = draft_state(_draft_for_contact(session, contact_id))
    return r


def latest() -> dict:
    """The stored proposals, best first, with the run's summary — and for each, the draft
    it became (if any) and that draft's state."""
    init_db()
    with get_session() as session:
        rows = session.exec(select(EmailProposal).order_by(EmailProposal.rank)).all()
        items = []
        for r in rows:
            try:
                items.append({"id": r.id, "run_id": r.run_id, "created_at": r.created_at.isoformat(), **json.loads(r.payload),
                              "draft": draft_state(_draft_for_contact(session, r.contact_id))})
            except json.JSONDecodeError:
                continue
    scores = [i["quality"]["score"] for i in items if i.get("complete") and i.get("quality")]
    return {"run_id": items[0]["run_id"] if items else None, "created_at": items[0]["created_at"] if items else None,
            "items": items, "summary": {
                "complete": len(scores), "incomplete": sum(1 for i in items if not i.get("complete")),
                "mean_quality": round(sum(scores) / len(scores), 1) if scores else None,
                "structures": dict(Counter(i["structure"]["id"] for i in items if i.get("complete"))),
                "markets": dict(Counter(i["market"] for i in items if i.get("complete")))}}


__all__ = ["build", "latest", "ensure_brief", "ensure_briefs", "to_draft", "draft_state", "STATE_LABEL"]
