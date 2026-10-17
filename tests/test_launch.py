"""Email launch: warm-up quota, bounces, opt-outs, the company cooldown, follow-up threading."""

from __future__ import annotations

import email as email_lib
import email.policy
import json
from datetime import datetime, timedelta

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from jobhunter import guardrails
from jobhunter.db import Company, Contact, Email, Reply, utcnow
from jobhunter.outreach import followup, ledger, sender, suppress, tracker, warmup


@pytest.fixture()
def db(tmp_path, monkeypatch):
    path = tmp_path / "launch.db"
    engine = create_engine(f"sqlite:///{path}")
    SQLModel.metadata.create_all(engine)
    for mod in (sender, followup, tracker):
        monkeypatch.setattr(mod, "get_session", lambda: Session(engine))
        monkeypatch.setattr(mod, "init_db", lambda: None)
    monkeypatch.setattr(ledger, "DB_PATH", path)
    monkeypatch.setattr(sender, "OUTBOX", tmp_path / "outbox")
    monkeypatch.setattr(sender, "SEND_MODE", "dryrun")
    monkeypatch.setattr(ledger, "effective_cap", lambda day: ledger.DAILY_CAP)
    return engine


def _world(engine, *, n_people: int = 2, confidence: str = "verified"):
    with Session(engine) as s:
        c = Company(name="Acme", domain="acme.com", website="https://acme.com")
        s.add(c)
        s.commit()
        s.refresh(c)
        ids = []
        for i, nm in enumerate(["Priya Sharma", "Rahul Verma", "Meena Iyer"][:n_people]):
            p = Contact(company_id=c.id, name=nm, email=f"{nm.split()[0].lower()}@acme.com", source="github",
                        confidence=confidence, affiliation="verified-current", affiliation_source="org-member",
                        email_kind="company", role_class="engineer", referral_rank=3)
            s.add(p)
            s.commit()
            s.refresh(p)
            ids.append(p.id)
        return c.id, ids


def _email(engine, cid, pid, *, status="approved", kind="cold", sent_days_ago=None, conf="verified", **kw) -> int:
    with Session(engine) as s:
        p = s.get(Contact, pid)
        e = Email(contact_id=pid, company_id=cid, to_email=p.email or "x@acme.com", subject="Referral at Acme",
                  body="Hi Priya,\n\nA short ask.\n\nThanks,\nTejmul", kind=kind, status=status,
                  address_confidence=conf, approved_at=utcnow() - timedelta(minutes=5), **kw)
        if sent_days_ago is not None:
            e.sent_at = utcnow() - timedelta(days=sent_days_ago)
        s.add(e)
        s.commit()
        s.refresh(e)
        return e.id


# ------------------------------------------------------------------ warm-up

def test_warmup_ramps_from_5_to_25_over_two_weeks_and_never_above_the_cap(monkeypatch):
    monkeypatch.setattr(warmup, "_W", {"start": "2026-10-19", "first": 5, "step": 5, "every": 2})
    monkeypatch.setattr(warmup, "DAILY_CAP", 25)        # the ramp's shape, independent of the live cap
    days = [d for d in warmup.schedule(14, from_day="2026-10-19") if d["send_day"]]
    assert [d["quota"] for d in days] == [5, 5, 10, 10, 15, 15, 20, 20, 25, 25]
    assert warmup.quota("2026-10-24") == 15 and not warmup.is_send_day("2026-10-24")   # Saturday
    assert warmup.quota("2026-12-01") == 25 and warmup.quota("2026-12-01", cap=10) == 10
    assert warmup.quota("2026-10-15") == 5          # before the start: the first step
    monkeypatch.setattr(warmup, "_W", {})
    assert warmup.quota("2026-10-19") == warmup.DAILY_CAP


def test_send_slots_are_spread_jittered_inside_the_window_and_stable_per_day():
    a = warmup.slots("2026-10-19", 10, window=(10, 19))
    assert a == warmup.slots("2026-10-19", 10, window=(10, 19)) != warmup.slots("2026-10-20", 10, window=(10, 19))
    assert len(a) == 10 and a == sorted(a)
    assert all(datetime(2026, 10, 19, 10) < t < datetime(2026, 10, 19, 19) for t in a)
    gaps = [(b - x).total_seconds() for x, b in zip(a, a[1:])]
    assert min(gaps) > 60                            # no burst


def test_the_ledger_cap_is_the_warm_up_quota(tmp_path, monkeypatch):
    path = tmp_path / "l.db"
    SQLModel.metadata.create_all(create_engine(f"sqlite:///{path}"))
    monkeypatch.setattr(ledger, "DB_PATH", path)
    monkeypatch.setattr(warmup, "quota", lambda d=None, cap=None: 3)
    assert [ledger.reserve(1)[0] for _ in range(4)] == [True, True, True, False]
    st = ledger.status()
    assert st["cap"] == 3 and st["left"] == 0 and st["guessed_cap"] == 3


def test_send_due_sends_only_what_the_slots_say_is_due(db, monkeypatch):
    cid, (p1, p2) = _world(db)
    _email(db, cid, p1)
    with Session(db) as s:          # a second company so the cooldown does not hold it
        c2 = Company(name="Beta", domain="beta.com")
        s.add(c2)
        s.commit()
        s.refresh(c2)
        q = Contact(company_id=c2.id, name="Kiran Das", email="kiran@beta.com", source="github", confidence="verified",
                    affiliation="verified-current", affiliation_source="org-member", email_kind="company")
        s.add(q)
        s.commit()
        s.refresh(q)
        cid2, q2 = c2.id, q.id
    _email(db, cid2, q2)
    monkeypatch.setattr(sender, "in_send_window", lambda now=None: True)
    monkeypatch.setattr(warmup, "due", lambda now=None, cap=None: 1)
    out = sender.send_due(datetime(2026, 10, 19, 12), stagger=False)
    assert out["sent"] == 1
    out = sender.send_due(datetime(2026, 10, 19, 12, 5), stagger=False)
    assert out["sent"] == 0 and out["skipped"] == "nothing due yet"


# ------------------------------------------------------------------ bounces

def test_a_bounce_kills_the_address_and_it_is_never_retried(db, monkeypatch):
    cid, (p1, _) = _world(db, confidence="pattern-guessed")
    eid = _email(db, cid, p1, status="sent", sent_days_ago=0, conf="pattern-guessed")
    other = _email(db, cid, p1, status="approved", kind="followup", parent_email_id=eid)
    with Session(db) as s:
        out = suppress.mark_bounced(s, s.get(Email, eid), "550 5.1.1 no such user")
        s.commit()
        p = s.get(Contact, p1)
        assert out["guessed"] and out["withdrawn"] == 1
        assert s.get(Email, eid).status == "failed" and s.get(Email, eid).error.startswith("bounced")
        assert s.get(Email, other).status == "rejected"
        assert p.email is None and p.dead_email == "priya@acme.com" and p.email_status == "bounced"
        # no writer stores it again: the guardrails refuse it, the pattern step skips it
        c = s.get(Company, cid)
        g = guardrails.vet_contact(s, company=c, name="Priya Sharma", email_addr="priya@acme.com", source="github")
        assert g["email"] is None
        assert suppress.blocked(s, contact=p, company=c)
        assert "bounced" in suppress.blocked(s, contact=None, company=c, to_email="priya@acme.com")


def test_the_bounce_brake_pauses_guessed_addresses(db, monkeypatch):
    monkeypatch.setattr(suppress, "BRAKE_MAX", 2)        # the rule, independent of the live setting
    cid, (p1, p2) = _world(db)
    ids = [_email(db, cid, pid, status="sent", sent_days_ago=1) for pid in (p1, p2)]
    with Session(db) as s:
        assert suppress.brake(s) is None
        for eid in ids:
            suppress.mark_bounced(s, s.get(Email, eid), "550")
        s.commit()
        assert "paused" in suppress.brake(s)


def test_a_failed_send_gives_the_guessed_slot_back_a_bounce_does_not(db, monkeypatch):
    cid, (p1, _) = _world(db, confidence="pattern-guessed")
    eid = _email(db, cid, p1, conf="pattern-guessed")

    def boom(*a, **k):
        raise OSError("smtp down")
    monkeypatch.setattr(sender, "_dryrun_send", boom)
    r = sender.send_email(eid, ignore_window=True)
    assert "smtp down" in r["error"] and ledger.status()["guessed_used"] == 0 and ledger.status()["used"] == 0


# ------------------------------------------------------------------ opt-outs

@pytest.mark.parametrize("text", ["Please don't email me again.", "unsubscribe", "Stop.", "Remove me from your list",
                                  "please do not contact me", "stop emailing me", "Kindly take me off this"])
def test_opt_out_phrases(text):
    assert suppress.is_opt_out(text)


@pytest.mark.parametrize("text", ["Happy to refer you, send me your resume", "Not hiring right now, sorry",
                                  "Don't hesitate to ping me next month", suppress.OPT_OUT_LINE or "x"])
def test_not_opt_outs(text):
    assert suppress.is_opt_out(text) is None


def test_an_opt_out_suppresses_the_person_and_the_company_forever(db, monkeypatch):
    cid, (p1, p2) = _world(db)
    eid = _email(db, cid, p1, status="sent", sent_days_ago=1)
    pending = _email(db, cid, p2, status="draft")
    monkeypatch.setattr(tracker, "classify", lambda *a: pytest.fail("an opt-out is not sent to the model"))
    r = tracker.record_reply(eid, "Please don't email me again.\n\nOn Mon, Tejmul wrote:\n> Hi", sender="priya@acme.com")
    assert r["sentiment"] == "negative" and r["opted_out"]["withdrawn"] == 1
    with Session(db) as s:
        assert s.get(Contact, p1).do_not_contact and s.get(Company, cid).do_not_contact
        assert s.get(Email, pending).status == "rejected"
        p2row = s.get(Contact, p2)
        assert not guardrails.is_ready(s.get(Contact, p1))
        assert "asked not to be contacted" in suppress.blocked(s, contact=p2row, company=s.get(Company, cid))
    # and nothing new to that company gets through approval
    later = _email(db, cid, p2, status="draft")
    assert "asked not to be contacted" in sender.approve(later)["error"]


def test_the_do_not_contact_list_in_config(monkeypatch):
    monkeypatch.setattr(suppress, "_DNC", {"emails": ["ceo@acme.com"], "domains": ["evil.io"], "companies": ["Beta Labs"]})
    assert suppress.config_block(email="CEO@acme.com")
    assert suppress.config_block(email="anyone@evil.io")
    assert suppress.config_block(company=Company(name="Beta Labs"))
    assert suppress.config_block(email="priya@acme.com", company=Company(name="Acme")) is None


def test_the_opt_out_line_is_added_once_above_the_sign_off(monkeypatch):
    monkeypatch.setattr(suppress, "OPT_OUT_LINE", "Reply stop and I won't write again.")
    body = "Hi Priya,\n\nA short ask.\n\nThanks,\nTejmul"
    once = suppress.with_opt_out(body)
    assert once == "Hi Priya,\n\nA short ask.\n\nReply stop and I won't write again.\n\nThanks,\nTejmul"
    assert suppress.with_opt_out(once) == once


# ------------------------------------------------------------------ cooldown

def test_one_ask_per_company_until_a_reply_or_30_days(db, monkeypatch):
    monkeypatch.setattr(suppress, "ASKS_PER_COMPANY", 1)
    cid, (p1, p2) = _world(db)
    first = _email(db, cid, p1, status="approved")
    second = _email(db, cid, p2, status="draft")
    r = sender.approve(second)
    assert "already approved" in r["error"]
    with Session(db) as s:
        e = s.get(Email, first)
        e.status, e.sent_at = "sent", utcnow() - timedelta(days=3)
        s.add(e)
        s.commit()
    assert "nobody has replied" in sender.approve(second)["error"]
    with Session(db) as s:
        s.add(Reply(email_id=first, from_addr="priya@acme.com", body="ask Rahul", sentiment="neutral"))
        s.commit()
    assert sender.approve(second).get("approved")
    with Session(db) as s:          # a follow-up belongs to the ask already made: not held by the cooldown
        assert suppress.cooldown(s, Email(contact_id=p1, company_id=cid, to_email="x", subject="", body="",
                                          kind="followup")) is None


def test_two_approved_asks_do_not_deadlock_the_older_one_goes(db, monkeypatch):
    monkeypatch.setattr(suppress, "ASKS_PER_COMPANY", 1)
    cid, (p1, p2) = _world(db)
    a = _email(db, cid, p1, status="approved")
    b = _email(db, cid, p2, status="approved")
    with Session(db) as s:
        s.get(Email, b).approved_at = utcnow()
        s.commit()
        assert suppress.cooldown(s, s.get(Email, a)) is None
        assert "already approved" in suppress.cooldown(s, s.get(Email, b))


# ------------------------------------------------------------------ follow-ups

def test_a_follow_up_is_drafted_once_after_five_silent_days_and_threads_under_the_original(db, monkeypatch):
    cid, (p1, _) = _world(db)
    parent = _email(db, cid, p1, status="sent", sent_days_ago=6, gmail_message_id="<abc@mail.gmail.com>",
                    gmail_thread_id="dryrun:1")
    young = _email(db, cid, p1, status="sent", sent_days_ago=2, gmail_message_id="<young@x>")   # not due yet
    out = followup.queue(use_model=False)
    assert out["drafted"] == 1 and out["template"] == 1
    assert followup.queue(use_model=False)["drafted"] == 0          # exactly one
    with Session(db) as s:
        f = s.exec(select(Email).where(Email.kind == "followup")).one()
        assert f.status == "draft" and f.parent_email_id == parent and f.subject.startswith("Re: ")
        assert f.gmail_thread_id == "dryrun:1" and "last time" in f.body
        assert json.loads(f.review_flags)[0]["kind"] == "template"
        fid = f.id
    assert young
    assert sender.approve(fid).get("approved")
    r = sender.send_email(fid, ignore_window=True)
    assert r.get("sent"), r
    msg = email_lib.message_from_bytes(open(r["path"], "rb").read(), policy=email.policy.default)
    assert msg["In-Reply-To"] == "<abc@mail.gmail.com>" and msg["References"] == "<abc@mail.gmail.com>"
    with Session(db) as s:
        assert s.get(Email, fid).gmail_thread_id == "dryrun:1"


def test_two_follow_ups_when_configured_each_new_and_only_the_last_says_so(db, monkeypatch):
    from jobhunter.outreach import drafter

    monkeypatch.setattr(followup, "MAX_FOLLOWUPS", 2)
    monkeypatch.setattr(drafter, "get_session", followup.get_session)
    cid, (p1, _) = _world(db)
    contract = {"proofs": [{"proof": "fine-tuned Mistral 7B, lifting accuracy from 20% to 92%"},
                           {"proof": "cut AI response latency by 88% with SSE streaming"}]}
    parent = _email(db, cid, p1, status="sent", sent_days_ago=12, contract=json.dumps(contract))
    assert followup.queue(use_model=False)["drafted"] == 1
    with Session(db) as s:
        f1 = s.exec(select(Email).where(Email.kind == "followup")).one()
        assert "88%" in f1.body and "last time" not in f1.body       # new value, not the last one
        f1.status, f1.sent_at = "sent", utcnow() - timedelta(days=1)
        s.add(f1)
        s.commit()
    assert followup.queue(use_model=False)["drafted"] == 0          # five quiet days after the first follow-up
    with Session(db) as s:
        f1 = s.exec(select(Email).where(Email.kind == "followup")).one()
        f1.sent_at = utcnow() - timedelta(days=6)
        s.add(f1)
        s.commit()
    assert followup.queue(use_model=False)["drafted"] == 1
    with Session(db) as s:
        f2 = s.exec(select(Email).where(Email.kind == "followup").order_by(Email.id.desc())).first()
        assert f2.parent_email_id == parent and "last time" in f2.body and "resume" in f2.body
        f2.status, f2.sent_at = "sent", utcnow() - timedelta(days=9)
        s.add(f2)
        s.commit()
    assert followup.queue(use_model=False)["due"] == 0              # never a third


def test_a_legacy_opt_out_line_counts_and_is_not_an_opt_out(monkeypatch):
    old = suppress.LEGACY_OPT_OUT_LINES[0]
    body = f"Hi Priya,\n\nA short ask.\n\n{old}\n\nThanks,\nTejmul"
    assert suppress.with_opt_out(body) == body and suppress.has_opt_out(body)
    assert suppress.is_opt_out(f"Sure, happy to help.\n\n{old}") is None


def test_no_follow_up_after_a_reply_a_bounce_or_an_opt_out(db):
    cid, (p1, p2) = _world(db)
    replied = _email(db, cid, p1, status="sent", sent_days_ago=6)
    with Session(db) as s:
        s.add(Reply(email_id=replied, from_addr="priya@acme.com", body="thanks", sentiment="neutral"))
        s.commit()
    _email(db, cid, p2, status="failed", sent_days_ago=6, error="bounced: 550")
    assert followup.queue(use_model=False)["due"] == 0


def test_a_live_follow_up_never_answers_a_dry_run_thread(db, monkeypatch):
    cid, (p1, _) = _world(db)
    parent = _email(db, cid, p1, status="sent", sent_days_ago=6, gmail_thread_id="dryrun:9",
                    gmail_message_id="<dryrun-9-x@zonuly.local>")
    fid = _email(db, cid, p1, status="approved", kind="followup", parent_email_id=parent)
    monkeypatch.setattr(sender, "SEND_MODE", "gmail")
    with Session(db) as s:
        assert "dry run" in sender._referrer_problem(s, s.get(Email, fid))
        assert followup.due(s) == []


def test_asks_per_company_lets_two_people_at_one_company_go_the_same_day(db, monkeypatch):
    monkeypatch.setattr(suppress, "ASKS_PER_COMPANY", 2)
    cid, (p1, p2) = _world(db)
    a = _email(db, cid, p1, status="approved")
    b = _email(db, cid, p2, status="draft")
    with Session(db) as s:
        assert suppress.cooldown(s, s.get(Email, b)) is None          # the second of two may go
