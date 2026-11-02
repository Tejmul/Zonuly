"""No guessed address enters the database (2026-11-02).

Tejmul: "i dont need guessed mails i only need verified mails okk very very strict guardrial it is
also keep in the system we dont need fake emails or guessed emails from now on no fake emails
entering my database."

The rule has two halves and both matter:

  REFUSED   an address whose provenance says it was CONSTRUCTED — source "pattern", confidence
            "pattern-guessed", a research note saying "email by pattern" — is dropped outright.
            Not on `contact.email`, and not on `email_unverified` either, because a hint nobody
            can prove is a bounce waiting to happen. The refusal is recorded, never silent.
  KEPT      an address genuinely FOUND (a commit, a public profile, a page) still waits in
            `email_unverified`, unproved and unmailable. That is the input Reacher proves from;
            dropping it would end verification rather than tighten it.
"""

from __future__ import annotations

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from jobhunter.db import Company, Contact, EmailCheck, ExtractionReject, looks_guessed


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(Company(id=1, name="Acme", domain="acme.in"))
        s.commit()
        yield s


def test_a_guessed_address_is_refused_and_not_even_kept_as_a_hint(session):
    session.add(Contact(company_id=1, name="Guessed Person", source="pattern",
                        email="first.last@acme.in"))
    session.commit()
    c = session.exec(select(Contact).where(Contact.name == "Guessed Person")).one()
    assert c.email is None, "a guessed address must never reach contact.email"
    assert c.email_unverified is None, "nor email_unverified — that was the loophole"
    assert c.email_status == "rejected:guessed"

    rej = session.exec(select(ExtractionReject)).all()
    assert [r.value for r in rej] == ["first.last@acme.in"]
    assert "guessed" in rej[0].reason


@pytest.mark.parametrize("field,value", [
    ("source", "pattern"),
    ("confidence", "pattern-guessed"),
    ("research_notes", "email by pattern: {first}@{domain}"),
])
def test_every_guess_marker_is_caught(session, field, value):
    c = Contact(company_id=1, name=f"P {field}", source="site", email="x@acme.in")
    setattr(c, field, value)
    assert looks_guessed(c), f"{field}={value!r} should read as guessed"
    session.add(c)
    session.commit()
    assert session.exec(select(Contact).where(Contact.name == f"P {field}")).one().email is None


def test_an_address_that_was_actually_found_is_still_kept_unproved(session):
    """The verification pipeline needs these. Found is not guessed."""
    session.add(Contact(company_id=1, name="Found Person", source="github", email="dev@acme.in"))
    session.commit()
    c = session.exec(select(Contact).where(Contact.name == "Found Person")).one()
    assert c.email is None, "unproved still never sits on contact.email"
    assert c.email_unverified == "dev@acme.in", "but it is kept for Reacher to prove"
    assert c.email_status == "fake:not-proved"
    assert session.exec(select(ExtractionReject)).all() == []


def test_a_proved_address_survives_even_if_its_provenance_looks_like_a_pattern(session):
    """Proof beats provenance: once check-if-email-exists says safe, how we first guessed at the
    shape of it stops mattering."""
    session.add(EmailCheck(address="real@acme.in", verdict="safe", source="reacher"))
    session.commit()
    session.add(Contact(company_id=1, name="Proved Person", source="pattern",
                        email="real@acme.in", email_status="real"))
    session.commit()
    c = session.exec(select(Contact).where(Contact.name == "Proved Person")).one()
    assert c.email == "real@acme.in"
    assert c.email_verified_by == "reacher"
