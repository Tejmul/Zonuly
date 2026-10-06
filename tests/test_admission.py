"""The admission gate, the stage vocabulary and the overrated rule (jobhunter/admission.py).

Pure functions plus an in-memory SQLite session for dedup — the live jobhunter.db is
never opened for writing here.
"""

from __future__ import annotations

import pytest
from sqlmodel import Session, SQLModel, create_engine

from jobhunter import admission as a
from jobhunter.db import Company


# ------------------------------------------------------------------ names

@pytest.mark.parametrize("raw, clean", [
    ("Swiss startup Jaipur Robotics", "Jaipur Robotics"),
    ("Milan-based biotech Aptadir Therapeutics", "Aptadir Therapeutics"),
    ("Crusoe reportedly", "Crusoe"),
    ("Swish to", "Swish"),
    ("[Update] Moneyview IPO", "Moneyview"),
    ("Netherlands-based AI startup Wonderful", "Wonderful"),
    ("Robbie Williams-backed Tickets for Good", "Tickets for Good"),
    ("IPO-Bound StockGro", "StockGro"),
    ("Dutch payments technology firm Silverflow", "Silverflow"),
    ("Dutch Deep Tech Startup RIFT", "RIFT"),
    ("Lithuanian legaltech EnforceShield", "EnforceShield"),
    ("Profitable Belgian CleanTech Octave.energy", "Octave.energy"),
    ("German AI startup mika", "mika"),
    ("Women’s wellness brand Nua", "Nua"),
    ("Even Healthcare to", "Even Healthcare"),
    ("Tandem Health", "Tandem Health"),
])
def test_headline_names_are_cleaned(raw, clean):
    assert a.clean_name(raw, headline=True)[0] == clean


@pytest.mark.parametrize("raw", [
    "newest unicorn", "India's newest unicorn", "Crane Venture Partners", "Protego Ventures",
    "WEH Ventures Announces First", "TechSparks", "Top European funding rounds", "6 days left to",
    "Developer Tools startups that", "CEE’s biggest checks", "After €387 million", "Swedish company",
    "semiconductor sector", "London’s AI", "Hiring Alert", "100% Remote Jobs Worldwide", "Bengaluru",
])
def test_headline_junk_is_refused(raw):
    name, why = a.clean_name(raw, headline=True)
    assert name is None, (raw, why)


def test_trusted_sources_keep_their_own_names():
    # a board or directory names the company in its own words: no headline surgery
    assert a.clean_name("Insight Partners", source="ats")[0] == "Insight Partners"
    assert a.clean_name("Acme (YC W24)", source="yc")[0] == "Acme"
    assert a.clean_name("harnessio", source="x")[0] == "harnessio"


def test_name_key_folds_suffixes_and_spacing():
    assert a.name_key("RevenueCat") == a.name_key("Revenue Cat") == a.name_key("RevenueCat, Inc.")
    assert a.name_key("Acme Labs") == a.name_key("Acme AI") == "acme"
    assert a.name_key("Acme") != a.name_key("Acme Robotics")


# ------------------------------------------------------------------ stages

@pytest.mark.parametrize("text, stage", [
    ("Series A / YC", "Series A"), ("Seed / YC", "Seed"), ("Series B / Pre-B", "Series B"),
    ("Series A/B", "Series B"), ("series a", "Series A"), ("pre seed", "Pre-seed"), ("Pre-seed", "Pre-seed"),
    ("Seed Round", "Seed"), ("Pre-Series A", "Seed"), ("angel", "Pre-seed"), ("Series F", "Series D+"),
    ("growth", "Series D+"), ("IPO", "Public"), ("bootstrapped", "Bootstrapped"), ("YC W24", "Seed"),
    ("strategic", None), ("debt", None), ("", None), (None, None),
])
def test_stage_normaliser(text, stage):
    assert a.normalize_stage(text) == stage


def test_funding_card_reads_total_and_ignores_revenue():
    ev = ("- Annual Revenue: USD 2,000,000 - Total Funding: USD 28,500,000 - "
          "Series a (2023-09-12): USD 20,000,000 - Seed Round (2021-01-01): USD 3,000,000")
    card = a.parse_funding_card(ev)
    assert card["total_usd_m"] == 28.5 and card["stage"] == "Series A"
    assert card["amount_usd_m"] == 20.0 and card["announced"] == "2023-09-12"
    # the stored $2,000M was the revenue line misread: the card wins
    f = a.funding_facts(stage=None, amount_usd_m=2000.0, total_usd_m=None, announced=None,
                        valuation_usd_m=None, evidence=ev)
    assert f["amount_usd_m"] == 20.0 and f["total_usd_m"] == 28.5


# ------------------------------------------------------------------ overrated

@pytest.mark.parametrize("name", ["Stripe", "OpenAI", "Google DeepMind", "Scale AI", "Anysphere", "Zepto",
                                  "Wipro Limited", "Ramp", "Cohere"])
def test_famous_names_are_overrated(name):
    assert a.overrated_verdict(name).overrated


@pytest.mark.parametrize("name", ["Ramp Network", "Cohere Health", "Hex Security", "Acme Robotics"])
def test_lookalikes_are_not_famous(name):
    assert a.famous(name) is None


def test_listed_valued_and_megafunded_are_overrated():
    assert a.overrated_verdict("Sezzle", evidence="- IPO Date: 2019-07-30 - Total Funding: USD 851,600,000").overrated
    assert a.overrated_verdict("Acme", valuation_usd_m=6000).overrated
    assert a.overrated_verdict("Acme", amount_usd_m=1200, evidence="Acme raised $1.2B in a Series E").overrated


def test_borderline_needs_hiring_and_pay():
    kw = dict(amount_usd_m=500, evidence="Acme raises $500M Series D")
    assert a.overrated_verdict("Acme", **kw).overrated                     # no hiring evidence
    assert a.overrated_verdict("Acme", open_roles=5, pay_band="thin", **kw).overrated
    v = a.overrated_verdict("Acme", open_roles=5, pay_band="deep_pockets", **kw)
    assert not v.overrated and v.borderline
    assert not a.overrated_verdict("Acme", hiring_verified=True, pay_band="pays", valuation_usd_m=3000).overrated


def test_eligible_and_unknown_funding_is_not_disqualifying():
    assert not a.overrated_verdict("Acme", stage="Seed", amount_usd_m=4).overrated
    assert not a.overrated_verdict("Acme").overrated
    # $1B of transaction volume is not $1B raised
    assert not a.overrated_verdict("Zerion", amount_usd_m=1000,
                                   evidence="Zerion has processed over $1 billion in transaction volume").overrated


# ------------------------------------------------------------------ sites

def test_bogus_hosts_and_name_fit():
    for host in ("jobs.ashbyhq.com", "grnh.se", "forms.gle", "github.com", "youtube.com", "app.dover.com", "bit.ly"):
        assert a.bogus_host(host), host
    assert not a.bogus_host("acme.com")
    assert a.host_fits_name("Acme", "getacme.io") and a.host_fits_name("Exa", "exa.ai")
    assert not a.host_fits_name("SpaceXAI", "exa.ai") and not a.host_fits_name("Wonderful", "exa.ai")


def test_india_two_cities():
    assert a.india_city("Bangalore, India") == "Bengaluru"
    assert a.india_city("Secunderabad") == "Hyderabad"
    assert a.india_city("Gurugram, India") is None and a.india_other_city("Gurugram, India") == "Gurugram"


# ------------------------------------------------------------------ the gate + dedup

@pytest.fixture()
def session():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_gate_dedups_twins_in_one_batch(session):
    v = a.admit(a.Candidate(name="RevenueCat", source="ats", website="https://www.revenuecat.com"), session)
    assert v.ok and v.existing is None and v.domain == "revenuecat.com"
    c = Company(name=v.name)
    a.stamp(c)
    session.add(c)
    a.remember(session, c)
    # same batch, before any commit: the twin finds the row (the old code inserted it again)
    twin = a.admit(a.Candidate(name="Revenuecat, Inc.", source="exa"), session)
    assert twin.ok and twin.existing is not None and twin.existing.id == c.id


def test_gate_dedups_by_domain_and_follows_merges(session):
    keep = Company(name="Heron", domain="herondata.io", website="https://herondata.io")
    session.add(keep)
    session.flush()
    a.remember(session, keep)
    assert a.find_existing(session, "Heron Data", "herondata.io").id == keep.id
    loser = Company(name="Heron Labs Old", status="merged", merged_into=keep.id)
    session.add(loser)
    session.flush()
    assert a.find_existing(session, "Heron Labs Old").id == keep.id


def test_gate_refuses_famous_junk_and_bogus_sites(session):
    assert not a.admit(a.Candidate(name="Stripe", source="ats"), session).ok
    assert not a.admit(a.Candidate(name="Crane Venture Partners", source="funding-rss", headline=True), session).ok
    v = a.admit(a.Candidate(name="Wonderful", source="hn", website="https://exa.ai/careers", website_is_link=True), session)
    assert v.ok and v.website is None
    v = a.admit(a.Candidate(name="Acme", source="hn", website="https://jobs.ashbyhq.com/acme"), session)
    assert v.ok and v.website is None
    ok, reason = a.admit(a.Candidate(name="Acme", source="hn"), session)
    assert ok and reason


def test_gate_respects_quarantine_and_list_rows(session):
    session.add(Company(name="Junkco", status="quarantined", status_reason="junk"))
    session.add(Company(name="Coupang", list_key="india-star"))
    session.flush()
    session.info.pop("admission_index", None)
    assert not a.admit(a.Candidate(name="Junkco", source="ats"), session).ok
    assert a.admit(a.Candidate(name="Coupang", source="ats"), session).ok
    session.add(Company(name="Swiggy", list_key="india-star"))   # famous, but hand-picked
    session.flush()
    session.info.pop("admission_index", None)
    assert a.admit(a.Candidate(name="Swiggy", source="ats"), session).ok


def test_bogus_host_spares_the_company_own_domain():
    assert not a.bogus_host("gitlab.com", "GitLab")
    assert not a.bogus_host("notion.so", "Notion")
    assert a.bogus_host("gitlab.com", "Acme")
    assert a.bogus_host("forms.gle")
