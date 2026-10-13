"""Referrals: address patterns learned from evidence, the complete bar, re-verification."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from jobhunter import guardrails as g
from jobhunter.contacts import addresses, patterns as P
from jobhunter.contacts.linkedin_public import people_from_results
from jobhunter.db import Company, Contact, Email, ExtractionReject, ExtractionWrite, people_order, utcnow


# ------------------------------------------------------------------ pattern learning

def test_one_example_is_not_enough_two_that_agree_are():
    one = P.learn_with_evidence([("Priya Sharma", "priya.sharma@acme.com")])
    assert one.status == "weak" and one.pattern is None and one.candidate == "{first}.{last}"
    two = P.learn_with_evidence([("Priya Sharma", "priya.sharma@acme.com"), ("Rahul Verma", "rahul.verma@acme.com")])
    assert two.status == "confirmed" and two.pattern == "{first}.{last}" and two.agree == 2
    assert set(two.examples) == {"priya.sharma@acme.com", "rahul.verma@acme.com"}


def test_one_example_plus_their_own_page_stating_the_format_is_enough():
    v = P.learn_with_evidence([("Priya Sharma", "psharma@acme.com")], stated=("{f}{last}", "https://acme.com/careers"))
    assert v.status == "confirmed" and v.pattern == "{f}{last}" and v.stated == "https://acme.com/careers"
    # a stated format the addresses contradict does not win on its own
    v2 = P.learn_with_evidence([("Priya Sharma", "priya@acme.com")], stated=("{f}{last}", "https://acme.com/careers"))
    assert v2.pattern is None


@pytest.mark.parametrize("local,want", [("firstname.lastname", "{first}.{last}"), ("first_last", "{first}_{last}"),
                                        ("flast", "{f}{last}"), ("firstl", "{first}{l}"), ("firstname", "{first}"),
                                        ("john.doe", None), ("priya.sharma", None)])
def test_a_page_stating_the_format(local, want):
    assert P.stated_pattern(f"{local}@acme.com") == want


def test_conflicting_patterns_are_not_used():
    tie = P.learn_with_evidence([("Priya Sharma", "priya.sharma@acme.com"), ("Rahul Verma", "rahul.verma@acme.com"),
                                 ("Anil Kumar", "anil@acme.com"), ("Divya Rao", "divya@acme.com")])
    assert tie.status == "conflict" and tie.pattern is None
    close = P.learn_with_evidence([("A Bc", "x@acme.com"), ("Priya Sharma", "priya.sharma@acme.com"),
                                   ("Rahul Verma", "rahul.verma@acme.com"), ("Meena Iyer", "meena.iyer@acme.com"),
                                   ("Anil Kumar", "anil@acme.com"), ("Divya Rao", "divya@acme.com")])
    assert close.status == "conflict"          # 3 vs 2 is not a clear lead
    clear = P.learn_with_evidence([("Priya Sharma", "priya.sharma@acme.com"), ("Rahul Verma", "rahul.verma@acme.com"),
                                   ("Meena Iyer", "meena.iyer@acme.com"), ("Kiran Das", "kiran.das@acme.com"),
                                   ("Anil Kumar", "anil@acme.com")])
    assert clear.status == "confirmed" and clear.pattern == "{first}.{last}"   # 4 vs 1, an old address


def test_names_that_cannot_be_patterned_are_skipped():
    for name in ("Madonna", "J Smith", "张伟", "", None):
        assert not P.patternable(name)
    v = P.learn_with_evidence([("Madonna", "madonna@acme.com"), ("J Smith", "jsmith@acme.com"), ("张伟", "zw@acme.com")])
    assert v.status == "none" and v.matched == 0
    # an address two patterns both explain says nothing
    amb = P.learn_with_evidence([("Li Li", "li@acme.com"), ("Li Li", "lili@acme.com")])
    assert amb.pattern is None


# ------------------------------------------------------------------ the database steps

@pytest.fixture()
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'r.db'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _company(session, **kw) -> Company:
    c = Company(name=kw.pop("name", "Acme"), domain=kw.pop("domain", "acme.com"), website="https://acme.com", **kw)
    session.add(c)
    session.commit()
    return c


def _person(session, c, name, email=None, *, source="github", affiliation="verified-current", **kw) -> Contact:
    p = Contact(company_id=c.id, name=name, email=email, source=source, affiliation=affiliation,
                email_kind="company" if email else None, linkedin=kw.pop("linkedin", None), **kw)
    session.add(p)
    session.commit()
    return p


def test_a_confirmed_pattern_addresses_ready_referrers_and_is_guessed(session):
    c = _company(session)
    _person(session, c, "Priya Sharma", "priya.sharma@acme.com", confidence="verified")
    _person(session, c, "Rahul Verma", "rahul.verma@acme.com", confidence="verified")
    lead = _person(session, c, "Kiran Das", source="linkedin-public", affiliation="likely-current",
                   linkedin="https://linkedin.com/in/kiran")
    gone = _person(session, c, "Old Hand", source="linkedin-public", affiliation="unconfirmed",
                   linkedin="https://linkedin.com/in/old")
    plan = addresses.plan_company(session, c, check_mx=False)
    assert plan.verdict["status"] == "confirmed" and plan.add == [(lead.id, "kiran.das@acme.com")]
    plan.mx_ok = True
    res = addresses.apply_plan(session, c, plan)
    session.commit()
    assert res == {"pattern": "written", "added": 1, "retracted": 0}
    session.refresh(lead)
    session.refresh(gone)
    assert lead.email == "kiran.das@acme.com" and lead.confidence == "pattern-guessed"   # the 5/day cap applies
    assert "2 of 2 agree" in lead.research_notes
    assert gone.email is None          # not confirmed there now: never addressed
    assert P.confirmed(session.get(Company, c.id)) == "{first}.{last}"
    assert session.exec(select(ExtractionWrite).where(ExtractionWrite.channel == "pattern")).first()


def test_no_mx_means_no_address(session):
    c = _company(session)
    _person(session, c, "Priya Sharma", "priya.sharma@acme.com")
    _person(session, c, "Rahul Verma", "rahul.verma@acme.com")
    lead = _person(session, c, "Kiran Das", source="linkedin-public", affiliation="likely-current")
    plan = addresses.plan_company(session, c, check_mx=False)
    plan.mx_ok = False
    addresses.apply_plan(session, c, plan)
    session.commit()
    session.refresh(lead)
    assert lead.email is None


def test_a_guess_from_an_unconfirmed_pattern_is_retracted_unless_mailed(session):
    c = _company(session, email_pattern="{first}")
    _person(session, c, "Priya Sharma", "priya@acme.com")               # one example only
    guess = _person(session, c, "Kiran Das", "kiran@acme.com", source="pattern", confidence="pattern-guessed")
    mailed = _person(session, c, "Divya Rao", "divya@acme.com", source="pattern", confidence="pattern-guessed")
    session.add(Email(contact_id=mailed.id, company_id=c.id, to_email="divya@acme.com", subject="s", body="b"))
    session.commit()
    plan = addresses.plan_company(session, c, check_mx=False)
    assert plan.verdict["status"] == "weak" and plan.pattern_change == "cleared"
    assert [r[0] for r in plan.retract] == [guess.id]
    addresses.apply_plan(session, c, plan)
    session.commit()
    session.refresh(guess)
    assert guess.email is None and session.get(Company, c.id).email_pattern is None
    assert session.exec(select(ExtractionReject).where(ExtractionReject.contact_id == guess.id)).first()
    assert session.get(Contact, mailed.id).email == "divya@acme.com"


def test_an_unnamed_address_pairs_with_the_one_person_it_renders():
    c = Company(id=1, name="Acme", domain="acme.com")
    rows = [Contact(id=1, company_id=1, name=None, email="priya.sharma@acme.com", source="web"),
            Contact(id=2, company_id=1, name="Priya Sharma", source="linkedin-public"),
            Contact(id=3, company_id=1, name=None, email="raj@acme.com", source="web"),
            Contact(id=4, company_id=1, name="Raj Mehta", source="linkedin-public"),
            Contact(id=5, company_id=1, name="Raj Kumar", source="linkedin-public")]
    ex = addresses.examples(rows, c.domain)
    assert ex == [("Priya Sharma", "priya.sharma@acme.com")]     # raj@ could be either Raj


def test_placeholder_people_are_never_ready():
    p = Contact(company_id=1, name="Firstname Lastname", source="web", affiliation="verified-current")
    assert not g.person_name("Firstname Lastname")
    assert not g.is_ready(p)


# ------------------------------------------------------------------ the complete bar

DESC = "Acme builds observability tooling that helps machine learning teams debug models in production."


def test_complete_needs_site_description_hiring_and_a_referrer_with_an_address():
    c = Company(name="Acme", domain="acme.com", website="https://acme.com", description=DESC, hiring_status="verified")
    assert g.complete_bar(c, has_roles=False, ready_with_email=1) == (True, [])
    assert g.complete_bar(c, has_roles=False, ready_with_email=0) == (False, ["referrer_email"])
    c.hiring_status = None
    assert g.complete_bar(c, has_roles=True, ready_with_email=1)[0]          # stored roles are hiring proof
    assert g.complete_bar(c, has_roles=False, ready_with_email=1)[1] == ["hiring"]
    bare = Company(name="Acme", description="Just a moment... Checking your browser")
    ok, missing = g.complete_bar(bare, has_roles=False, ready_with_email=0)
    assert not ok and missing == ["website", "description", "hiring", "referrer_email"]
    # a part the rolling re-check failed is missing until a later check passes it
    c.hiring_status = "verified"
    c.recheck_failed = json.dumps({"website": "does not answer (ConnectError)"})
    assert g.complete_bar(c, has_roles=True, ready_with_email=1) == (False, ["website"])


def test_refresh_complete_counts_only_ready_people_with_an_address(session):
    c = _company(session, description=DESC, hiring_status="verified")
    _person(session, c, "Priya Sharma", affiliation="likely-current")                       # no address
    _person(session, c, None, "info@acme.com", affiliation="verified-current")              # an inbox
    out = g.refresh_complete(session)
    assert out["complete"] == 0 and json.loads(session.get(Company, c.id).complete_missing) == ["referrer_email"]
    _person(session, c, "Rahul Verma", "rahul.verma@acme.com")
    out = g.refresh_complete(session)
    row = session.get(Company, c.id)
    assert out["became_complete"] == 1 and row.complete and row.complete_at is not None
    assert people_order(row)[0] == 2
    row.complete, row.complete_missing = False, json.dumps(["referrer_email"])
    assert people_order(row)[0] == 1           # only a referrer short: second in the queue
    row.list_key = "india-star"
    assert people_order(row)[0] == 0


# ------------------------------------------------------------------ re-verification

def test_a_failed_recheck_downgrades_to_unconfirmed_and_keeps_the_reason(session):
    c = _company(session)
    p = _person(session, c, "Kiran Das", "kiran.das@acme.com", affiliation="likely-current",
                affiliation_source="linkedin-headline", affiliation_checked_at=utcnow() - timedelta(days=120))
    assert g.is_ready(p)
    g.downgrade_affiliation(session, p, reason="public headline no longer names the company")
    session.commit()
    session.refresh(p)
    assert p.affiliation == "unconfirmed" and not g.is_ready(p)
    assert (utcnow() - p.affiliation_checked_at).total_seconds() < 60
    rej = session.exec(select(ExtractionReject).where(ExtractionReject.contact_id == p.id)).one()
    assert rej.field == "contact.affiliation" and rej.value == "likely-current" and "no longer" in rej.reason


def test_set_recheck_records_failures_and_recoveries(session):
    c = _company(session)
    out = g.set_recheck(session, c, {"website": "HTTP 404"})
    assert out == {"newly_failed": ["website"], "recovered": []} and c.validated_at is not None
    out = g.set_recheck(session, c, {})
    assert out == {"newly_failed": [], "recovered": ["website"]} and c.recheck_failed is None


def test_a_former_employer_headline_is_not_a_referrer():
    hits = [
        {"title": "Priya Sharma - Senior Engineer - Acme | LinkedIn", "url": "https://in.linkedin.com/in/priya"},
        {"title": "Rahul Verma - ex-Acme | Building at Other", "url": "https://linkedin.com/in/rahul"},
        {"title": "Meena Iyer", "url": "https://linkedin.com/in/meena",
         "snippet": "ML Engineer\n### ML Engineer - [Acme](https://www.linkedin.com/company/acme) (Current)"},
        {"title": "Kiran Das - Engineer - Globex", "url": "https://linkedin.com/in/kiran"},
    ]
    got = {p["name"]: p for p in people_from_results(hits, "Acme", "acme.com")}
    assert set(got) == {"Priya Sharma", "Meena Iyer"}
    assert got["Meena Iyer"]["role"] == "ML Engineer"
    aff = g.affiliation(company_name="Acme", domain="acme.com", linkedin_headline=got["Priya Sharma"]["headline"])
    assert aff.label == "likely-current"


# ------------------------------------------------------------------ the guessed-address cap still holds

def test_pattern_guessed_addresses_spend_the_5_a_day_guessed_cap(tmp_path, monkeypatch):
    import inspect

    from jobhunter.outreach import ledger, sender

    engine = create_engine(f"sqlite:///{tmp_path / 'l.db'}")
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(ledger, "DB_PATH", tmp_path / "l.db")
    # the guessed cap on its own — the warm-up quota (tests/test_launch.py) is a separate ceiling
    monkeypatch.setattr(ledger, "effective_cap", lambda day: ledger.DAILY_CAP)
    # the sender counts anything not `verified` — pattern-guessed included — as guessed
    assert 'guessed = (email.address_confidence or "verified") != "verified"' in inspect.getsource(sender)
    for _ in range(ledger.GUESSED_CAP):
        assert ledger.reserve(1, guessed=True)[0]
    ok, why = ledger.reserve(1, guessed=True)
    assert not ok and "guessed-address cap" in why and ledger.GUESSED_CAP == 5
    assert ledger.reserve(1, guessed=False)[0]          # verified addresses still go
