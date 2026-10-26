"""2026-10-26: replies found outside our threads, automatic review, the circle's daily share
and a send lost to the network. Nothing here reads a real mailbox or sends anything."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from jobhunter.db import Company, Contact, Email, Job, Reply, utcnow
from jobhunter.outreach import autoreview, gmail, ledger, sender, suppress, tracker

RESUME = "https://drive.google.com/file/d/RESUME/view"


@pytest.fixture()
def db(tmp_path, monkeypatch):
    path = tmp_path / "r.db"
    engine = create_engine(f"sqlite:///{path}")
    SQLModel.metadata.create_all(engine)
    for mod in (sender, tracker, autoreview):
        monkeypatch.setattr(mod, "get_session", lambda: Session(engine))
        monkeypatch.setattr(mod, "init_db", lambda: None)
    monkeypatch.setattr(ledger, "DB_PATH", path)
    monkeypatch.setattr(sender, "OUTBOX", tmp_path / "outbox")
    monkeypatch.setattr(sender, "SEND_MODE", "dryrun")
    monkeypatch.setattr(ledger, "effective_cap", lambda day: ledger.DAILY_CAP)
    monkeypatch.setattr(tracker, "classify", lambda body, sender: {"sentiment": "neutral", "reason": "r", "action": "a"})
    monkeypatch.setattr(autoreview, "LOG", tmp_path / "autoreview.log")
    monkeypatch.setattr(autoreview, "_RESUME", RESUME)
    monkeypatch.setattr(autoreview, "START", "2000-01-01T00:00")
    return engine


def _company(engine, name="Acme", domain="acme.com", people=("Priya Sharma", "Rahul Verma")):
    with Session(engine) as s:
        c = Company(name=name, domain=domain, website=f"https://{domain}")
        s.add(c)
        s.commit()
        s.refresh(c)
        ids = []
        for nm in people:
            p = Contact(company_id=c.id, name=nm, email=f"{nm.split()[0].lower()}@{domain}", source="github",
                        confidence="verified", affiliation="verified-current", affiliation_source="org-member",
                        email_kind="company", role_class="engineer", referral_rank=3)
            s.add(p)
            s.commit()
            s.refresh(p)
            ids.append(p.id)
        return c.id, ids


def _sent(engine, cid, pid, *, hours_ago=24, n=1):
    with Session(engine) as s:
        p = s.get(Contact, pid)
        e = Email(contact_id=pid, company_id=cid, to_email=p.email, subject="Referral at Acme", body="Hi",
                  kind="cold", status="sent", sent_at=utcnow() - timedelta(hours=hours_ago),
                  gmail_thread_id=f"{1000 + n}", gmail_message_id=f"<ours-{n}@gmail.com>")
        s.add(e)
        s.commit()
        s.refresh(e)
        return e.id


def _msg(mid, sender, *, hours_ago=1, refs="", auto=False, text="Thanks, please apply on our site."):
    when = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return {"id": mid, "from": sender, "date": format_datetime(when), "text": text, "is_bounce": False,
            "is_ours": False, "is_auto": auto, "refs": refs}


@pytest.fixture()
def mailbox(monkeypatch):
    box = {"threads": {}, "from": {}, "asked": []}
    monkeypatch.setattr(gmail, "AUTH", "app_password")
    monkeypatch.setattr(gmail, "configured", lambda: True)
    monkeypatch.setattr(gmail, "thread_messages", lambda tid, message_id=None: box["threads"].get(tid, []))

    def frm(who, since):
        box["asked"].append(who)
        return box["from"].get(who, [])
    monkeypatch.setattr(gmail, "messages_from", frm)
    return box


# ------------------------------------------------------------------ replies outside the thread

def test_a_colleague_answering_in_a_new_thread_is_recorded_on_the_mail_it_names(db, mailbox):
    cid, (p1, p2) = _company(db)
    first = _sent(db, cid, p1, hours_ago=48, n=1)
    second = _sent(db, cid, p2, hours_ago=24, n=2)
    # HR answers the FIRST mail (its References name it), from a new address, in a new thread
    mailbox["from"]["acme.com"] = [_msg("g1", "Sona <sona@acme.com>", refs="<x@outlook> <ours-1@gmail.com>")]
    out = tracker.poll()
    assert out["new_replies"] == 1 and mailbox["asked"] == ["acme.com"]          # one search per company
    with Session(db) as s:
        r = s.exec(select(Reply)).one()
        assert r.email_id == first and s.get(Email, first).status == "replied"
        assert s.get(Email, second).status == "sent"
    assert tracker.poll()["new_replies"] == 0                                    # recorded once


def test_company_mail_with_no_reference_goes_to_the_latest_mail_before_it(db, mailbox):
    cid, (p1, p2) = _company(db)
    _sent(db, cid, p1, hours_ago=48, n=1)
    second = _sent(db, cid, p2, hours_ago=24, n=2)
    mailbox["from"]["acme.com"] = [_msg("g1", "hr@acme.com", hours_ago=2)]
    tracker.poll()
    with Session(db) as s:
        assert s.exec(select(Reply)).one().email_id == second


def test_machine_mail_and_mail_from_before_the_ask_are_not_replies(db, mailbox):
    cid, (p1, _) = _company(db)
    _sent(db, cid, p1, hours_ago=24, n=1)
    mailbox["from"]["acme.com"] = [_msg("g1", "newsletter@acme.com", auto=True),
                                   _msg("g2", "ceo@acme.com", hours_ago=30)]          # before we wrote
    assert tracker.poll()["new_replies"] == 0


def test_a_personal_address_is_searched_by_itself_not_by_gmail_com(db, mailbox):
    cid, _ = _company(db, people=())
    with Session(db) as s:
        p = Contact(company_id=cid, name="Vinod B", email="vinod@gmail.com", source="github", confidence="verified",
                    affiliation="verified-current", role_class="engineer")
        s.add(p)
        s.commit()
        pid = p.id
    _sent(db, cid, pid, n=1)
    tracker.poll()
    assert mailbox["asked"] == ["vinod@gmail.com"]


def test_a_reply_inside_the_thread_is_still_read(db, mailbox):
    cid, (p1, _) = _company(db)
    eid = _sent(db, cid, p1, n=1)
    mailbox["threads"]["1001"] = [_msg("t1", "priya@acme.com", text="Happy to refer you")]
    mailbox["from"]["acme.com"] = [_msg("t1", "priya@acme.com", text="Happy to refer you")]   # same message
    assert tracker.poll()["new_replies"] == 1
    with Session(db) as s:
        assert s.exec(select(Reply)).one().email_id == eid


def test_machine_senders_are_recognised():
    from email.message import EmailMessage

    def m(**h):
        x = EmailMessage()
        for k, v in h.items():
            x[k.replace("_", "-")] = v
        return x
    assert gmail._is_auto(m(From="no-reply@acme.com"))
    assert gmail._is_auto(m(From="Sona <sona@acme.com>", Auto_Submitted="auto-replied"))
    assert gmail._is_auto(m(From="team@acme.com", List_Unsubscribe="<mailto:u@acme.com>"))
    assert not gmail._is_auto(m(From="Sona <sona@acme.com>"))
    assert not gmail._is_auto(m(From="careers@acme.com"))


# ------------------------------------------------------------------ automatic review

def _draft(engine, cid, pid, *, title="Software Engineer I", desc="0-1 years of experience", quality=85, body=None,
           flags=()):
    with Session(engine) as s:
        n = len(s.exec(select(Job)).all())
        j = Job(company_id=cid, company_name="Acme", title=title, url=f"https://acme.com/jobs/{n}",
                fingerprint=f"{title}-{n}", source="t", description=desc)
        s.add(j)
        s.commit()
        p = s.get(Contact, pid)
        first = p.name.split()[0]
        e = Email(contact_id=pid, company_id=cid, job_id=j.id, to_email=p.email, subject=f"{title} at Acme",
                  body=body or f"Hi {first},\n\nI'm Tejmul.\n\nResume: {RESUME}\n\nThanks,\nTejmul",
                  kind="cold", status="draft", quality=quality, address_confidence="verified",
                  review_flags=json.dumps(list(flags)), contract=json.dumps({"first_name": first}),
                  expires_at=utcnow() + timedelta(days=5))
        s.add(e)
        s.commit()
        return e.id


def test_a_good_fresher_draft_is_approved_and_a_senior_one_rejected_with_its_reason(db, monkeypatch):
    monkeypatch.setattr(suppress, "ASKS_PER_COMPANY", 5)
    cid, (p1, p2) = _company(db)
    good = _draft(db, cid, p1)
    senior = _draft(db, cid, p2, title="Senior Backend Engineer")
    out = autoreview.run()
    assert out["approved"] == [good] and [r["email_id"] for r in out["rejected"]] == [senior]
    with Session(db) as s:
        assert s.get(Email, good).status == "approved" and s.get(Email, good).approved_by == autoreview.BY
        assert s.get(Email, senior).status == "rejected" and "not a fresher role" in s.get(Email, senior).error
    assert len(autoreview.LOG.read_text().splitlines()) == 2


def test_years_quality_link_and_greeting_are_checked(db, monkeypatch):
    monkeypatch.setattr(suppress, "ASKS_PER_COMPANY", 9)
    cid, (p1, p2) = _company(db, people=("Priya Sharma", "Rahul Verma"))
    _, (p3, p4) = _company(db, name="Beta", domain="beta.ai", people=("Meena Iyer", "Arun Das"))
    years = _draft(db, cid, p1, desc="You have 3+ years of experience building APIs")
    low = _draft(db, cid, p2, quality=60)
    nolink = _draft(db, cid, p3, body="Hi Meena,\n\nResume: https://old.link/x\n\nThanks")
    wrongname = _draft(db, cid, p4, body=f"Hi there,\n\nResume: {RESUME}\n\nThanks")
    out = autoreview.run()
    why = {r["email_id"]: " ".join(r["why"]) for r in out["rejected"]}
    assert "3+ years" in why[years] and "quality 60" in why[low]
    assert "resume link" in why[nolink] and "first name" in why[wrongname]
    assert autoreview._years_asked("We were founded 5 years ago") == 0


def test_a_draft_held_for_now_stays_a_draft(db, monkeypatch):
    monkeypatch.setattr(suppress, "ASKS_PER_COMPANY", 1)
    monkeypatch.setattr(suppress, "focus_ids", lambda session, since=None: set())
    cid, (p1, p2) = _company(db)
    _sent(db, cid, p1, n=1)                      # an open ask at the company: the next one waits
    held = _draft(db, cid, p2)
    out = autoreview.run()
    assert [w["email_id"] for w in out["waiting"]] == [held] and not out["rejected"]
    with Session(db) as s:
        assert s.get(Email, held).status == "draft"


def test_nothing_is_approved_before_the_start(db, monkeypatch):
    monkeypatch.setattr(autoreview, "START", "2999-01-01T00:00")
    assert "starts" in autoreview.run()["skipped"]


# ------------------------------------------------------------------ a send lost to the network

def test_a_dropped_send_goes_back_to_the_queue_and_a_retry_never_sends_twice(db, monkeypatch):
    cid, (p1, _) = _company(db)
    with Session(db) as s:
        e = Email(contact_id=p1, company_id=cid, to_email="priya@acme.com", subject="Referral at Acme", body="Hi Priya",
                  kind="cold", status="approved", approved_at=utcnow(), address_confidence="verified")
        s.add(e)
        s.commit()
        eid = e.id
    monkeypatch.setattr(sender, "_referrer_problem", lambda session, email: None)

    def drop(*a, **k):
        raise OSError("[Errno 8] nodename nor servname provided, or not known")
    monkeypatch.setattr(sender, "_dryrun_send", drop)
    assert sender.send_email(eid, ignore_window=True)["error"]
    with Session(db) as s:
        row = s.get(Email, eid)
        assert row.status == "approved" and row.error.startswith("retry 1:")
    assert ledger.status()["used"] == 0                                         # the slot was given back

    # the retry, live: Gmail already has it in Sent Mail — so it is not sent again
    monkeypatch.setattr(sender, "SEND_MODE", "gmail")
    monkeypatch.setattr(gmail, "AUTH", "app_password")
    monkeypatch.setattr(gmail, "sent_already", lambda to, subject, since: {"id": "<ours@gmail.com>", "threadId": "77"})
    monkeypatch.setattr(gmail, "send", lambda *a, **k: pytest.fail("a mail Gmail already took is not sent twice"))
    assert sender.send_email(eid, ignore_window=True)["sent"]
    with Session(db) as s:
        row = s.get(Email, eid)
        assert row.status == "sent" and row.gmail_thread_id == "77" and row.error is None


def test_a_refusal_is_never_retried(db, monkeypatch):
    cid, (p1, _) = _company(db)
    with Session(db) as s:
        e = Email(contact_id=p1, company_id=cid, to_email="priya@acme.com", subject="s", body="b", kind="cold",
                  status="approved", approved_at=utcnow(), address_confidence="verified")
        s.add(e)
        s.commit()
        eid = e.id
    monkeypatch.setattr(sender, "_referrer_problem", lambda session, email: None)

    def refuse(*a, **k):
        raise RuntimeError("550 5.4.5 Daily user sending limit exceeded")
    monkeypatch.setattr(sender, "_dryrun_send", refuse)
    sender.send_email(eid, ignore_window=True)
    assert sender.requeue_failed() == []
    with Session(db) as s:
        assert s.get(Email, eid).status == "failed"


# ------------------------------------------------------------------ the circle

def test_the_circle_shares_the_day_keeps_its_companies_and_refills_best_bets_first(db, monkeypatch):
    from datetime import date

    from jobhunter import focus
    from jobhunter.db import CompanyDecision

    monkeypatch.setattr(focus, "get_session", lambda: Session(db))
    monkeypatch.setattr(focus, "init_db", lambda: None)
    monkeypatch.setattr(focus, "COMPANIES", 2)
    monkeypatch.setattr(focus, "PER_COMPANY", 2)
    monkeypatch.setattr(focus, "GOAL", 3)
    monkeypatch.setattr(focus, "MIN_READY", 1)
    monkeypatch.setattr(ledger, "effective_cap", lambda day: 3)
    names = {}
    for i, (nm, pool, rank) in enumerate([("A", "Bengaluru", 1), ("B", "Bengaluru", 2), ("R", "Remote", None),
                                          ("C", "Hyderabad", 1)]):
        cid, _ = _company(db, name=nm, domain=f"{nm.lower()}.ai", people=("Ann One", "Bob Two", "Cid Three"))
        names[nm] = cid
        with Session(db) as s:
            s.add(CompanyDecision(company_id=cid, pool=pool, verdict="target", p_offer=0.5 - i / 10, p_lo=0, p_hi=1,
                                  p_open=0, p_reach=0, p_screen=0, p_call=0, p_loop=0, p_pay=0, dsa=0, info=0,
                                  rank=rank, at=utcnow()))
            s.commit()

    d1 = focus.pick(date(2026, 11, 2))
    assert [(t["name"], t["take"]) for t in d1["targets"]] == [("A", 2), ("B", 1)] and d1["slots"] == 3
    assert focus.pick(date(2026, 11, 2)) == d1                                   # the plan stays put

    def ask_all(cid, n):
        with Session(db) as s:
            for p in s.exec(select(Contact).where(Contact.company_id == cid)).all()[:n]:
                s.add(Email(contact_id=p.id, company_id=cid, to_email=p.email, subject="s", body="b", kind="cold",
                            status="sent", sent_at=utcnow()))
            s.commit()
    ask_all(names["A"], 3)                                                       # A reached the goal
    ask_all(names["B"], 1)
    d2 = focus.pick(date(2026, 11, 3))
    # A leaves; B stays; the next best bet is the ranked Hyderabad shortlist, not the unranked Remote one
    assert [t["name"] for t in d2["targets"]] == ["B", "C"] and d2["slots"] == 3
    assert names["A"] in focus.circle()["done"]


def test_naming_the_role_is_not_claiming_its_skills():
    """2026-10-26, GoComet: "Full stack AI Engineer" in the ask flagged every draft as claiming full-stack."""
    from jobhunter.outreach import gates

    body = "Hi Jayesh,\n\nI saw that GoComet is hiring for Full stack AI Engineer, and I'd be grateful for a referral."
    assert "uncovered_claim" in [f["kind"] for f in gates.check(body, corpus=body, forbidden=["Full-stack"]).flags]
    ok = gates.check(body, corpus=body, forbidden=["Full-stack"], roles=["Full stack AI Engineer"])
    assert "uncovered_claim" not in [f["kind"] for f in ok.flags]
    claim = gates.check(body + " I'm a strong full-stack developer.", corpus=body, forbidden=["Full-stack"],
                        roles=["Full stack AI Engineer"])
    assert "uncovered_claim" in [f["kind"] for f in claim.flags]                 # a real claim still is


def test_a_company_whose_drafts_all_fail_leaves_the_circle(db, monkeypatch):
    from datetime import date

    from jobhunter import focus
    from jobhunter.db import CompanyDecision

    monkeypatch.setattr(focus, "get_session", lambda: Session(db))
    monkeypatch.setattr(focus, "init_db", lambda: None)
    monkeypatch.setattr(focus, "COMPANIES", 1)
    monkeypatch.setattr(focus, "MIN_READY", 1)
    monkeypatch.setattr(ledger, "effective_cap", lambda day: 2)
    ids = []
    for i, nm in enumerate(["Dud", "Next"]):
        cid, people = _company(db, name=nm, domain=f"{nm.lower()}.ai", people=("Ann One", "Bob Two", "Cid Three", "Dee Four"))
        ids.append((cid, people))
        with Session(db) as s:
            s.add(CompanyDecision(company_id=cid, pool="Bengaluru", verdict="target", p_offer=0.5, p_lo=0, p_hi=1,
                                  p_open=0, p_reach=0, p_screen=0, p_call=0, p_loop=0, p_pay=0, dsa=0, info=0,
                                  rank=i + 1, at=utcnow()))
            s.commit()
    assert [t["name"] for t in focus.pick(date(2026, 11, 2))["targets"]] == ["Dud"]
    dud, people = ids[0]
    with Session(db) as s:
        for pid in people[:3]:
            s.add(Email(contact_id=pid, company_id=dud, to_email="x@dud.ai", subject="s", body="b", kind="cold",
                        status="rejected", error="auto-review: not a fresher role: Staff Engineer"))
        s.commit()
    assert [t["name"] for t in focus.pick(date(2026, 11, 3))["targets"]] == ["Next"]


def test_a_near_copy_into_the_same_company_is_held_back_but_gmail_com_is_not_one_company(db, monkeypatch):
    monkeypatch.setattr(suppress, "ASKS_PER_COMPANY", 9)
    cid, (p1, p2) = _company(db)
    a = _draft(db, cid, p1)
    b = _draft(db, cid, p2)                              # the same wording into acme.com
    gid, (g1, g2) = _company(db, name="Solo", domain="gmail.com", people=("Ann One", "Bob Two"))
    c = _draft(db, gid, g1)
    d = _draft(db, gid, g2)
    out = autoreview.run()
    assert a in out["approved"] and c in out["approved"] and d in out["approved"]
    rej = {r["email_id"]: " ".join(r["why"]) for r in out["rejected"]}
    assert b in rej and "spam signal" in rej[b]


def test_a_company_whose_best_bet_is_senior_is_asked_about_its_fresher_role(db):
    from jobhunter import focus

    cid, _ = _company(db, people=())
    with Session(db) as s:
        senior = Job(company_id=cid, company_name="Acme", title="Senior Backend Engineer", url="https://acme.com/1",
                     fingerprint="1", source="t", selection_score=90)
        junior = Job(company_id=cid, company_name="Acme", title="Software Engineer I", url="https://acme.com/2",
                     fingerprint="2", source="t", selection_score=60, description="0-1 years of experience")
        s.add(senior)
        s.add(junior)
        s.commit()
        c = s.get(Company, cid)
        c.best_selection_job_id = senior.id
        s.add(c)
        s.commit()
        assert focus.role_for(s, c) == junior.id and not focus._blocked(s, c)
        junior.title = "Staff Engineer"
        s.add(junior)
        s.commit()
        assert focus.role_for(s, c) is False and focus._blocked(s, c)
