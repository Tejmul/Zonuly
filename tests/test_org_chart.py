"""Reading an org chart off a company's own team page, and folding the duplicates it exposes.

2026-11-08, OpenHands: 83 contact rows for a company of 27 people, every one of them labelled
"engineer, rank 2" because the scrapers had recorded GitHub handles and no titles. Two separate
faults, and these are the tests for both.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime
from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine, select

from jobhunter.contacts import roles
from jobhunter.db import Company, Contact, Email, EmailCheck

ROOT = Path(__file__).resolve().parent.parent


def _dedupe_module():
    spec = importlib.util.spec_from_file_location("dedupe_contacts", ROOT / "scripts" / "dedupe_contacts.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["dedupe_contacts"] = mod
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------- the classifier

def test_a_researcher_at_an_ai_lab_is_an_engineer_not_an_other():
    """"Senior Agent Researcher" used to fall to rank 9 and so was never asked for a referral.

    At an agent startup the researchers ship the code and sit beside the hiring manager; ranking
    them below a recruiter was a scraping artefact, not a judgement about them."""
    for title in ("Senior Agent Researcher", "Senior Researcher", "Principal Researcher"):
        v = roles.classify(title)
        assert v.role_class == "senior_engineer", f"{title} -> {v.role_class}"
        assert v.referral_rank == 1


def test_member_of_technical_staff_is_an_engineer():
    """MTS is the standard engineer title at OpenAI, Anthropic, Mistral and most agent labs."""
    v = roles.classify("Member of the Technical Staff, Agent R&D")
    assert v.role_class == "engineer"
    assert v.seniority == "staff"


def test_the_commercial_titles_still_rank_last():
    """The fix must not sweep in everyone: an account executive cannot refer an engineer."""
    for title in ("Account Executive", "Business Development Representative", "Community Manager"):
        assert roles.classify(title).referral_rank == 9


def test_a_title_still_beats_a_bio():
    """The seniority ladder is unchanged by the researcher rule."""
    assert roles.classify("Staff Software Engineer").referral_rank == 1
    assert roles.classify("Software Engineer").referral_rank == 2
    assert roles.classify("Engineering Manager").role_class == "eng_manager"
    assert roles.classify("CEO").role_class == "founder"


# --------------------------------------------------------------- the dedupe

def _three_rows_of_one_person(engine):
    """The same person as three scrapers found them: GitHub, the site, and a sheet he mailed."""
    with Session(engine) as s:
        s.add(Company(id=1, name="OpenHands"))
        # found by the GitHub pass — a handle and nothing else
        s.add(Contact(id=10, company_id=1, name="Engel Nyst", github="https://github.com/enyst",
                      source="github"))
        # found again by the site pass — this one carries the title
        s.add(Contact(id=11, company_id=1, name="Engel Nyst", github="https://github.com/ENYST",
                      role="Software Engineer", source="site-team"))
        # and a third, which is the one he actually mailed from his own sheet
        s.add(Contact(id=12, company_id=1, name="Engel Nyst", github="https://github.com/enyst",
                      email_unverified="engel@example.com", source="apify",
                      mailed_by_tejmul_at=datetime(2026, 10, 30, 20, 9)))
        s.add(Email(id=1, contact_id=12, company_id=1, to_email="engel@example.com",
                    subject="Engel, quick question", body="\u2026", status="sent"))
        s.commit()


def test_three_rows_become_one_person_and_the_mailed_lock_survives(tmp_path, monkeypatch):
    """The lock is the whole risk: lose it and he mails someone a second time.

    It does not matter which row wins — whichever does must carry `mailed_by_tejmul_at`, because
    that is what keeps the person out of every future CSV."""
    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}")
    SQLModel.metadata.create_all(engine)
    _three_rows_of_one_person(engine)
    mod = _dedupe_module()
    monkeypatch.setattr(mod, "get_session", lambda: Session(engine))
    monkeypatch.setattr(mod, "init_db", lambda: None)

    res = mod.run(company_id=1)
    assert res["dropped"] == 2
    # `locks_preserved` counts a lock that had to be CARRIED to another row. It is 0 here, and
    # that is the designed outcome: `score()` ranks the lock first, so the row holding it wins
    # the merge outright and the lock never has to move. The counter is the net below that.

    with Session(engine) as s:
        left = s.exec(select(Contact).where(Contact.company_id == 1)).all()
        assert len(left) == 1
        kept = left[0]
        assert kept.mailed_by_tejmul_at is not None, "the mailed lock was lost — he would mail twice"
        # the title found by the site pass is not thrown away with the row that carried it
        assert kept.role == "Software Engineer"
        # and the sent mail still points at a person who exists
        mail = s.get(Email, 1)
        assert mail.contact_id == kept.id


def test_a_differing_case_handle_is_the_same_person():
    """GitHub logins are case-insensitive; three scrapers wrote three casings."""
    mod = _dedupe_module()
    a = Contact(company_id=1, github="https://github.com/enyst")
    b = Contact(company_id=1, github="https://github.com/ENYST")
    assert mod.key_of(a) == mod.key_of(b)


def test_a_shared_mailbox_is_never_merged_with_a_person():
    """"General inbox" is a label, not a name — two of them are not one person."""
    mod = _dedupe_module()
    assert mod.key_of(Contact(company_id=1, name="General inbox")) is None
    assert mod.key_of(Contact(company_id=1, name="")) is None


def test_a_proved_address_beats_an_unproved_one_in_a_merge(tmp_path, monkeypatch):
    """Verification is the scarce thing here — a merge must never discard a proved address."""
    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(Company(id=1, name="OpenHands"))
        s.add(Contact(id=20, company_id=1, name="A", github="https://github.com/a",
                      source="github", email_unverified="a@example.com"))
        s.add(EmailCheck(address="a@example.com", verdict="safe", source="apify"))
        s.commit()
        s.add(Contact(id=21, company_id=1, name="A", github="https://github.com/a",
                      source="apify", email="a@example.com", email_status="real",
                      email_verified_by="apify"))
        s.commit()
    mod = _dedupe_module()
    monkeypatch.setattr(mod, "get_session", lambda: Session(engine))
    monkeypatch.setattr(mod, "init_db", lambda: None)
    mod.run(company_id=1)
    with Session(engine) as s:
        kept = s.exec(select(Contact).where(Contact.company_id == 1)).all()
        assert len(kept) == 1
        assert kept[0].email == "a@example.com"
        assert kept[0].email_status == "real"


# --------------------------------------------------------------- the 500 that was

def test_a_prose_investor_field_does_not_take_down_the_company_page():
    """2026-11-08: a sentence typed into `funding_investors` made /detail return 500 — the whole
    page gone because one field was not JSON."""
    from jobhunter.network import _as_list

    assert _as_list(None) == []
    assert _as_list('["Madrona", "Menlo Ventures"]') == ["Madrona", "Menlo Ventures"]
    assert _as_list("Madrona; Menlo Ventures") == ["Madrona", "Menlo Ventures"]
    assert _as_list("just a sentence") == ["just a sentence"]
