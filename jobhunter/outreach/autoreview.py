"""The review gate, run by rules instead of a person (2026-10-26).

Tejmul: "from now i am gonna remove the approval gate and claude will approve from now on …
if the email are not good … it will find the bug in the system and change it — it should be
a loop". So a cold draft no longer waits for a click: this pass reads each one the way the
review queue was read by hand and either approves it or rejects it with the reason written
on the row (`error`, prefixed "auto-review:"). Nothing is sent here — the sender still sends
one mail per run inside the cap, the gaps and the per-domain limit.

The loop: every rejection is a line in logs/autoreview.log. A rejection means the system
made a draft it should not have (a senior role picked, a broken template, an address it
should not trust); the next session reads the log, fixes the stage that made it, and the
contact can be drafted again.

What a draft must pass, all of it:
    the sender's own checks   a confirmed-current referrer, nobody suppressed, the cooldowns
    a real address            never a pattern guess
    the composer's quality    outreach.autoreview.min_quality and no blocking flag
                              (faithfulness, uncovered claim, AI tell, incomplete, near-copy)
    a fresher role            not senior / lead / staff / manager, not 3+ years asked
    the letter itself         a greeting by first name, the current resume link, no
                              unfilled template slot, no Zonuly counts

Layering: outreach layer — db, guardrails and the other outreach modules below; shortlist above.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path

from sqlmodel import col, select

from jobhunter import CONFIG
from jobhunter.db import Contact, Email, get_session, init_db, utcnow

log = logging.getLogger(__name__)

_A = (CONFIG.get("outreach") or {}).get("autoreview") or {}
ENABLED = bool(_A.get("enabled", True))
MIN_QUALITY = int(_A.get("min_quality", 75))
MAX_SAME = float(_A.get("max_near_duplicate", 0.88))
START = str(_A.get("start", "2026-10-26T19:00"))   # "today is ok, don't make any changes in today"
LOG = Path(__file__).resolve().parents[2] / "logs" / "autoreview.log"
BY = "auto-review (rules, on Tejmul's instruction 2026-10-26)"

_BLOCKING = {"faithfulness", "uncovered_claim", "ai_tell", "incomplete"}
_SENIOR = re.compile(r"\b(senior|sr\.?|lead|principal|staff|architect|manager|head|director|vp|"
                     r"mid[- ]?level|ii|iii|iv|l[4-9]|sde[- ]?(2|3|ii|iii))\b", re.I)
_YEARS = re.compile(r"\b(\d{1,2})\s*\+?\s*(?:-|–|to)?\s*(?:\d{1,2}\s*)?\+?\s*(?:years?|yrs?)\b", re.I)
_SLOT = re.compile(r"\{[a-z_]+\}|\[\[|\]\]|<[A-Z_]+>")
_RESUME = next((re.search(r"https?://\S+", str(x)).group(0) for x in
                ((CONFIG.get("outreach") or {}).get("user_links") or [])
                if str(x).lower().startswith("resume") and re.search(r"https?://\S+", str(x))), None)


def _years_asked(text: str) -> int:
    """The smallest experience floor a posting states ("3+ years", "2-4 yrs"), or 0."""
    floors = [int(m.group(1)) for m in _YEARS.finditer(text or "") if int(m.group(1)) < 30]
    return min(floors) if floors else 0


def problems(session, e: Email) -> list[str]:
    """Why this draft may not go, or [] when it may."""
    from jobhunter.db import Job
    from jobhunter.outreach import sender

    out: list[str] = []
    why = sender._referrer_problem(session, e)
    if why:
        out.append(why)
    if (e.address_confidence or "verified") == "pattern-guessed":
        out.append("the address is a pattern guess")
    if e.expires_at and e.expires_at < utcnow():
        out.append("the draft is stale")
    if (e.quality or 0) < MIN_QUALITY:
        out.append(f"quality {e.quality} < {MIN_QUALITY}")
    for f in json.loads(e.review_flags or "[]"):
        kind = f.get("kind")
        if kind in _BLOCKING:
            out.append(f"flag {kind}: {f.get('detail', '')[:80]}")
        if kind == "near_duplicate":
            m = re.search(r"(\d+)% the same", f.get("detail", ""))
            if m and int(m.group(1)) / 100 > MAX_SAME:
                out.append(f"near-copy of mail already sent ({m.group(1)}%)")
    job = session.get(Job, e.job_id) if e.job_id else None
    if job is not None:
        if job.is_senior or _SENIOR.search(job.title or ""):
            out.append(f"not a fresher role: {job.title}")
        elif _years_asked(job.description or "") >= 3:
            out.append(f"the posting asks {_years_asked(job.description or '')}+ years: {job.title}")
    body = e.body or ""
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
    """Approve every cold draft that passes, reject every one that does not."""
    from jobhunter.outreach import sender

    if not ENABLED:
        return {"skipped": "outreach.autoreview.enabled is off"}
    now = now or datetime.now()
    if now < datetime.fromisoformat(START):
        return {"skipped": f"auto-review starts {START}"}
    init_db()
    out = {"approved": [], "rejected": []}
    with get_session() as s:
        drafts = s.exec(select(Email).where(Email.status == "draft", Email.kind == "cold")
                        .order_by(col(Email.created_at)).limit(limit)).all()
        verdicts = [(e.id, problems(s, e)) for e in drafts]
    lines = []
    for eid, why in verdicts:
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
                out["rejected"].append({"email_id": eid, "why": [r["error"]]})
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


__all__ = ["run", "problems", "MIN_QUALITY"]
