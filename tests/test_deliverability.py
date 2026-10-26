"""Deliverability (2026-10-23, BlockedCo: Gmail refused 9 of 24 asks as spam, 5.7.1).

The built message, the review gate's deliverability flags, and the holds a spam block puts
on the account, the company and the recipient domain. Nothing here sends anything."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlmodel import Session, SQLModel, create_engine

from jobhunter.db import Company, Contact, Email, utcnow
from jobhunter.outreach import gates, gmail, sender, suppress

BODY = ("Hi Alex,\n\nI'm Tejmul, a final-year CS & AI student at Newton School of Technology.\n\n"
        "I built three agentic systems at MVRX Labs and fine-tuned Mistral 7B at eWealth Tech.\n\n"
        "I saw that Acme is hiring for Agent Engineer, and I'd be grateful if you'd consider referring me. "
        "Here's [my resume](https://drive.google.com/file/d/abc/view). Completely understand if not.\n\n"
        "Thanks so much for your time,\nTejmul Movin\n[LinkedIn](https://www.linkedin.com/in/x/)")


# ------------------------------------------------------------------ the built message

def test_build_message_is_a_person_to_person_mail_with_no_library_fingerprints():
    msg = gmail.build_message("alex@acme.com", "Agent Engineer at Acme", BODY)
    raw = msg.as_string()
    assert msg.get_content_type() == "multipart/alternative"
    assert [p.get_content_type() for p in msg.iter_parts()] == ["text/plain", "text/html"]
    assert all(p.get_content_disposition() != "attachment" for p in msg.walk())
    assert msg["Message-ID"].endswith("@gmail.com>") and msg["Date"] and "Tejmul" in msg["From"]
    assert "List-Unsubscribe" not in raw and "===============" not in raw
    assert raw.count("MIME-Version") == 1
    plain = msg.get_body(("plain",)).get_content()
    assert "my resume <https://drive.google.com/file/d/abc/view>" in plain
    html = msg.get_body(("html",)).get_content()
    assert '<a href="https://drive.google.com/file/d/abc/view">my resume</a>' in html and "<img" not in html


# ------------------------------------------------------------------ the gate

def _kinds(flags):
    return [f["kind"] for f in flags]


def test_a_clean_first_mail_has_no_deliverability_flags():
    assert gates.deliverability("Agent Engineer at Acme", BODY, first_name="Alex", to_email="alex@acme.com") == []


def test_attachment_links_shortener_and_bare_greeting_are_flagged_never_rejected():
    body = "Hi,\n\nSee https://bit.ly/x and [a](https://a.com) and [b](https://b.com).\n\nThanks"
    flags = gates.deliverability("hello", body, first_name="Alex", attachments=1)
    text = " ".join(f["detail"] for f in flags)
    assert set(_kinds(flags)) == {"deliverability"}
    assert "attachment" in text and "3 links" in text and "bit.ly" in text and "no name" in text


def test_a_near_copy_into_the_same_domain_is_flagged_and_elsewhere_only_noted():
    other = BODY.replace("Hi Alex", "Hi Priya")
    same_dom = gates.deliverability("Agent Engineer at Acme", BODY, first_name="Alex", to_email="alex@acme.com",
                                    sent=[{"to": "priya@acme.com", "subject": "Agent Engineer at Acme", "body": other}])
    assert _kinds(same_dom) == ["deliverability", "deliverability"]       # near-copy + same subject
    assert "near-copies" in same_dom[0]["detail"] and "acme.com" in same_dom[1]["detail"]
    elsewhere = gates.deliverability("Agent Engineer at Acme", BODY, first_name="Alex", to_email="alex@acme.com",
                                     sent=[{"to": "priya@beta.ai", "subject": "x", "body": other}])
    assert _kinds(elsewhere) == ["near_duplicate"]
    assert gates.jaccard(gates.shingles(BODY), gates.shingles(other)) >= gates.SIMILAR_AT


# ------------------------------------------------------------------ the holds

@pytest.fixture()
def world(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'd.db'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        c = Company(name="BlockedCo AI", domain="blocked-co.example")
        s.add(c)
        s.commit()
        s.refresh(c)
        p = Contact(company_id=c.id, name="Alex P", email="alex@blocked-co.example", source="x", confidence="pattern-guessed")
        s.add(p)
        s.commit()
        s.refresh(p)
        return engine, c.id, p.id


def _mail(s, cid, pid, to, *, status, error=None, hours_ago=None):
    e = Email(contact_id=pid, company_id=cid, to_email=to, subject="s", body="b", kind="cold", status=status,
              error=error, sent_at=(utcnow() - timedelta(hours=hours_ago)) if hours_ago is not None else None)
    s.add(e)
    s.commit()
    s.refresh(e)
    return e


BLOCKED = "bounced: ** Message blocked **\n\nYour message to p1@blocked-co.example has been blocked."
NOT_FOUND = "bounced: ** Address not found **\n\nYour message wasn't delivered to x@blocked-co.example"


def test_spam_block_detection_tells_a_block_from_a_dead_address():
    assert suppress.is_spam_block(BLOCKED) and suppress.is_spam_block("Status: 5.7.1 Message rejected")
    assert not suppress.is_spam_block(NOT_FOUND)


def test_a_spam_block_pauses_everything_then_holds_the_domain(world):
    engine, cid, pid = world
    with Session(engine) as s:
        nxt = _mail(s, cid, pid, "alex@blocked-co.example", status="approved")
        blk = _mail(s, cid, pid, "p1@blocked-co.example", status="failed", error=BLOCKED, hours_ago=5)
        assert "pauses" in suppress.spam_hold(s, nxt)                       # inside spam_pause_hours
        blk.sent_at = utcnow() - timedelta(days=4)
        s.add(blk)
        s.commit()
        assert "cools down" in suppress.spam_hold(s, nxt)                   # the domain, for 21 days
        blk.sent_at = utcnow() - timedelta(days=suppress.SPAM_COOLDOWN_DAYS + 1)
        s.add(blk)
        s.commit()
        assert suppress.spam_hold(s, nxt) is None
        blk.error = NOT_FOUND                                               # a dead address is not a block
        blk.sent_at = utcnow() - timedelta(hours=1)
        s.add(blk)
        s.commit()
        assert suppress.spam_hold(s, nxt) is None


def test_per_domain_daily_cap_holds_the_fourth_mail_into_one_domain(world, monkeypatch):
    engine, cid, pid = world
    monkeypatch.setattr(suppress, "PER_DOMAIN_DAILY_CAP", 3)
    with Session(engine) as s:
        nxt = _mail(s, cid, pid, "alex@blocked-co.example", status="approved")
        for i in range(2):
            _mail(s, cid, pid, f"p{i}@blocked-co.example", status="sent", hours_ago=0.1)
        _mail(s, cid, pid, "q@other.ai", status="sent", hours_ago=0.1)
        assert suppress.domain_cap(s, nxt) is None
        _mail(s, cid, pid, "p9@blocked-co.example", status="sent", hours_ago=0.1)
        assert "blocked-co.example" in suppress.domain_cap(s, nxt)


def test_the_sender_holds_a_cold_mail_after_a_block_without_changing_its_status(world, monkeypatch, tmp_path):
    engine, cid, pid = world
    monkeypatch.setattr(sender, "SEND_MODE", "dryrun")             # never the real Gmail, even by accident
    monkeypatch.setattr(sender, "OUTBOX", tmp_path / "outbox")
    monkeypatch.setattr(sender, "get_session", lambda: Session(engine))
    monkeypatch.setattr(sender, "init_db", lambda: None)
    with Session(engine) as s:
        nxt = _mail(s, cid, pid, "alex@blocked-co.example", status="approved").id
        _mail(s, cid, pid, "p1@blocked-co.example", status="failed", error=BLOCKED, hours_ago=1)
    r = sender.send_email(nxt, ignore_window=True)
    assert "spam" in r["error"]
    with Session(engine) as s:
        assert s.get(Email, nxt).status == "approved"


def test_a_target_company_takes_focus_per_company_asks_and_nothing_else_changes(world, monkeypatch):
    """2026-10-26: a send day's target companies (focus.py) may take outreach.focus.per_company
    cold asks a day and outreach.focus.asks_per_company in all; a company that is not a target
    keeps asks_per_company."""
    import json
    from datetime import date

    from jobhunter.db import Setting

    engine, cid, pid = world
    monkeypatch.setattr(suppress, "ASKS_PER_COMPANY", 1)
    monkeypatch.setattr(suppress, "ASKS_OVERRIDES", {})
    monkeypatch.setattr(suppress, "FOCUS_PER_COMPANY", 3)
    with Session(engine) as s:
        for i in range(2):
            _mail(s, cid, pid, f"p{i}@blocked-co.example", status="sent", hours_ago=1)
        nxt = Email(contact_id=pid + 1000, company_id=cid, to_email="z@blocked-co.example", subject="s", body="b",
                    kind="cold", status="approved", approved_at=utcnow())
        s.add(nxt)
        s.commit()
        assert "open ask" in suppress.cooldown(s, nxt)                      # not a target: one ask
        today = date.today().isoformat()
        s.add(Setting(key=suppress.FOCUS_KEY + today, value=json.dumps({"targets": [{"company_id": cid}]})))
        s.commit()
        assert suppress.focus_companies(s, today) == {cid}
        assert suppress.cooldown(s, nxt) is None                             # a target: no waiting for replies
        monkeypatch.setattr(suppress, "FOCUS_GOAL", 3)
        _mail(s, cid, pid, "p8@blocked-co.example", status="sent", hours_ago=1)
        assert "open ask" in suppress.cooldown(s, nxt)                       # past the goal it waits


def test_a_send_lost_to_the_network_goes_again_and_a_refusal_does_not():
    """2026-10-26: "if an email gets missed it should wire up next" — DNS / TLS / timeouts are
    retried (MAX_RETRIES), a 5xx, a limit or a spam block never is."""
    from jobhunter.outreach import sender

    assert sender.transient("[Errno 8] nodename nor servname provided, or not known")
    assert sender.transient("[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol")
    assert sender.transient("421 4.7.0 Try again later")
    assert not sender.transient("550 5.4.5 Daily user sending limit exceeded")
    assert not sender.transient("bounced: ** Address not found **")
    assert sender.retries("retry 2: timed out") == 2 and sender.retries(None) == 0


def test_personal_mail_domains_are_not_one_company():
    from jobhunter.outreach import suppress

    e = Email(contact_id=1, company_id=1, to_email="someone@gmail.com", subject="s", body="b", kind="cold",
              status="approved")
    assert suppress.domain_cap(None, e) is None
