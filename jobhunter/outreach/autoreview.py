"""The review gate, run by rules instead of a person (2026-10-26).

Tejmul: "from now i am gonna remove the approval gate and claude will approve from now on …
if the email are not good … it will find the bug in the system and change it — it should be
a loop". So a cold draft no longer waits for a click: this pass reads each one the way the
review queue was read by hand and either approves it or rejects it with the reason written
on the row (`error`, prefixed "auto-review:"). Nothing is sent here — the sender still sends
one mail per run inside the cap, the gaps and the per-domain limit.

The loop: every rejection is a line in logs/autoreview.log. A rejection means the system
made a draft it should not have (a senior role picked, a broken template, an address it
should not trust); the next session reads the log and fixes the stage that made it. The
rejected person is not drafted again (focus.sendable), so the fix serves the people after.

What a draft must pass, all of it:
    the referrer              confirmed current, not suppressed (bounced, opted out, blocked).
                              A draft only held for now — another ask at the company still
                              open, a pause after a spam block — stays a draft for the next run
    a real address            never a pattern guess
    the composer's quality    outreach.autoreview.min_quality and no blocking flag
                              (faithfulness, uncovered claim, AI tell, incomplete, and the
                              spam signals: a near-copy or same subject into the same company)
    a fresher role            not senior / lead / staff / manager, not 3+ years asked
    the letter itself         a greeting by first name, the current resume link, no
                              unfilled template slot, no Zonuly counts

Layering: outreach layer — db, guardrails and the other outreach modules below; shortlist above.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path

from sqlmodel import col, select

from jobhunter import CONFIG
from jobhunter.db import DB_PATH, Contact, Email, get_session, init_db, utcnow

log = logging.getLogger(__name__)

_A = (CONFIG.get("outreach") or {}).get("autoreview") or {}
ENABLED = bool(_A.get("enabled", True))
MIN_QUALITY = int(_A.get("min_quality", 75))
START = str(_A.get("start", "2026-10-26T19:00"))   # "today is ok, don't make any changes in today"
# next to the live database's logs/; a run against a copy (ZONULY_DB_PATH) logs beside that copy,
# so a rehearsal never writes into the real log (2026-10-27: ten simulated days had)
LOG = (DB_PATH.parent / "autoreview.log") if os.environ.get("ZONULY_DB_PATH", "").strip() \
    else Path(__file__).resolve().parents[2] / "logs" / "autoreview.log"
BY = "auto-review (rules, on Tejmul's instruction 2026-10-26)"

# deliverability = a spam signal the gate found: a near-copy or the same subject into the same
# company's mail server, a bare greeting, a shortener, broken characters
_BLOCKING = {"faithfulness", "uncovered_claim", "ai_tell", "incomplete", "deliverability"}
_SENIOR = re.compile(r"\b(senior|sr\.?|lead|principal|staff|architect|manager|head|director|vp|"
                     r"mid[- ]?level|ii|iii|iv|l[4-9]|sde[- ]?(2|3|ii|iii))\b", re.I)
_YEARS = re.compile(r"\b(\d{1,2})\s*\+?\s*(?:-|–|to)?\s*(?:\d{1,2}\s*)?\+?\s*(?:years?|yrs?)\b", re.I)
_SLOT = re.compile(r"\{[a-z_]+\}|\[\[|\]\]|<[A-Z_]+>")
_RESUME = next((re.search(r"https?://\S+", str(x)).group(0) for x in
                ((CONFIG.get("outreach") or {}).get("user_links") or [])
                if str(x).lower().startswith("resume") and re.search(r"https?://\S+", str(x))), None)


_EXPERIENCE = re.compile(r"experience|exp\b|building|working|professional|industry|hands[- ]on|track record|"
                         r"\bas an?\b|\bin (a |an )?(software|backend|frontend|full[- ]?stack|ml|ai|data|production|"
                         r"engineering|development)|\bof (software|backend|engineering|development|coding|programming)", re.I)


def _years_asked(text: str) -> int:
    """The smallest experience floor a posting states ("3+ years of experience", "2-4 yrs
    building …"), or 0. A number of years with no experience word right after it ("founded
    5 years ago") is not a floor."""
    floors = [int(m.group(1)) for m in _YEARS.finditer(text or "")
              if int(m.group(1)) < 30 and _EXPERIENCE.search((text or "")[m.end():m.end() + 60])]
    return min(floors) if floors else 0


def _deliverability(session, e: Email) -> list[dict]:
    from datetime import timedelta

    from jobhunter.outreach import drafter, gates

    since = utcnow() - timedelta(days=30)
    rows = session.exec(select(Email).where(
        Email.kind == "cold", Email.contact_id != e.contact_id, Email.id != e.id, col(Email.created_at) >= since,
        col(Email.status).in_(["draft", "approved", "sending", "sent", "replied", "failed"]))).all()
    sent = [{"to": o.to_email, "subject": o.subject, "body": o.body, "at": o.sent_at} for o in rows
            if o.body and (o.status != "draft" or (o.created_at, o.id) < (e.created_at, e.id))]
    try:
        first = (json.loads(e.contract or "{}").get("first_name") or None)
    except (ValueError, AttributeError):
        first = None
    sig = drafter.direct_signature() if e.structure == "direct_intro" else drafter.cold_signature()
    return [f for f in gates.deliverability(e.subject or "", e.body or "", first_name=first, signature=sig,
                                            sent=sent, to_email=e.to_email) if f["kind"] == "deliverability"]


def role_problem(job) -> str | None:
    """Why this role is not one for a fresher (senior / lead / II, or 3+ years asked), or None.
    Also used to keep such companies out of the circle (focus.py)."""
    if job is None:
        return None
    if job.is_senior or _SENIOR.search(job.title or ""):
        return f"not a fresher role: {job.title}"
    years = _years_asked(job.description or "")
    if years >= 3:
        return f"the posting asks {years}+ years: {job.title}"
    return None


def waits(session, e: Email) -> str | None:
    """Why this draft may not go YET (another ask at the company still open, a pause after a
    spam block) — it stays a draft and is read again next run. None when nothing holds it."""
    from jobhunter.outreach import suppress

    return suppress.cooldown(session, e) or suppress.spam_hold(session, e)


def problems(session, e: Email) -> list[str]:
    """Why this draft may never go as written, or [] when it may."""
    from jobhunter import guardrails
    from jobhunter.db import Company, Job
    from jobhunter.outreach import suppress

    out: list[str] = []
    contact = session.get(Contact, e.contact_id)
    why = suppress.blocked(session, contact=contact, company=session.get(Company, e.company_id), to_email=e.to_email)
    if why:
        out.append(why)
    if contact is None:
        out.append("the contact this draft was written to no longer exists")
    elif not guardrails.is_ready(contact):
        out.append(f"{contact.name or e.to_email} is not a confirmed-current referrer "
                   f"({contact.affiliation or 'unchecked'}: {contact.affiliation_evidence or 'no evidence'})")
    if (e.address_confidence or "verified") == "pattern-guessed":
        out.append("the address is a pattern guess")
    if e.expires_at and e.expires_at < utcnow():
        out.append("the draft is stale")
    if (e.quality or 0) < MIN_QUALITY:
        out.append(f"quality {e.quality} < {MIN_QUALITY}")
    for f in json.loads(e.review_flags or "[]"):
        kind = f.get("kind")
        if kind in _BLOCKING and kind != "deliverability":
            out.append(f"flag {kind}: {f.get('detail', '')[:80]}")
    # the spam signals are read again NOW, against what is approved or out by now and the
    # drafts older than this one — not the flags stored when it was drafted. near_duplicate
    # (alike to mail at OTHER companies) is not blocking: with four wordings it is the normal
    # state, and each company's mail server only sees its own mail.
    for f in _deliverability(session, e):
        out.append(f"spam signal: {f['detail'][:100]}")
    why = role_problem(session.get(Job, e.job_id) if e.job_id else None)
    if why:
        out.append(why)
    body = e.body or ""
    try:
        first = (json.loads(e.contract or "{}").get("first_name") or "").strip()
    except (ValueError, AttributeError):
        first = ""
    if not first:
        contact = session.get(Contact, e.contact_id)
        first = ((contact.name or "").split() or [""])[0] if contact else ""
    if not first or not re.match(rf"^(hi|hello|hey|dear)\s+{re.escape(first)}\b", body.strip(), re.I):
        out.append("the greeting is not to their first name")
    if _RESUME and _RESUME not in body:
        out.append("the current resume link is missing")
    if _SLOT.search(body) or _SLOT.search(e.subject or ""):
        out.append("an unfilled template slot")
    if re.search(r"zonuly[^.\n]{0,60}\d", body, re.I):
        out.append("quotes Zonuly numbers")
    return out


def run(limit: int = 200, *, now: datetime | None = None) -> dict:
    """Approve every cold draft that passes, reject every one that never can, and leave the
    ones held for now (waits) as drafts."""
    from jobhunter.outreach import sender

    if not ENABLED:
        return {"skipped": "outreach.autoreview.enabled is off"}
    now = now or datetime.now()
    if now < datetime.fromisoformat(START):
        return {"skipped": f"auto-review starts {START}"}
    init_db()
    out = {"approved": [], "rejected": [], "waiting": []}
    with get_session() as s:
        drafts = s.exec(select(Email).where(Email.status == "draft", Email.kind == "cold")
                        .order_by(col(Email.created_at)).limit(limit)).all()
        verdicts = []
        for e in drafts:
            why = problems(s, e)
            hold = None if why else waits(s, e)
            verdicts.append((e.id, why, hold))
    lines = []
    for eid, why, hold in verdicts:
        if hold:
            out["waiting"].append({"email_id": eid, "why": hold})
            continue          # read again next run; not logged, or the log fills every 10 minutes
        if why:
            with get_session() as s:
                e = s.get(Email, eid)
                e.status, e.error = "rejected", "auto-review: " + "; ".join(why)
                s.add(e)
                s.commit()
            out["rejected"].append({"email_id": eid, "why": why})
        else:
            r = sender.approve(eid)
            if r.get("error"):
                out["waiting"].append({"email_id": eid, "why": r["error"]})
                continue
            with get_session() as s:
                e = s.get(Email, eid)
                e.approved_by = BY
                s.add(e)
                s.commit()
            out["approved"].append(eid)
        lines.append(json.dumps({"at": now.isoformat(timespec="seconds"), "email_id": eid,
                                 "verdict": "reject" if why else "approve", "why": why}))
    if lines:
        LOG.parent.mkdir(exist_ok=True)
        with LOG.open("a") as fh:
            fh.write("\n".join(lines) + "\n")
    log.info("auto-review: %d approved, %d rejected", len(out["approved"]), len(out["rejected"]))
    return out


__all__ = ["run", "problems", "waits", "role_problem", "MIN_QUALITY"]
