"""Pattern approval — approve a KIND of email once, not every email one by one.

The user's ask (2026-10-16): "Once I feel that this is good and I'm giving some reviews,
I'll change it. It should depend on the patterns so that this kind of mail should go for
this specific email." A pattern is one segment — the structure the drafter picked for who
the recipient is (compose.choose_structure: engineer referral, founder, recruiter, proof of
work to a small team) x the market (india / remote / abroad) — and the email the user
approved for it, WITH his edits, stored as a template: every per-person fact (their first
name, the company, the role, the hook sentence, the need, the proofs) becomes a {{slot}};
everything he wrote around them stays word for word.

With `outreach.pattern_approval: true`, a new cold draft in an active pattern's segment is
written from that template with the new person's own evidence, and is approved without a
click ONLY when every gate still passes: the review gate's flags (faithfulness, uncovered
claim, AI tells, length, one ask...; a guessed address is the only flag allowed, the
bounce brake and the guessed cap still hold it at send time), the contract is kept, the
quality is at least `pattern_min_quality`, and sender.approve's own checks (confirmed-
current referrer, suppression, cooldowns, staleness). It is marked approved_by="pattern"
so the queue shows it, and it can be undone — or the whole pattern revoked, which sends
every unsent one back to review at once — until the send agent takes its slot. The send
cap, the warm-up quota, the window and the bounce brake are the sender's and are untouched.

Off (the default), nothing here changes a draft: patterns can be approved and are kept,
but every email waits for the per-email review, as before.

Layering: db, outreach (compose, drafter, sender, suppress) only. api mounts routes.py.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from sqlalchemy import text as sql
from sqlmodel import col, func, select

from jobhunter import CONFIG, ROOT
from jobhunter.db import Company, Email, EmailPattern, Job, get_session, init_db, utcnow

log = logging.getLogger(__name__)

STRUCTURE_LABEL = {
    "proof_opinion": "Proof of work → their opinion (small team)",
    "referral_engineer": "Referral ask to an engineer",
    "founder_direct": "Direct ask to a founder",
    "recruiter_direct": "Direct ask to a recruiter",
}
MARKET_LABEL = {"india": "India", "remote": "Remote", "abroad": "Abroad (on-site)"}

# a slot the template must keep: without it every email of the kind would say the same
# thing about different people ("your post about X" to someone who never wrote it)
REQUIRED = ("first_name", "hook", "role")
# the only gate flag an auto-approval may carry — the bounce brake and the guessed cap
# still decide at send time whether a guessed address goes
ALLOWED_FLAGS = {"guessed"}

_SLOT = re.compile(r"\{\{(\w+)\}\}")
# tests set this; None = read config.yaml
_OVERRIDE: bool | None = None
_cfg_cache: dict = {"mtime": None, "value": None}


def _outreach_cfg() -> dict:
    """outreach.* read fresh from config.yaml when the file changed, so flipping
    pattern_approval takes effect without restarting the API."""
    path: Path = ROOT / "config.yaml"
    try:
        mtime = path.stat().st_mtime
        if _cfg_cache["mtime"] != mtime:
            import yaml

            _cfg_cache.update(mtime=mtime, value=(yaml.safe_load(path.read_text()) or {}).get("outreach") or {})
        return _cfg_cache["value"] or {}
    except Exception:  # noqa: BLE001 — a half-written file: the config loaded at start stands
        return CONFIG.get("outreach") or {}


def enabled() -> bool:
    if _OVERRIDE is not None:
        return _OVERRIDE
    return bool(_outreach_cfg().get("pattern_approval", False))


def min_quality() -> int:
    return int(_outreach_cfg().get("pattern_min_quality", 75))


def max_waiting() -> int:
    """Auto-approved mail never stacks up more than one day's cap ahead of the send agent."""
    from jobhunter.outreach import sender

    return int(_outreach_cfg().get("pattern_max_waiting", sender.DAILY_CAP))


# ------------------------------------------------------------------ segments

def market(job: dict | None, company: dict | None) -> str:
    """india | remote | abroad — the same reading the proposals page uses."""
    j, co = job or {}, company or {}
    if j.get("market"):
        return j["market"]
    loc = (j.get("location") or "").lower()
    if j.get("remote_anywhere"):
        return "remote"
    if any(k in loc for k in ("bengaluru", "bangalore", "hyderabad", "india")) or co.get("list_key"):
        return "india"
    return "remote" if j.get("remote") else "abroad"


def segment(structure: str | None, mkt: str | None) -> str:
    return f"{structure or 'referral_engineer'}:{mkt or 'abroad'}"


def segment_label(seg: str | None) -> str:
    st, _, mk = (seg or ":").partition(":")
    return f"{STRUCTURE_LABEL.get(st, st or '?')} · {MARKET_LABEL.get(mk, mk or '?')}"


def segment_of_email(session, e: Email) -> str:
    """A stored draft's segment (older drafts did not record one)."""
    if e.segment:
        return e.segment
    job = session.get(Job, e.job_id) if e.job_id else None
    co = session.get(Company, e.company_id) if e.company_id else None
    jd = {"market": job.selection_market, "location": job.location, "remote": job.remote,
          "remote_anywhere": job.remote_anywhere} if job else None
    return segment(e.structure, market(jd, {"list_key": co.list_key} if co else None))


# ------------------------------------------------------------------ templates

def _cap(s: str) -> str:
    return s[0].upper() + s[1:] if s else s


def slots(contract: dict | None) -> dict[str, str]:
    """The per-person facts of one email, by slot name — what a template leaves open."""
    c = contract or {}
    need = c.get("need") or {}
    out = {"first_name": c.get("first_name"), "company": c.get("company"), "role": c.get("target_role"),
           "role_full": c.get("target_role_full"), "hook": (c.get("hook") or {}).get("sentence"),
           "need": need.get("what")}
    if need.get("what"):
        # compose.fill's own phrase: a stated need "asks for", an implied one "suggests you need"
        out["asks_for"] = (f"asks for {need['what']}" if need.get("kind") == "stated"
                           else f"suggests you need {need['what']}")
    for i, p in enumerate((c.get("proofs") or [])[:3], 1):
        out[f"proof{i}"] = p.get("proof")
        out[f"topic{i}"] = p.get("topic")
    return {k: str(v).strip() for k, v in out.items() if v and len(str(v).strip()) >= 2}


def to_template(text: str, values: dict[str, str]) -> str:
    """Replace every per-person fact in `text` with its {{slot}} — longest first, so the
    hook sentence goes before the company name inside it. A capitalised use ("Migrated
    a pipeline..." at a bullet's start) becomes {{Proof1}}."""
    out = text or ""
    for name, val in sorted(values.items(), key=lambda kv: -len(kv[1])):
        for v, marker in ((val, name), (_cap(val), name[0].upper() + name[1:])):
            out = re.sub(r"(?<!\w)" + re.escape(v) + r"(?!\w)", lambda _m, mk=marker: "{{" + mk + "}}", out)
    return out


def render(template: str, values: dict[str, str]) -> tuple[str, list[str]]:
    """The template filled with one person's facts; the slots it needs that they lack."""
    missing: list[str] = []

    def sub(m: re.Match) -> str:
        name = m.group(1)
        key = name[0].lower() + name[1:]
        v = values.get(key)
        if not v:
            missing.append(key)
            return m.group(0)
        return _cap(v) if name[0].isupper() else v

    return _SLOT.sub(sub, template or ""), missing


def used_slots(template: str) -> set[str]:
    return {m[0].lower() + m[1:] for m in _SLOT.findall(template or "")}


# ------------------------------------------------------------------ reads

def _active(session, seg: str) -> EmailPattern | None:
    return session.exec(select(EmailPattern).where(EmailPattern.segment == seg, EmailPattern.status == "active")
                        .order_by(col(EmailPattern.id).desc())).first()


def _pattern_dict(session, p: EmailPattern) -> dict:
    def n(*conds) -> int:
        return session.exec(select(func.count()).select_from(Email).where(Email.pattern_id == p.id, *conds)).one()

    return {"id": p.id, "segment": p.segment, "label": segment_label(p.segment), "structure": p.structure,
            "market": p.market, "status": p.status, "subject_template": p.subject_template,
            "body_template": p.body_template, "slots": sorted(used_slots(p.body_template + " " + p.subject_template)),
            "source_email_id": p.source_email_id, "edited": p.edited,
            "created_at": p.created_at.isoformat() if p.created_at else None,
            "revoked_at": p.revoked_at.isoformat() if p.revoked_at else None,
            "auto_approved": n(Email.approved_by == "pattern"),
            "waiting_to_send": n(Email.approved_by == "pattern", Email.status == "approved"),
            "sent": n(Email.approved_by == "pattern", col(Email.status).in_(["sent", "replied"])),
            "written_for_review": n(Email.status == "draft")}


def list_patterns() -> dict:
    init_db()
    with get_session() as session:
        rows = session.exec(select(EmailPattern).order_by(EmailPattern.status != "active",
                                                          col(EmailPattern.id).desc())).all()
        items = [_pattern_dict(session, p) for p in rows]
        waiting: dict[str, int] = {}
        for e in session.exec(select(Email).where(Email.status == "draft", Email.kind == "cold")).all():
            s = segment_of_email(session, e)
            waiting[s] = waiting.get(s, 0) + 1
    for it in items:
        it["drafts_waiting_in_segment"] = waiting.get(it["segment"], 0) if it["status"] == "active" else 0
    return {"enabled": enabled(), "min_quality": min_quality(), "max_waiting": max_waiting(), "patterns": items}


def review_counts() -> dict:
    """What needs the user: drafts waiting for review, and auto-approved mail he can still undo."""
    init_db()
    with get_session() as session:
        def n(*conds) -> int:
            return session.exec(select(func.count()).select_from(Email).where(*conds)).one()

        return {"waiting": n(Email.status == "draft"),
                "approved_unsent": n(Email.status == "approved"),
                "auto_approved_unsent": n(Email.status == "approved", Email.approved_by == "pattern"),
                "pattern_approval": enabled()}


# ------------------------------------------------------------------ the drafter's hook

def decide(g: dict, r: dict) -> dict | None:
    """For a complete cold draft about to be stored: the active pattern for its segment
    rewrites it from the template and says whether it may be auto-approved. None when
    pattern approval is off or no pattern matches. Never raises into the drafter."""
    if not enabled() or r.get("incomplete"):
        return None
    seg = segment(r.get("structure"), market(g.get("job"), g.get("company")))
    try:
        with get_session() as session:
            p = _active(session, seg)
            if p is None:
                return None
            pid, body_t, subj_t = p.id, p.body_template, p.subject_template
    except Exception as e:  # noqa: BLE001 — an old DB file without the table: no pattern
        log.debug("patterns: %s", e)
        return None
    label = f"pattern #{pid} ({segment_label(seg)})"
    vals = slots(r.get("contract"))
    body, miss_b = render(body_t, vals)
    subject, miss_s = render(subj_t, vals)
    missing = sorted(set(miss_b + miss_s))
    if missing:
        return {"pattern_id": None, "auto": False,
                "note": f"{label} matched, but this person's evidence has no {', '.join(missing)} for its template "
                        "— written the usual way, waiting for your review"}
    from jobhunter.outreach import drafter

    v = drafter.evaluate_body(g, r, subject, body)
    problems = []
    bad = [f for f in v["flags"] if f["kind"] not in ALLOWED_FLAGS]
    if bad:
        problems.append("the gate flagged " + ", ".join(sorted({f["kind"] for f in bad})))
    if v["dropped"]:
        problems.append("it dropped " + ", ".join(v["dropped"]))
    if v["quality"]["score"] < min_quality():
        problems.append(f"quality {v['quality']['score']} is under {min_quality()}")
    if problems and (v["blocked"] or v["dropped"]):
        # the template's version is worse than the drafter's own: keep the drafter's
        return {"pattern_id": None, "auto": False,
                "note": f"{label} matched, but its version of this email failed: {'; '.join(problems)} "
                        "— written the usual way, waiting for your review"}
    out = {"pattern_id": pid, "auto": not problems, "segment": seg,
           "text": {"subject": subject[:180], "body": v["body"], "flags": v["flags"], "words": v["words"],
                    "quality": v["quality"]}}
    out["note"] = (f"Written from {label}; every gate passed" if not problems else
                   f"Written from {label}, but not auto-approved: {'; '.join(problems)} — waiting for your review")
    return out


def finish(email_id: int, decision: dict | None) -> dict | None:
    """After the drafter stored the draft: approve it for the pattern when the decision
    allows and sender.approve's own checks pass; otherwise leave the reason on it."""
    if not decision:
        return None
    from jobhunter.outreach import sender, suppress

    note = decision.get("note")
    auto = bool(decision.get("auto"))
    if auto:
        with get_session() as session:
            e = session.get(Email, email_id)
            waiting = session.exec(select(func.count()).select_from(Email).where(
                Email.status == "approved", Email.approved_by == "pattern")).one()
            guessed = e is not None and (e.address_confidence or "verified") != "verified"
            brake = suppress.brake(session) if guessed else None
        if waiting >= max_waiting():
            auto, note = False, (f"{note}, but {waiting} pattern-approved emails are already waiting to send "
                                 f"(limit {max_waiting()}) — waiting for your review")
        elif brake:
            auto, note = False, f"{note}, but the address is guessed and {brake} — waiting for your review"
    if auto:
        r = sender.approve(email_id)
        if r.get("error"):
            auto, note = False, f"{note}, but approval was refused: {r['error']}"
        else:
            note = f"Auto-approved by {note.removeprefix('Written from ').split(';')[0]} — sends in its slot unless you undo it"
    with get_session() as session:
        e = session.get(Email, email_id)
        if e is None:
            return None
        e.pattern_note = note[:500] if note else None
        if decision.get("pattern_id"):
            e.pattern_id = decision["pattern_id"]
        if auto:
            e.approved_by = "pattern"
        session.add(e)
        session.commit()
    return {"auto_approved": auto, "pattern_id": decision.get("pattern_id"), "note": note}


# ------------------------------------------------------------------ the user's actions

def approve_from(email_id: int, *, subject: str | None = None, body: str | None = None) -> dict:
    """Make this draft — with the user's edits — the pattern for its segment, and approve
    the draft itself. Replaces the segment's previous pattern (kept, marked replaced)."""
    from jobhunter.outreach import sender

    init_db()
    with get_session() as session:
        e = session.get(Email, email_id)
        if e is None:
            return {"error": f"no email {email_id}"}
        if e.kind != "cold" or not e.contract:
            return {"error": "only a cold draft written by the composer can become a pattern"}
        if e.status not in ("draft", "approved", "rejected"):
            return {"error": f"this email is '{e.status}' — a pattern is made from a draft"}
        contract = json.loads(e.contract)
        seg = segment_of_email(session, e)
        subject = subject if subject is not None else e.subject
        body = body if body is not None else e.body
        edited = (subject != e.subject) or (body != e.body)
    vals = slots(contract)
    body_t, subj_t = to_template(body, vals), to_template(subject, vals)
    lost = [s for s in REQUIRED if s in vals and s not in used_slots(body_t)]
    if lost:
        names = {"first_name": "their first name", "hook": "the line about them (the hook)", "role": "the role's name"}
        return {"error": "A pattern has to keep a place for each person's own " + ", ".join(names[s] for s in lost)
                         + ". Your edit changed or removed it, so every email of this kind would say the same thing "
                         "about different people. Keep that part as drafted, or approve just this email."}
    with get_session() as session:
        e = session.get(Email, email_id)
        if e.status != "approved":
            r = sender.approve(email_id, subject=subject, body=body)
            if r.get("error"):
                return {"error": f"the draft itself could not be approved: {r['error']}"}
        session.expire_all()
        for old in session.exec(select(EmailPattern).where(EmailPattern.segment == seg,
                                                           EmailPattern.status == "active")).all():
            old.status, old.revoked_at = "replaced", utcnow()
            session.add(old)
        st, _, mk = seg.partition(":")
        p = EmailPattern(segment=seg, structure=st, market=mk, subject_template=subj_t, body_template=body_t,
                         source_email_id=email_id, edited=edited)
        session.add(p)
        session.commit()
        session.refresh(p)
        e = session.get(Email, email_id)
        e.pattern_id, e.segment = p.id, seg
        e.pattern_note = f"You approved this email and made it pattern #{p.id} ({segment_label(seg)})"
        session.add(e)
        session.commit()
        out = _pattern_dict(session, p)
    log.info("pattern %s approved for %s from email %s (edited=%s)", out["id"], seg, email_id, edited)
    return {"approved": True, "email_id": email_id, "pattern": out, "enabled": enabled()}


def _back_to_review(session, where, note: str) -> int:
    """approved -> draft for unsent mail, atomically against the sender's claim
    (approved -> sending): whatever the send agent already took is not touched."""
    r = session.exec(sql("update email set status='draft', approved_at=null, approved_by=null, "
                         "pattern_note=:n where status='approved' and " + where[0]).bindparams(n=note, **where[1]))
    session.commit()
    return r.rowcount or 0


def revoke(pattern_id: int) -> dict:
    """Stop the pattern now; every email it approved that has not left goes back to review."""
    init_db()
    with get_session() as session:
        p = session.get(EmailPattern, pattern_id)
        if p is None:
            return {"error": f"no pattern {pattern_id}"}
        p.status, p.revoked_at = "revoked", utcnow()
        session.add(p)
        session.commit()
        back = _back_to_review(session, ("pattern_id=:p and approved_by='pattern'", {"p": pattern_id}),
                               f"pattern #{pattern_id} was revoked — back to your review")
    return {"revoked": True, "pattern_id": pattern_id, "back_to_review": back}


def unapprove(email_id: int) -> dict:
    """Undo one approval (yours or a pattern's) before the send agent takes it."""
    init_db()
    with get_session() as session:
        e = session.get(Email, email_id)
        if e is None:
            return {"error": f"no email {email_id}"}
        if e.status != "approved":
            return {"error": f"this email is '{e.status}' — only an approved, unsent email can go back to review"}
        n = _back_to_review(session, ("id=:i", {"i": email_id}), "approval undone — back to your review")
    if not n:
        return {"error": "the send agent took it a moment ago"}
    return {"unapproved": True, "email_id": email_id}


def save(email_id: int, *, subject: str | None = None, body: str | None = None) -> dict:
    """Save an edit to a draft without approving it."""
    init_db()
    with get_session() as session:
        e = session.get(Email, email_id)
        if e is None:
            return {"error": f"no email {email_id}"}
        if e.status not in ("draft", "rejected"):
            return {"error": f"this email is '{e.status}' — only a draft can be edited (undo the approval first)"}
        if subject is not None:
            if not subject.strip():
                return {"error": "the subject is empty"}
            e.subject = subject[:180]
        if body is not None:
            if not body.strip():
                return {"error": "the body is empty"}
            e.body = body
        session.add(e)
        session.commit()
    return {"saved": True, "email_id": email_id}


def apply_to_waiting(pattern_id: int, limit: int = 50) -> dict:
    """Run an active pattern over the drafts already waiting in its segment: each is
    rewritten from the template and auto-approved when every gate passes (the switch must
    be on). The rest stay in review, with the reason on them."""
    from jobhunter.outreach import drafter

    init_db()
    if not enabled():
        return {"error": "pattern approval is off — set outreach.pattern_approval: true in config.yaml"}
    with get_session() as session:
        p = session.get(EmailPattern, pattern_id)
        if p is None or p.status != "active":
            return {"error": "that pattern is not active"}
        todo = [(e.id, e.contact_id, e.job_id) for e in session.exec(
            select(Email).where(Email.status == "draft", Email.kind == "cold", col(Email.contract).is_not(None))
            .order_by(col(Email.id))).all() if segment_of_email(session, e) == p.segment][:limit]
    out = {"checked": 0, "auto_approved": 0, "left_for_review": 0, "notes": []}
    for eid, cid, jid in todo:
        g = drafter.gather(cid, jid, research=False)
        if g.get("error"):
            continue
        r = drafter.write(g, use_model=False)
        if r.get("incomplete"):
            continue
        out["checked"] += 1
        d = decide(g, r)
        if d and d.get("text"):
            t = d["text"]
            with get_session() as session:
                e = session.get(Email, eid)
                if e is None or e.status != "draft":
                    continue
                e.subject, e.body, e.words = t["subject"], t["body"], t["words"]
                e.review_flags, e.quality = json.dumps(t["flags"]), t["quality"]["score"]
                e.quality_detail = json.dumps(t["quality"])
                e.segment = d.get("segment") or e.segment
                session.add(e)
                session.commit()
        f = finish(eid, d)
        if f and f["auto_approved"]:
            out["auto_approved"] += 1
        else:
            out["left_for_review"] += 1
            if f and f.get("note"):
                out["notes"].append(f["note"])
    out["notes"] = out["notes"][:10]
    return out


__all__ = ["enabled", "segment", "segment_label", "market", "slots", "to_template", "render", "decide", "finish",
           "approve_from", "revoke", "unapprove", "save", "apply_to_waiting", "list_patterns", "review_counts"]
