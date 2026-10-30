"""Sending inboxes (2026-10-30): per-inbox ramp, verified-only domain inboxes, address checks,
a follow-up from the inbox its first mail left from."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlmodel import Session, SQLModel, create_engine

from jobhunter.db import Company, Contact, Email, utcnow
from jobhunter.outreach import inboxes, ledger, sender, verify_address, warmup

ZOHO = inboxes.Inbox(key="zoho-test", address="tejmul@tejmul.in", name="Tejmul Movin", smtp_host="smtp.zoho.in",
                     imap_host="imap.zoho.in", password_env="ZOHO_PASS_TEST", cap=30,
                     warmup={"first": 5, "step": 5, "every": 2})


@pytest.fixture()
def db(tmp_path, monkeypatch):
    path = tmp_path / "inboxes.db"
    engine = create_engine(f"sqlite:///{path}")
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(sender, "get_session", lambda: Session(engine))
    monkeypatch.setattr(sender, "init_db", lambda: None)
    monkeypatch.setattr(ledger, "DB_PATH", path)
    monkeypatch.setattr(ledger, "effective_cap", lambda day: 100)
    monkeypatch.setattr(sender, "OUTBOX", tmp_path / "outbox")
    monkeypatch.setattr(sender, "SEND_MODE", "dryrun")
    gmail_box = inboxes._gmail()
    monkeypatch.setattr(inboxes, "all_inboxes", lambda: [gmail_box, ZOHO])
    monkeypatch.setattr(inboxes, "sending", lambda live: [gmail_box, ZOHO])
    monkeypatch.setattr(sender, "in_send_window", lambda now=None: True)
    monkeypatch.setattr(inboxes, "due", lambda session, inbox, now=None: 5)
    return engine


def _people(engine, specs):
    """specs: [(company, person, confidence)] -> [(company_id, contact_id)]"""
    out = []
    with Session(engine) as s:
        for comp, person, conf in specs:
            c = Company(name=comp, domain=f"{comp.lower()}.com")
            s.add(c)
            s.commit()
            s.refresh(c)
            p = Contact(company_id=c.id, name=person, email=f"{person.split()[0].lower()}@{comp.lower()}.com",
                        source="github", confidence=conf, affiliation="verified-current",
                        affiliation_source="org-member", email_kind="company")
            s.add(p)
            s.commit()
            s.refresh(p)
            out.append((c.id, p.id))
    return out


def _email(engine, cid, pid, conf, **kw) -> int:
    with Session(engine) as s:
        p = s.get(Contact, pid)
        e = Email(contact_id=pid, company_id=cid, to_email=p.email, subject="Referral", body="Hi,\n\nAn ask.\n\nTejmul",
                  status="approved", address_confidence=conf, approved_at=utcnow() - timedelta(minutes=5), **kw)
        s.add(e)
        s.commit()
        s.refresh(e)
        return e.id


def test_a_new_inbox_ramps_from_its_first_send_and_never_passes_its_cap(db):
    with Session(db) as s:
        assert inboxes.quota(s, ZOHO, "2026-11-02") == 5             # no sends yet: the first step
        (cid, pid), = _people(db, [("Acme", "Priya Sharma", "verified")])
        eid = _email(db, cid, pid, "verified")
        e = s.get(Email, eid)
        e.sender_inbox, e.sent_at, e.status = ZOHO.key, datetime(2026, 11, 2, 6), "sent"   # Monday
        s.add(e)
        s.commit()
        assert inboxes.quota(s, ZOHO, "2026-11-03") == 5
        assert inboxes.quota(s, ZOHO, "2026-11-04") == 10
        assert inboxes.quota(s, ZOHO, "2026-11-06") == 15            # Fri: 4 sending days in
        assert inboxes.quota(s, ZOHO, "2027-01-04") == 30            # weeks later: its cap, never more


def test_verified_goes_from_the_domain_inbox_and_a_guess_only_from_the_gmail(db, monkeypatch):
    monkeypatch.setattr(verify_address, "state", lambda contact, addr, conf: "verified" if conf == "verified" else "guessed")
    (c1, p1), (c2, p2) = _people(db, [("Acme", "Priya Sharma", "verified"), ("Beta", "Kiran Das", "scraped")])
    good = _email(db, c1, p1, "verified")
    guess = _email(db, c2, p2, "scraped")
    out = sender.send_due(datetime(2026, 11, 2, 12), stagger=False)
    assert out["sent"] == 2
    with Session(db) as s:
        assert s.get(Email, good).sender_inbox == ZOHO.key
        assert s.get(Email, guess).sender_inbox == inboxes.GMAIL
    assert "X-ZoNuLy-Inbox: zoho-test" in (sender.OUTBOX / f"{good:05d}.eml").read_text()
    assert "From: Tejmul Movin <tejmul@tejmul.in>" in (sender.OUTBOX / f"{good:05d}.eml").read_text()


def test_a_checked_good_guess_may_go_from_the_domain_inbox(db, monkeypatch):
    monkeypatch.setattr(verify_address, "has_mail_server", lambda d: True)
    monkeypatch.setattr(verify_address, "configured", lambda: True)
    monkeypatch.setattr(verify_address, "millionverifier", lambda a: "ok")
    (cid, pid), = _people(db, [("Acme", "Priya Sharma", "scraped")])
    eid = _email(db, cid, pid, "scraped")
    out = sender.send_due(datetime(2026, 11, 2, 12), stagger=False)
    assert out["sent"] == 1
    with Session(db) as s:
        assert s.get(Email, eid).sender_inbox == ZOHO.key
        assert s.get(Contact, pid).email_status == "check:ok"      # paid for once, kept


def test_an_address_with_no_mail_server_is_never_sent(db, monkeypatch):
    monkeypatch.setattr(verify_address, "has_mail_server", lambda d: False)
    (cid, pid), = _people(db, [("Acme", "Priya Sharma", "pattern-guessed")])
    eid = _email(db, cid, pid, "pattern-guessed")
    r = sender.send_email(eid, ignore_window=True)
    assert "address check" in r["error"]
    with Session(db) as s:
        assert s.get(Email, eid).status == "failed"
        assert s.get(Email, eid).sent_at is None


def test_a_follow_up_leaves_from_the_inbox_its_first_mail_left_from(db):
    (cid, pid), = _people(db, [("Acme", "Priya Sharma", "verified")])
    first = _email(db, cid, pid, "verified")
    with Session(db) as s:
        e = s.get(Email, first)
        e.status, e.sent_at, e.sender_inbox = "sent", utcnow() - timedelta(days=6), ZOHO.key
        e.gmail_message_id = e.gmail_thread_id = "<abc@tejmul.in>"
        s.add(e)
        s.commit()
    fu = _email(db, cid, pid, "verified", kind="followup", parent_email_id=first)
    r = sender.send_email(fu, ignore_window=True, inbox=inboxes.GMAIL)   # asked for the Gmail: the thread wins
    assert r.get("sent") and r["inbox"] == ZOHO.key


def test_each_inbox_has_its_own_send_times():
    a = warmup.slots("2026-11-02", 5, window=(10, 19))
    b = warmup.slots("2026-11-02", 5, window=(10, 19), seed="zoho-test")
    assert a == warmup.slots("2026-11-02", 5, window=(10, 19), seed="gmail") and a != b
