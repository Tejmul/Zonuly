"""The notification bot (notify_bot), the watchdog, and the tick fixes that came with them.
No mail leaves: SMTP and IMAP are never opened; dry runs write into tmp_path."""

from __future__ import annotations

import fcntl
import json
import os
import sqlite3
from datetime import datetime, timedelta
from email.message import EmailMessage

import pytest
from sqlmodel import Session, SQLModel, create_engine

from jobhunter import notify_bot, tick, watchdog
from jobhunter.db import Company, Contact, Email, Reply, utcnow

ME = "me@example.com"


@pytest.fixture()
def bot(tmp_path, monkeypatch):
    eng = create_engine(f"sqlite:///{tmp_path / 'j.db'}")
    SQLModel.metadata.create_all(eng)
    monkeypatch.setattr(notify_bot, "get_session", lambda: Session(eng))
    monkeypatch.setattr(notify_bot, "STATE_FILE", tmp_path / "notify_state.json")
    monkeypatch.setattr(notify_bot, "OUTBOX", tmp_path / "outbox")
    monkeypatch.setattr(notify_bot, "EMAIL_TO", ME)
    monkeypatch.setattr(notify_bot, "_mode", lambda: "dryrun")
    monkeypatch.setattr(notify_bot, "SEND_IN_DRYRUN", False)   # dryrun → .eml, never SMTP
    monkeypatch.setattr(notify_bot, "_macos", lambda *a, **k: None)
    monkeypatch.setattr(tick, "last", lambda kind: {"finished": "2026-10-16T08:00:00"})
    sent: list[EmailMessage] = []

    def fake_smtp(msg):
        sent.append(msg)
        return {"to": ME}

    monkeypatch.setattr(notify_bot, "_smtp_send", fake_smtp)
    return {"engine": eng, "tmp": tmp_path, "smtp": sent}


def _emls(bot) -> list[str]:
    d = bot["tmp"] / "outbox"
    return sorted(p.read_text() for p in d.glob("*.eml")) if d.exists() else []


# ------------------------------------------------------------------ commands

def test_parse_commands_lines_case_and_quotes():
    text = ("Approve 12 14\nreject #15, 16\n  PAUSE  \nthanks!\nstatus\n\n"
            "On Fri, 16 Oct 2026 at 08:00, ZoNuLy <me@example.com> wrote:\n> approve 99\n")
    assert notify_bot.parse_commands(text) == [("approve", [12, 14]), ("reject", [15, 16]), ("pause", []),
                                               ("status", [])]


def test_parse_commands_stops_at_wrapped_attribution_and_signature():
    assert notify_bot.parse_commands("resume\nOn Fri, 16 Oct 2026 at 08:00, ZoNuLy\n<me@x> wrote:\napprove 1") == [
        ("resume", [])]
    assert notify_bot.parse_commands("status\n-- \napprove 3") == [("status", [])]
    # the help text's example lines are not commands even if the quoting is lost
    assert notify_bot.parse_commands(notify_bot.HELP) == []


def _reply(*, frm=ME, in_reply_to=None, body="approve 7\n", bot_header=False, mid="<r1@mail.gmail.com>", auth=None):
    m = EmailMessage()
    m["From"] = f"Tejmul <{frm}>"
    m["To"] = ME
    m["Subject"] = "Re: [ZoNuLy] Daily digest [ZN-abc123]"
    m["Message-ID"] = mid
    if in_reply_to:
        m["In-Reply-To"] = in_reply_to
    if bot_header:
        m[notify_bot.BOT_HEADER] = "confirm"
    if auth:
        m["Authentication-Results"] = auth
    m.set_content(body)
    return m


def test_accept_only_his_replies_to_bot_mail(bot):
    ids = {"<znabc@gmail.com>"}
    assert notify_bot.accept(_reply(in_reply_to="<znabc@gmail.com>"), ids, set()) is None
    assert notify_bot.accept(_reply(in_reply_to="<znabc@gmail.com>", frm="evil@x.com"), ids, set()) == \
        "not from the configured address"
    assert notify_bot.accept(_reply(in_reply_to="<other@gmail.com>"), ids, set()) == "not a reply to a bot mail"
    assert notify_bot.accept(_reply(in_reply_to="<znabc@gmail.com>", bot_header=True), ids, set()) == \
        "the bot's own mail"
    assert notify_bot.accept(_reply(in_reply_to="<znabc@gmail.com>"), ids, {"<r1@mail.gmail.com>"}) == \
        "already handled"
    assert notify_bot.accept(_reply(in_reply_to="<znabc@gmail.com>", auth="mx.google.com; dmarc=fail"), ids,
                             set()) == "failed authentication"


def test_commands_use_the_dashboard_functions_and_confirm_once(bot, monkeypatch):
    from jobhunter.outreach import sender

    calls = []
    monkeypatch.setattr(sender, "approve", lambda i, **k: calls.append(("approve", i)) or {"approved": True})
    monkeypatch.setattr(sender, "reject", lambda i: calls.append(("reject", i)) or {"error": "cannot reject an email that was already sent"})
    first = notify_bot.test_mail()            # a bot mail to answer (.eml in dryrun)
    assert first["dryrun"]
    msg = _reply(in_reply_to=first["message_id"], body="approve 12 14\nreject 15\n")
    out = notify_bot.handle_commands([msg])
    assert out["commands"] == 2
    assert calls == [("approve", 12), ("approve", 14), ("reject", 15)]
    confirm = [e for e in _emls(bot) if "X-ZoNuLy-Bot: confirm" in e]
    assert len(confirm) == 1
    assert "approve #12: done" in confirm[0] and "reject #15: NOT done" in confirm[0]
    assert f"In-Reply-To: {msg['Message-ID']}" in confirm[0]
    # the same message again: nothing runs twice
    assert notify_bot.handle_commands([msg])["commands"] == 0
    assert len(calls) == 3


def test_pause_and_resume_gate_the_send_tick(bot, monkeypatch):
    from jobhunter.outreach import sender

    notify_bot.execute([("pause", [])])
    assert notify_bot.is_paused()
    monkeypatch.setattr(sender, "send_due", lambda: pytest.fail("sent while paused"))
    r = tick.run_send()
    assert r["sent"] == 0 and "paused" in r["skipped"]
    notify_bot.execute([("resume", [])])
    assert notify_bot.is_paused() is None
    monkeypatch.setattr(sender, "send_due", lambda: {"sent": 0, "skipped": "nothing due"})
    assert tick.run_send()["skipped"] == "nothing due"


# ------------------------------------------------------------------ delivery + kinds

def test_dryrun_writes_eml_and_send_in_dryrun_uses_smtp(bot, monkeypatch):
    r = notify_bot.urgent("k1", "tick stuck", "detail")
    assert r["dryrun"] and not bot["smtp"]
    eml = _emls(bot)[0]
    assert "Subject: [ZoNuLy] URGENT: tick stuck [ZN-" in eml and f"To: {ME}" in eml
    monkeypatch.setattr(notify_bot, "SEND_IN_DRYRUN", True)
    r = notify_bot.urgent("k2", "db locked", "detail")
    assert r["sent"] and len(bot["smtp"]) == 1


def test_urgent_is_deduplicated_per_issue(bot):
    now = datetime(2026, 10, 16, 9, 0)
    assert notify_bot.urgent("stuck:free", "t", "d", now=now).get("dryrun")
    assert notify_bot.urgent("stuck:free", "t", "d", now=now + timedelta(hours=2))["skipped"]
    assert notify_bot.urgent("db-locked", "t", "d", now=now + timedelta(hours=2)).get("dryrun")
    assert notify_bot.urgent("stuck:free", "t", "d", now=now + timedelta(hours=7)).get("dryrun")


def test_forced_preview_does_not_swallow_the_real_alert(bot):
    notify_bot.urgent("x", "t", "d", force_dry=True)
    assert notify_bot.urgent("x", "t", "d").get("dryrun")   # not "already told"


def test_audit_alerts_only_emergencies(bot):
    assert notify_bot.audit_alerts(["CHANNEL: jina failed 9 of 10 calls in the last hour"]) == []
    out = notify_bot.audit_alerts(["STALL: the tick ran in the last hour but nothing new was written",
                                   "CHANNEL: web search failed 10 of 10 calls in the last hour across a, b",
                                   "CHANNEL: jina failed 9 of 10 calls in the last hour"])
    assert [r["key"] for r in out] == ["audit:stall", "audit:search-down", "audit:channels"]


def _world(eng, *, sentiment="positive", age_h=1, name="Acme AI"):
    with Session(eng) as s:
        co = Company(name=name)
        s.add(co)
        s.commit()
        ct = Contact(company_id=co.id, name="Priya Sharma", role="Staff Engineer", source="linkedin-public",
                     email="priya@acme.ai")
        s.add(ct)
        s.commit()
        em = Email(contact_id=ct.id, company_id=co.id, to_email="priya@acme.ai", subject="Referral for AI Engineer?",
                   body="Hi Priya, I built an agentic GTM system...", status="replied", sent_at=utcnow() - timedelta(days=2))
        s.add(em)
        s.commit()
        s.add(Reply(email_id=em.id, gmail_message_id=f"g{age_h}{sentiment}", from_addr="Priya <priya@acme.ai>",
                    body="Happy to refer you! Send me your resume.\n\nOn Tue, Tejmul wrote:\n> Hi Priya",
                    sentiment=sentiment, sentiment_reason="offers a referral | next: send the resume",
                    received_at=utcnow() - timedelta(hours=age_h)))
        s.commit()
        return em.id


def test_positive_reply_mailed_once_with_context(bot):
    _world(bot["engine"])
    _world(bot["engine"], age_h=100, name="Old Co")     # older than the lookback: no backlog mail
    _world(bot["engine"], sentiment="negative", name="No Co")
    assert notify_bot.email_positive_replies()["mailed"] == 1
    eml = _emls(bot)[0]
    assert "Positive reply: Priya Sharma at Acme AI" in eml
    assert "Happy to refer you" in eml and "Hi Priya, I built" in eml and "send the resume" in eml
    assert "> Hi Priya" not in eml.split("What they wrote:")[1].split("Why it")[0]   # quoted original stripped
    assert notify_bot.email_positive_replies()["mailed"] == 0


def test_digest_once_a_day_after_the_hour(bot, monkeypatch):
    monkeypatch.setattr(notify_bot, "DIGEST_HOUR", 8)
    eid = _world(bot["engine"])
    with Session(bot["engine"]) as s:
        d = Email(contact_id=1, company_id=1, to_email="a@acme.ai", subject="Hello there", body="x", status="draft",
                  quality=88)
        s.add(d)
        s.commit()
        did = d.id
    from jobhunter import audit

    monkeypatch.setattr(watchdog, "check", lambda *a, **k: [{"key": "stuck:free", "title": "free job stuck for 300 min",
                                                             "detail": ""}])
    monkeypatch.setattr(audit, "latest", lambda: {"last": {"at": "2026-10-16T08:00", "alerts": []}})
    assert notify_bot.daily_digest(datetime(2026, 10, 16, 7, 0))["skipped"]
    assert notify_bot.daily_digest(datetime(2026, 10, 16, 8, 5)).get("dryrun")
    assert notify_bot.daily_digest(datetime(2026, 10, 16, 12, 0))["skipped"]
    eml = _emls(bot)[0]
    assert f"#{did}" in eml and "DRAFTS WAITING FOR YOUR REVIEW: 1" in eml and "free job stuck" in eml
    assert eid


def test_cycle_off_unless_enabled(bot, monkeypatch):
    monkeypatch.setattr(notify_bot, "ENABLED", False)
    assert notify_bot.cycle() == {"skipped": "notify.email is off"}
    out = notify_bot.cycle(force=True, force_dry=True, issues=[])
    assert out["commands"]["skipped"] and "positive" in out


# ------------------------------------------------------------------ watchdog

@pytest.fixture()
def dog(tmp_path, monkeypatch):
    lock, logf = tmp_path / "tick.lock", tmp_path / "tick-free.log"
    monkeypatch.setattr(watchdog, "JOBS", (("free", lock, logf),))
    monkeypatch.setattr(watchdog, "_LOG_OF", {"free": logf})
    monkeypatch.setattr(watchdog, "STATE_FILE", tmp_path / "wd.json")
    return lock, logf


def test_stuck_job_alerts_and_kills_only_when_asked(dog, monkeypatch):
    lock, logf = dog
    now = datetime(2026, 10, 16, 9, 0)
    lock.write_text(f"4242 {(now - timedelta(hours=7)).isoformat()}\n")
    logf.write_text("x")
    old = (now - timedelta(hours=6)).timestamp()
    os.utime(logf, (old, old))
    monkeypatch.setattr(watchdog, "_proc", lambda pid: {"cpu": 0.0, "state": "S", "etime": "07:00:00",
                                                        "command": "python scripts/run.py tick run --kind free"})
    killed = []
    monkeypatch.setattr(watchdog.os, "kill", lambda pid, sig: killed.append(pid))
    issues = watchdog.stuck_jobs(now, kill=False)
    assert [i["key"] for i in issues] == ["stuck:free"] and "blocked waiting" in issues[0]["detail"]
    assert killed == []
    watchdog.stuck_jobs(now, kill=True)
    assert killed == [4242]


def test_busy_job_with_a_moving_log_is_not_stuck(dog, monkeypatch):
    lock, logf = dog
    now = datetime.now()
    lock.write_text(f"4242 {(now - timedelta(minutes=70)).isoformat()}\n")
    logf.write_text("x")   # mtime: now
    monkeypatch.setattr(watchdog, "_proc", lambda pid: {"cpu": 50.0, "state": "R", "etime": "01:10:00",
                                                        "command": "python scripts/run.py tick run --kind free"})
    assert watchdog.stuck_jobs(now, kill=False) == []
    monkeypatch.setattr(watchdog, "_proc", lambda pid: None)   # holder gone: never stuck
    assert watchdog.stuck_jobs(now + timedelta(hours=9), kill=False) == []


def test_db_locked_alerts_only_after_the_threshold(dog, tmp_path):
    db = tmp_path / "x.db"
    holder = sqlite3.connect(str(db), timeout=0.1)
    holder.execute("create table t(a)")
    holder.commit()
    holder.execute("BEGIN IMMEDIATE")
    try:
        now = datetime(2026, 10, 16, 9, 0)
        assert watchdog.db_locked(now, wait_s=0.1, db_path=db) == []                 # first refusal remembered
        assert watchdog.db_locked(now + timedelta(minutes=3), wait_s=0.1, db_path=db) == []
        issues = watchdog.db_locked(now + timedelta(minutes=12), wait_s=0.1, db_path=db)
        assert [i["key"] for i in issues] == ["db-locked"]
    finally:
        holder.rollback()
        holder.close()
    assert watchdog.db_locked(datetime(2026, 10, 16, 9, 20), wait_s=0.1, db_path=db) == []
    assert "db_locked_since" not in json.loads((tmp_path / "wd.json").read_text())


def test_stale_job_respects_sleep(dog, monkeypatch):
    now = datetime(2026, 10, 16, 9, 0)
    monkeypatch.setattr(watchdog, "_STALE", {"referrals": 60})
    monkeypatch.setattr(watchdog, "_last_finished", lambda kind: now - timedelta(hours=3))
    assert [i["key"] for i in watchdog.stale_jobs(now, awake_since=now - timedelta(hours=5))] == ["stale:referrals"]
    assert watchdog.stale_jobs(now, awake_since=now - timedelta(minutes=10)) == []   # just woke up


# ------------------------------------------------------------------ tick fixes

def test_send_tick_does_not_wait_behind_the_free_tick(tmp_path, monkeypatch):
    monkeypatch.setattr(tick, "LOCK", tmp_path / "tick.lock")
    monkeypatch.setattr(tick, "SEND_LOCK", tmp_path / "send.lock")
    monkeypatch.setattr(tick, "LOGS", tmp_path)
    monkeypatch.setattr(tick, "_record", lambda *a, **k: None)
    monkeypatch.setattr(tick, "run_send", lambda: {"kind": "send", "sent": 0})
    monkeypatch.setattr(tick, "_notify_cycle", lambda: {"skipped": "off"})
    monkeypatch.setattr(tick._Lock.__init__, "__defaults__", (tmp_path / "tick.lock", "tick"))
    with open(tmp_path / "tick.lock", "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)      # a free tick is running
        r = tick.run("send")
    assert "skipped" not in r and r["notify"] == {"skipped": "off"}


def test_tick_record_stays_parseable_when_large(tmp_path, monkeypatch):
    eng = create_engine(f"sqlite:///{tmp_path / 'j.db'}")
    SQLModel.metadata.create_all(eng)
    import jobhunter.db as db

    monkeypatch.setattr(db, "get_session", lambda: Session(eng))
    tick._record("free", {"kind": "free", "finished": "2026-10-16T09:00:00", "huge": "x" * 9000, "n": 3})
    got = tick.last("free")
    assert got["finished"] == "2026-10-16T09:00:00" and got["truncated"] and "huge" not in got
