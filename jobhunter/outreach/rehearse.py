"""The launch week, rehearsed on a COPY of the database — nothing live is touched.

`run_on_copy()` copies jobhunter.db with SQLite's backup API (WAL-safe while the tick
writes) to data/backups/, then runs `_simulate()` in a child process pointed at the copy
(ZONULY_DB_PATH), with:

    * a simulated clock (every module's "now" patched): Mon → the next Tue by default
    * send_mode forced to dryrun, Gmail's SMTP patched to refuse, the outbox redirected to
      data/backups/…-outbox — so even with send_mode: gmail in config nothing can leave
    * the model patched out (reply classification and time extraction answer from rules),
      macOS notifications off
    * the week's batch: the real targets (outreach.batch.plan on the copy), drafted from a
      stub body and approved through sender.approve — the same checks the Queue runs
    * each sending day: the send agent's runs every 10 minutes through the window
      (sender.send_due with the warm-up quota and the jittered slots), then the day's
      injected events — a bounce (Mon), an opt-out (Tue), a "yes" with a time (Wed), a
      colleague answering on the thread from another address (Thu)
    * each morning 08:00: follow-ups for threads silent five days (followup.queue, the
      template), approved by the simulated user, threaded under the original

The result is one row per day: quota, cold / follow-up sent, guessed used, the slot times,
what happened — and the checks (cap never exceeded, guessed cap held, the bounced address
never retried, the opted-out company never written to again, follow-ups threaded).
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

from jobhunter import ROOT


def _backup(tag: str) -> Path:
    from jobhunter.db import DB_PATH

    dest = ROOT / "data" / "backups" / f"jobhunter-{datetime.now():%Y-%m-%d-%H%M%S}-{tag}.db"
    dest.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(DB_PATH, timeout=60)
    dst = sqlite3.connect(dest)
    with dst:
        src.backup(dst)
    dst.close()
    src.close()
    return dest


def run_on_copy(*, start: str = "2026-10-19", days: int = 9, keep: bool = True) -> dict:
    copy = _backup("rehearse-week")
    out_json = copy.with_suffix(".json")
    env = {**os.environ, "ZONULY_DB_PATH": str(copy)}
    code = ("import sys, json; from jobhunter.outreach import rehearse; "
            f"rehearse._simulate({start!r}, {int(days)}, {str(out_json)!r})")
    r = subprocess.run([sys.executable, "-c", code], env=env, cwd=str(ROOT), capture_output=True, text=True,
                       timeout=1800)
    if r.returncode != 0 or not out_json.exists():
        return {"error": (r.stderr or r.stdout)[-2000:], "copy": str(copy)}
    data = json.loads(out_json.read_text())
    data["copy"] = str(copy)
    if not keep:
        copy.unlink(missing_ok=True)
    return data


class _Clock:
    def __init__(self, t: datetime):
        self.t = t

    def local(self) -> datetime:
        return self.t

    def utc(self) -> datetime:
        off = datetime.now().astimezone().utcoffset() or timedelta(0)
        return self.t - off


def _simulate(start: str, days: int, out_path: str) -> None:   # noqa: C901 — one linear script
    import email as email_lib
    import email.policy
    import logging

    from sqlmodel import col, func, select

    from jobhunter import llm
    from jobhunter.db import DB_PATH, Company, Contact, Email, Event, get_session, init_db
    from jobhunter.outreach import (batch, drafter, followup, gmail, ledger, schedule, sender, suppress,
                                    tracker, warmup)

    logging.basicConfig(level=logging.WARNING)
    assert "backups" in str(DB_PATH), f"refusing to rehearse on {DB_PATH}"
    init_db()
    clock = _Clock(datetime.strptime(start, "%Y-%m-%d").replace(hour=8))

    # ---- nothing leaves, nothing pops up, no model
    outbox = Path(out_path).with_suffix("").parent / (Path(out_path).stem + "-outbox")
    outbox.mkdir(parents=True, exist_ok=True)
    sender.OUTBOX = outbox
    sender.SEND_MODE = "dryrun"

    def _refuse(*a, **k):
        raise RuntimeError("rehearsal: Gmail is off")
    gmail.send = _refuse
    gmail._smtp = _refuse
    schedule._notify = lambda *a, **k: None
    llm.chat_json = lambda *a, default=None, **k: default
    tracker.classify = lambda body, sender_: (
        {"sentiment": "positive", "reason": "offered a call", "action": "confirm the time"}
        if "happy to" in body.lower() else {"sentiment": "neutral", "reason": "holding reply", "action": "wait"})

    # ---- the clock, everywhere a module reads "now"
    for mod in (sender, suppress, followup, batch, tracker, drafter, schedule):
        if hasattr(mod, "utcnow"):
            mod.utcnow = clock.utc
    ledger._today = lambda: clock.local().strftime("%Y-%m-%d")
    _orig_window = sender.in_send_window
    sender.in_send_window = lambda now=None: _orig_window(now or clock.local())

    def _sent_today() -> int:
        mid = clock.local().replace(hour=0, minute=0, second=0, microsecond=0)
        off = datetime.now().astimezone().utcoffset() or timedelta(0)
        with get_session() as s:
            return s.exec(select(func.count()).select_from(Email).where(
                Email.status == "sent", col(Email.sent_at) >= mid - off)).one()
    sender.sent_today = _sent_today

    # ---- start clean: the copy's own pending queue is set aside (rehearsal mail only)
    with get_session() as s:
        for e in s.exec(select(Email).where(col(Email.status).in_(["draft", "approved"]))).all():
            e.status, e.error = "rejected", "set aside for the rehearsal"
            s.add(e)
        s.commit()

    # ---- the batch: real targets, a stub body, approved by the simulated user on Sunday
    sched = warmup.schedule(days, from_day=start)
    total = sum(d["quota"] for d in sched)
    plan = batch.plan(limit=total + 10, guessed_max=ledger.GUESSED_CAP * sum(1 for d in sched if d["send_day"]))
    clock.t = datetime.strptime(start, "%Y-%m-%d") - timedelta(hours=6)   # Sunday 18:00
    approved, refused = 0, []
    for t in plan["targets"][:total]:
        body = drafter._compose(
            f"I'm a final-year student who builds AI/infra tools, and I'd like to apply for {t['job'] or 'an engineering role'} "
            f"at {t['company']}. Would you be open to referring me, or pointing me to the right person? "
            "[rehearsal stub body]", t["person"])
        with get_session() as s:
            e = Email(contact_id=t["contact_id"], company_id=t["company_id"], job_id=t["job_id"], to_email=t["address"],
                      subject=f"Referral for {t['job'] or 'an engineering role'} at {t['company']}"[:180], body=body,
                      kind="cold", status="draft", address_confidence=t["address_confidence"],
                      created_at=clock.utc(), expires_at=clock.utc() + timedelta(days=7), review_flags="[]")
            s.add(e)
            s.commit()
            s.refresh(e)
            eid = e.id
        r = sender.approve(eid)
        if r.get("approved"):
            approved += 1
        else:
            refused.append(r.get("error"))
    with get_session() as s:
        opt_line_ok = all(suppress.OPT_OUT_LINE in e.body for e in s.exec(
            select(Email).where(Email.status == "approved", Email.kind == "cold")).all())

    rows: list[dict] = []
    checks = {"cap_never_exceeded": True, "guessed_cap_held": True, "bounced_not_retried": True,
              "opted_out_company_not_written_again": True, "followups_threaded": None,
              "opt_out_line_on_every_cold_mail": opt_line_ok, "approval_refusals": refused[:5]}
    bounced_addr = None
    opted_company = None
    for d in sched:
        day = datetime.strptime(d["day"], "%Y-%m-%d")
        row = {"day": d["day"], "weekday": d["weekday"], "quota": d["quota"], "cold": 0, "followups": 0,
               "guessed": 0, "slots": [], "events": [], "followups_drafted": 0}
        # 08:00 — follow-ups for silent threads, approved by the user over breakfast
        clock.t = day.replace(hour=8)
        f = followup.queue(use_model=False, now=clock.utc())
        row["followups_drafted"] = f["drafted"]
        for fid in f["ids"]:
            sender.approve(fid)
        if d["send_day"]:
            t = day.replace(hour=int(sender.SEND_WINDOW[0]))
            end = day.replace(hour=int(sender.SEND_WINDOW[1]))
            while t < end:
                clock.t = t
                res = sender.send_due(now=t, stagger=False)
                for r in res.get("results") or []:
                    if r.get("sent"):
                        with get_session() as s:
                            e = s.get(Email, r["email_id"])
                            row["followups" if e.kind != "cold" else "cold"] += 1
                            row["guessed"] += (e.address_confidence or "verified") != "verified" and e.kind == "cold"
                            row["slots"].append(t.strftime("%H:%M"))
                            if bounced_addr and e.to_email.lower() == bounced_addr:
                                checks["bounced_not_retried"] = False
                            if opted_company and e.company_id == opted_company:
                                checks["opted_out_company_not_written_again"] = False
                t += timedelta(minutes=10)
            led = ledger.status()
            if led["used"] > led["cap"] or led["used"] > d["quota"]:
                checks["cap_never_exceeded"] = False
            if led["guessed_used"] > ledger.GUESSED_CAP:
                checks["guessed_cap_held"] = False
            row["ledger"] = {"cap": led["cap"], "used": led["used"], "guessed_used": led["guessed_used"]}
        # 19:30 — what came back today
        clock.t = day.replace(hour=19, minute=30)
        with get_session() as s:
            todays = s.exec(select(Email).where(Email.status == "sent", Email.kind == "cold",
                                                col(Email.sent_at) >= clock.utc() - timedelta(hours=10))).all()
            todays = [(e.id, e.to_email, e.address_confidence, e.company_id) for e in todays]
        first_week = day < datetime.strptime(start, "%Y-%m-%d") + timedelta(days=5)
        if not first_week:
            todays = []
        if d["weekday"] == "Mon" and todays and not bounced_addr:
            pick = next((x for x in todays if (x[2] or "verified") != "verified"), todays[0])
            with get_session() as s:
                e = s.get(Email, pick[0])
                suppress.mark_bounced(s, e, "550 5.1.1 The email account that you tried to reach does not exist.")
                s.commit()
            bounced_addr = pick[1].lower()
            row["events"].append(f"bounce: {pick[1]} ({pick[2]}) — address taken off the contact, not retried")
        elif d["weekday"] == "Tue" and todays:
            pick = todays[0]
            r = tracker.record_reply(pick[0], "Please don't email me again, thanks.", sender=pick[1])
            opted_company = pick[3]
            row["events"].append(f"opt-out from {pick[1]}: company suppressed, {r.get('opted_out', {}).get('withdrawn', 0)} pending withdrawn")
        elif d["weekday"] == "Wed" and todays:
            pick = todays[0]
            r = tracker.record_reply(pick[0], "Happy to refer you. Can you do a quick call Friday at 4pm IST? "
                                              "https://meet.google.com/abc-defg-hij", sender=pick[1])
            row["events"].append(f"yes from {pick[1]}: {r['sentiment']}, events {len(r.get('events') or [])}")
        elif d["weekday"] == "Thu" and todays:
            pick = todays[0]
            dom = pick[1].split("@")[-1]
            r = tracker.record_reply(pick[0], "Forwarding this to our hiring manager, she will get back to you.",
                                     sender=f"Colleague <colleague@{dom}>")
            row["events"].append(f"reply on the thread from another address (colleague@{dom}): {r['sentiment']}")
        rows.append(row)

    # ---- follow-up threading: each follow-up's .eml answers its parent's Message-ID
    threaded = []
    with get_session() as s:
        for f in s.exec(select(Email).where(Email.kind == "followup", Email.status == "sent")).all():
            parent = s.get(Email, f.parent_email_id)
            eml = outbox / f"{f.id:05d}.eml"
            if not eml.exists() or parent is None:
                continue
            msg = email_lib.message_from_bytes(eml.read_bytes(), policy=email.policy.default)
            threaded.append(msg.get("In-Reply-To") == parent.gmail_message_id and
                            (msg.get("Subject") or "").lower().startswith("re:") and
                            f.gmail_thread_id == parent.gmail_thread_id)
        checks["followups_threaded"] = (all(threaded) if threaded else None)
        checks["followups_checked"] = len(threaded)
        opted = s.get(Company, opted_company) if opted_company else None
        checks["opted_company_flag"] = bool(opted and opted.do_not_contact)
        dead = s.exec(select(Contact).where(col(Contact.dead_email) == bounced_addr)).first() if bounced_addr else None
        checks["bounced_contact_has_no_address"] = bool(dead and not dead.email and dead.email_status == "bounced")
        checks["events"] = s.exec(select(func.count()).select_from(Event)).one()
        left = s.exec(select(func.count()).select_from(Email).where(Email.status == "approved")).one()
    out = {"start": start, "drafted": min(total, len(plan["targets"])), "approved": approved, "available": plan["available"],
           "ranked_by": plan["ranked_by"], "approved_left_at_end": left, "outbox": str(outbox), "days": rows,
           "checks": checks}
    Path(out_path).write_text(json.dumps(out, default=str, indent=1))


def table(out: dict) -> str:
    if out.get("error"):
        return f"rehearsal failed: {out['error']}"
    lines = [f"{'day':10} {'':3} {'quota':>5} {'cold':>4} {'f-up':>4} {'guess':>5}  first–last send  events",
             "-" * 96]
    for r in out["days"]:
        span = f"{r['slots'][0]}–{r['slots'][-1]}" if r["slots"] else "—"
        lines.append(f"{r['day']:10} {r['weekday']:3} {r['quota']:>5} {r['cold']:>4} {r['followups']:>4} "
                     f"{r['guessed']:>5}  {span:15}  {'; '.join(r['events'])[:120]}")
    lines.append("")
    lines.append("checks: " + ", ".join(f"{k}={v}" for k, v in out["checks"].items()))
    return "\n".join(lines)


__all__ = ["run_on_copy", "table"]
