"""guardrails: every extraction passes one validator per field before it is written."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from jobhunter import audit, guardrails as g
from jobhunter.db import Company, Contact, ExtractionReject, ExtractionWrite


# ------------------------------------------------------------------ people

@pytest.mark.parametrize("raw", ["Samrat002", "kbysani-affirm", "yuchengxin", "Bulbasaur", "jdoe_42",
                                 "GitHub Team", "someone@acme.com", "https://github.com/x"])
def test_handles_and_non_people_are_not_names(raw):
    assert not g.person_name(raw)


@pytest.mark.parametrize("raw,want", [("Federico Bartoli", "Federico Bartoli"), ("theo michel", "Theo Michel"),
                                      ("Jane Doe 🚀", "Jane Doe"), ("O'Brien Smith", "O'Brien Smith"),
                                      ("张伟", "张伟")])
def test_real_names_pass_and_are_cleaned(raw, want):
    chk = g.person_name(raw)
    assert chk and chk.value == want


def test_generic_inboxes_are_company_contacts_never_referrers():
    for addr in ("info@acme.com", "careers@acme.com", "hr@acme.com", "hello@acme.com"):
        chk = g.email(addr, domain="acme.com")
        assert chk and chk.value[1] == "generic"
        aff = g.affiliation(company_name="Acme", domain="acme.com", email_kind="generic", email_addr=addr)
        assert aff.label == "generic"
    assert g.email("a.b@acme.com", domain="acme.com").value[1] == "company"
    assert g.email("x@gmail.com", domain="acme.com").value[1] == "personal"
    assert g.email("s@irobot.com", domain="acme.com").value[1] == "other"
    assert not g.email("noreply@acme.com", domain="acme.com")


def test_a_commit_is_not_employment():
    now = datetime(2026, 10, 7)
    old = g.affiliation(company_name="Acme", domain="acme.com", email_kind="personal",
                        last_commit_at=now - timedelta(days=900), now=now)
    assert old.label == "unconfirmed"
    recent = g.affiliation(company_name="Acme", domain="acme.com", email_kind="personal",
                           last_commit_at=now - timedelta(days=30), commit_repo="acme/api", now=now)
    assert recent.label == "likely-current"
    company = g.affiliation(company_name="Acme", domain="acme.com", email_kind="company",
                            last_commit_at=now - timedelta(days=30), now=now)
    assert company.label == "verified-current"
    member = g.affiliation(company_name="Acme", domain="acme.com", email_kind="personal", org_member=True, now=now)
    assert member.label == "verified-current"
    claim = g.affiliation(company_name="Acme", email_kind="personal", employer_claim="@acme-hq", now=now)
    assert claim.label == "verified-current"
    ex = g.affiliation(company_name="Acme", email_kind="personal", employer_claim="ex-Acme, now Globex", now=now)
    assert ex.label == "unconfirmed"
    headline = g.affiliation(company_name="Acme", email_kind=None,
                             linkedin_headline="Priya Rao - Backend Engineer - Acme | LinkedIn", now=now)
    assert headline.label == "likely-current"
    # an old commit address is not a verified address either
    assert g.address_confidence(source="github", email_kind="personal",
                                last_commit_at=now - timedelta(days=900), now=now) == "scraped"
    assert g.address_confidence(source="github", email_kind="personal",
                                last_commit_at=now - timedelta(days=10), now=now) == "verified"


def test_only_confirmed_current_people_are_ready():
    assert g.is_ready(Contact(company_id=1, source="github", affiliation="verified-current", email_kind="personal"))
    assert g.is_ready(Contact(company_id=1, source="linkedin-public", affiliation="likely-current"))
    assert not g.is_ready(Contact(company_id=1, source="github", affiliation="unconfirmed"))
    assert not g.is_ready(Contact(company_id=1, source="web", affiliation="generic", email_kind="generic"))
    assert not g.is_ready(Contact(company_id=1, source="github"))          # never checked is not ready


# ------------------------------------------------------------------ descriptions

@pytest.mark.parametrize("text,why", [
    ("Just a moment... Checking your browser before accessing the site, this may take a few seconds.", "bot wall"),
    ("We use cookies to improve your experience on our site. Accept all cookies to continue browsing Acme.", "cookie"),
    ("Acme raises $12M Series A led by Sequoia to build the future of AI agents for finance teams", "funding headline"),
    ("Acme is a Technology, Information and Internet company.", "industry label"),
    ("We're looking for a senior backend engineer with 5+ years of experience to join Acme's team.", "job ad"),
    ("Stripe is a payments platform that lets businesses accept money online across the world.", "another company"),
    ("Acme builds agents.", "too short"),
])
def test_bad_descriptions_are_refused(text, why):
    chk = g.description(text, name="Acme", domain="acme.com", strict=True)
    assert not chk, why
    assert chk.reason


def test_a_description_must_name_the_company_unless_it_is_from_their_own_site():
    text = "Healthcare data infrastructure that unifies patient records from every source for care teams."
    assert not g.description(text, name="Acme", domain="acme.com", strict=True)
    assert g.description(text, name="Acme", domain="acme.com", own_site=True)
    ok = g.description("Acme gives finance teams one ledger for every bank account, card and invoice they hold.",
                       name="Acme", domain="acme.com")
    assert ok and ok.confidence == "high"
    long = "Acme builds software. " + "It does many things for many teams around the world. " * 20
    trimmed = g.description(long, name="Acme")
    assert trimmed and len(trimmed.value) <= g.DESC_MAX and "trimmed" in trimmed.flags


# ------------------------------------------------------------------ funding

def test_revenue_debt_and_absurd_amounts_are_not_raises():
    assert not g.funding_amount(12, "Acme reported annual revenue of $12 million last year")
    assert not g.funding_amount(29, "Acme secured a $29M venture debt facility from a bank")
    assert not g.funding_amount(20000, "Acme raised $20B")
    assert not g.funding_amount(0.01, "Acme raised $10K from friends")
    assert g.funding_amount(12, "Acme raised $12 million in a Series A led by Sequoia")


def test_dates_cannot_be_in_the_future_or_before_founding():
    now = datetime(2026, 10, 7)
    assert not g.funding_date("2027-05-01", now=now)
    assert not g.funding_date("2019-03", founded_year=2021, now=now)
    assert g.funding_date("Tue, 07 Oct 2026 10:00:00 GMT", now=now).value == "2026-10-07"
    assert g.funding_date("2025-06", now=now).value == "2025-06"


def test_investors_are_names_and_valuation_must_exceed_the_raise():
    inv = g.investors("Sequoia, Accel and others; participation from existing investors")
    assert inv and inv.value == ["Sequoia", "Accel"]
    assert not g.investors(["The round was led by Sequoia Capital with participation"])
    assert not g.valuation(5, amount=10)
    assert not g.valuation(5000, stage="Seed")
    assert g.valuation(60, amount=10, stage="Series A")


def test_the_funding_quote_must_name_the_company():
    assert not g.funding_evidence("The pre-seed round was led by Superhero Capital.", name="Acme")
    assert g.funding_evidence("Acme closes $5M seed", name="Acme")
    assert g.funding_evidence("YC Winter 2025 (yc-oss directory) — batch investment, seed-stage", name="Acme")
    assert g.funding_evidence("We have raised $258M from world-class investors.", name="Acme", channel="page")


def test_hiring_needs_a_role_title_on_their_own_page_and_goes_stale():
    now = datetime(2026, 10, 7)
    assert g.hiring("verified", roles=[], careers_url="https://acme.com/careers", domain="acme.com").value == "careers_only"
    assert g.hiring("verified", roles=["Backend Engineer"], careers_url="https://www.linkedin.com/jobs/acme",
                    domain="acme.com").value == "careers_only"
    ok = g.hiring("verified", roles=["Backend Engineer"], careers_url="https://jobs.lever.co/acme",
                  checked_at=now, domain="acme.com", now=now)
    assert ok and ok.value == "verified"
    stale = g.hiring("verified", roles=["Backend Engineer"], careers_url="https://acme.com/careers",
                     checked_at=now - timedelta(days=45), domain="acme.com", now=now)
    assert stale.value == "needs_recheck"


# ------------------------------------------------------------------ writing: provenance, rejects, conflicts

@pytest.fixture()
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'g.db'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_writes_carry_provenance_and_rejects_are_kept(session):
    c = Company(name="Acme", domain="acme.com")
    session.add(c)
    session.flush()
    g.set_description(session, c, "Acme raises $5M to build agents for finance teams across Europe and the US",
                      source="https://news.example/acme", channel="feed")
    assert c.description is None
    g.set_description(session, c, "Acme gives finance teams one ledger for every bank account, card and invoice.",
                      source="https://acme.com", channel="page", own_site=True)
    assert c.description.startswith("Acme gives")
    out = g.set_funding(session, c, {"stage": "seed", "amount_usd_m": 5, "announced": "2099-01-01",
                                     "evidence": "Acme closes a $5M seed round led by Point Nine"},
                        source="https://news.example/acme", channel="feed")
    assert c.funding_stage == "Seed" and c.funding_amount_usd_m == 5
    assert c.funding_announced is None and ("funding_announced", "2099-01 is in the future") != out["rejected"][0]
    session.commit()
    rejects = session.exec(select(ExtractionReject)).all()
    assert {r.field for r in rejects} >= {"description", "funding_announced"}
    writes = {w.field for w in session.exec(select(ExtractionWrite)).all()}
    assert {"description", "funding_stage", "funding_amount_usd_m"} <= writes
    meta = g.meta_of(c, "funding_stage")
    assert meta["channel"] == "feed" and meta["source"] == "https://news.example/acme"


def test_agreement_raises_confidence_and_a_conflict_is_recorded(session):
    c = Company(name="Acme", domain="acme.com")
    session.add(c)
    session.flush()
    g.set_funding(session, c, {"stage": "Series A", "evidence": "Acme raised a Series A"}, channel="feed")
    g.set_funding(session, c, {"stage": "Series A", "evidence": "Acme's Series A, announced"}, channel="exa-mcp")
    assert g.meta_of(c, "funding_stage")["sources"] == 2
    assert g.meta_of(c, "funding_stage")["confidence"] == "high"
    g.set_funding(session, c, {"stage": "Seed", "evidence": "a seed round"}, channel="exa-mcp")   # names no one
    assert c.funding_stage == "Series A"
    session.commit()
    assert session.exec(select(ExtractionReject).where(ExtractionReject.field == "funding")).first()


def test_vet_contact_keeps_the_handle_and_labels_the_inbox(session):
    c = Company(name="Acme", domain="acme.com")
    session.add(c)
    session.flush()
    out = g.vet_contact(session, company=c, name="kbysani-affirm", email_addr="kailash@gmail.com",
                        source="github", handle=None, last_commit_at=None)
    assert out["name"] is None and out["handle"] == "kbysani-affirm"
    assert out["affiliation"] == "unconfirmed" and out["confidence"] == "scraped"
    inbox = g.vet_contact(session, company=c, name=None, email_addr="careers@acme.com", source="site")
    assert inbox["affiliation"] == "generic" and inbox["referral_rank"] == 9


def test_completeness_counts_every_part():
    c = Company(name="Acme", description="x" * 80, funding_stage="Seed", funding_amount_usd_m=3,
                funding_announced="2026-01", hiring_status="verified", careers_url="https://acme.com/careers")
    score, missing = g.completeness(c, referrers=3, referrers_with_email=1)
    assert score == 80 and set(missing) == {"investors", "valuation"}


# ------------------------------------------------------------------ the audit's stall detection

def test_stall_when_the_tick_ran_and_nothing_was_written():
    assert audit.stall_check(progress_last_hour=0, tick_ran=True, channel_rates={})
    assert not audit.stall_check(progress_last_hour=0, tick_ran=False, channel_rates={})
    assert not audit.stall_check(progress_last_hour=12, tick_ran=True, channel_rates={})


def test_a_channel_failing_most_calls_alerts_but_a_small_sample_does_not():
    rates = {"exa-mcp": {"ok": 1, "fail": 19}, "jina": {"ok": 0, "fail": 3}, "direct": {"ok": 0, "fail": 50}}
    alerts = audit.stall_check(progress_last_hour=5, tick_ran=True, channel_rates=rates)
    assert len(alerts) == 1 and "exa-mcp" in alerts[0]
    assert audit.stall_check(progress_last_hour=5, tick_ran=True, channel_rates={},
                             openrouter={"ok": 1, "fail": 20})
