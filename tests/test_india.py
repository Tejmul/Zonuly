"""The India ₹20 LPA pay floor and the Bengaluru / Hyderabad city evidence (jobhunter/india.py).

Pure functions plus an in-memory SQLite session — the live jobhunter.db is never opened
for writing here.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlmodel import Session, SQLModel, create_engine

from jobhunter import guardrails, india
from jobhunter.db import Company


def job(title="Software Engineer", *, senior=False, intern=False, smin=None, smax=None, raw=None, ppo=None,
        stipend=None):
    return SimpleNamespace(title=title, is_senior=senior, is_internship=intern, salary_min_lpa=smin,
                           salary_max_lpa=smax, salary_raw=raw, ppo_lpa=ppo, stipend_inr_month=stipend)


def verdict(**kw):
    ev = india.pay_evidence(company_ppo=kw.pop("ppo", None), company_ppo_evidence=kw.pop("ppo_ev", None),
                            jobs=kw.pop("jobs", []), list_ctc=kw.pop("ctc", None))
    return india.pay_verdict(ev, floor=20)


# ------------------------------------------------------------------ the pay floor

def test_any_evidence_at_or_above_the_floor_keeps_the_company():
    state, best, _ = verdict(ppo=12, jobs=[job(smin=8, smax=14, raw="₹8L – ₹14L"), job("SDE 1", smax=24, raw="₹18L – ₹24L")])
    assert state == "ok" and best.lpa == 24
    # the list's own upper bound is evidence too
    assert verdict(ppo=15, ctc="₹8–20L+")[0] == "ok"


def test_all_evidence_under_the_floor_is_below_the_bar_and_quoted():
    state, best, why = verdict(jobs=[job("SDE-1", smin=10, smax=12, raw="₹10L – ₹12L")])
    assert state == "below" and best.lpa == 12
    assert "₹10L – ₹12L" in why and "SDE-1" in why


def test_internship_stipend_is_not_full_time_pay():
    # a stipend-only internship says nothing about full-time pay: pay unknown, kept
    assert verdict(jobs=[job("SDE Intern", intern=True, stipend=40000, smax=4.8)])[0] == "unknown"
    # but a stated PPO on an internship IS full-time pay
    assert verdict(jobs=[job("SDE Intern", intern=True, stipend=40000, ppo=24)])[0] == "ok"
    assert verdict(jobs=[job("SDE Intern", intern=True, stipend=40000, ppo=9)])[0] == "below"


def test_senior_roles_are_not_fresher_evidence():
    assert verdict(jobs=[job("Staff Engineer", senior=True, smax=60)])[0] == "unknown"
    assert verdict(jobs=[job("Staff Engineer", senior=True, smax=60), job("SDE 1", smax=12)])[0] == "below"


def test_unknown_pay_is_not_below_the_bar():
    state, best, why = verdict()
    assert state == "unknown" and best is None and "unknown" in why


def test_implausible_figures_are_ignored_not_used_to_hide():
    # "₹1L – ₹2L" on a senior-looking role is a monthly number misread as annual
    assert verdict(jobs=[job("Backend Engineer", smin=1, smax=2, raw="₹1L – ₹2L")])[0] == "unknown"


@pytest.mark.parametrize("ctc, upper", [
    ("₹6–18L+", 18), ("₹8–20L+", 20), ("₹30–60L (lead AI role)", 60), ("₹10–25L (AI role)", 25),
    ("₹12L", 12), ("verify on the live JD", None), (None, None),
])
def test_list_ctc_upper_bound(ctc, upper):
    assert india.ctc_upper(ctc) == upper


def test_list_ctc_decides_the_star_rows():
    assert verdict(ctc="₹6–18L+")[0] == "below"
    assert verdict(ctc="₹7–20L+")[0] == "ok"
    # a posting at ₹22L outweighs the list's ₹18L
    assert verdict(ctc="₹6–18L+", jobs=[job("SDE 1", smax=22)])[0] == "ok"


def test_scope_is_bengaluru_hyderabad_india_hq_or_the_star_list():
    assert india.in_scope(Company(name="A", city="Hyderabad"))
    assert india.in_scope(Company(name="B", hq_region="india"))
    assert india.in_scope(Company(name="C", list_key="india-star"))
    assert not india.in_scope(Company(name="D", hq_region="us"))


# ------------------------------------------------------------------ India city evidence

@pytest.mark.parametrize("text, city", [
    ("Bengaluru-based startup Vecton AI has secured ₹6 crore in a pre-seed round.", "Bengaluru"),
    ("Hyderabad-Based Bluecopa Raises $7.5 Million Series A to Scale AI", "Hyderabad"),
    ("Zenalyst, an enterprise AI platform based in Bengaluru, has raised Rs 3 crore", "Bengaluru"),
    ("Kily Bangalore, India · Artificial Intelligence (AI) Kily is a B2B SaaS platform", "Bengaluru"),
])
def test_a_quote_naming_the_company_and_the_city_is_evidence(text, city):
    name = text.split("startup ")[-1].split(" ")[0] if "startup " in text else None
    name = name or next(n for n in ("Bluecopa", "Zenalyst", "Kily") if n in text)
    chk = guardrails.india_city(text, name=name)
    assert chk and chk.value == city


def test_city_evidence_is_refused_without_the_city_or_the_name():
    assert not guardrails.india_city("Delhi-based Zinara raises pre-seed funding", name="Zinara")
    assert not guardrails.india_city("Mumbai startup Acme raises seed", name="Acme")
    # the sentence must be about THIS company
    assert not guardrails.india_city("Bengaluru-based Foo raises $2M", name="Acme")
    # another Indian city as the base, a target city only in passing: not evidence
    assert not guardrails.india_city("Pune-based Acme, whose investors include a Bengaluru fund, raised $2M",
                                     name="Acme")
    # but "based in Bengaluru" next to another city is
    assert guardrails.india_city("Acme, based in Bengaluru, opened a Pune office", name="Acme")
    # the company's own careers board need not repeat its name
    assert guardrails.india_city("Software Developer - Fresher, Hyderabad, Telangana", name="Acme", own_page=True)


def test_city_quote_picks_the_sentence_with_both():
    text = ("Vecton AI Raises ₹6 Crore Pre-Seed Funding. Bengaluru-based startup Vecton AI has secured ₹6 crore "
            "in a pre-seed round led by Zeropearl VC. The funds will be used to hire.")
    assert india.city_quote(text, "Vecton AI").startswith("Bengaluru-based startup Vecton AI")
    assert india.city_quote("Vecton AI raises ₹6 crore. It is based in Bengaluru.", "Vecton AI") is None


def test_discovery_admits_only_with_city_evidence_and_sets_city():
    eng = create_engine("sqlite://")
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        run = india.Run(limit=10)
        ok = india._admit(s, run, india.Cand(
            name="Bluecopa", kind="search", url="https://example.com/a",
            text="Hyderabad-Based Bluecopa Raises $7.5 Million Series A. Bluecopa, a Hyderabad-based FinOps "
                 "startup, raised $7.5 million in a Series A led by Analog Partners in March 2026."))
        assert ok is not None and ok.city == "Hyderabad" and ok.india_found_at is not None
        assert ok.funding_stage == "Series A"
        # no target city: not admitted as an India target
        no = india._admit(s, run, india.Cand(name="Zinara", kind="rss", url="https://example.com/b",
                                             text="Delhi NCR based Zinara raises pre-seed funding for its AI app"))
        assert no is None
        assert run.by_source["rss"]["skipped"].get("no Bengaluru/Hyderabad evidence") == 1
        # a famous name never gets in, whatever the city
        assert india._admit(s, run, india.Cand(name="Flipkart", kind="search", url="https://example.com/c",
                                               text="Bengaluru-based Flipkart raises $1 billion")) is None


@pytest.mark.parametrize("text, usd_m", [
    ("raised $7.5 million", 7.5), ("raises ₹32 Cr", round(32e7 / 88 / 1e6, 3)), ("roughly $300,000, in", 0.3),
    ("USD 500 thousand", 0.5), ("$1.3 Mn", 1.3), ("no money here", None),
])
def test_amounts(text, usd_m):
    assert india.amount_usd_m(text) == usd_m


def test_directory_rows_parse_with_city_round_and_domain():
    page = ("[K Kily Bengaluru, India · Artificial Intelligence (AI) Kily is a B2B SaaS platform that provides "
            "autonomous AI agents. Seed·Aug 2026 $3.1M](https://fundediq.co/kily-kily-ai-funding/)")
    rows = india.parse_directory(page)
    assert rows and rows[0]["name"] == "Kily" and rows[0]["domain"] == "kily.ai"
    assert rows[0]["announced"] == "2026-08" and rows[0]["amount_usd_m"] == 3.1 and rows[0]["stage_raw"] == "Seed"


@pytest.mark.parametrize("title, name", [
    ("Bengaluru's Coreworks AI Raises $5M Seed for Natural Language BI", "Coreworks AI"),
    ("Hyderabad-Based Bluecopa Raises $7.5 Million Series A", "Bluecopa"),
    ("Bangalore AI Startup raises $2M", None),            # a description, not a name
    ("Solara in talks to raise $20-25 million", None),     # not a round yet
    ("Top 30 Funded AI Startups In Bengaluru (2026)", None),
])
def test_headline_names(title, name):
    assert india.headline_name(title) == name


def test_an_aggregator_figure_alone_never_hides_a_company():
    from jobhunter import india

    ev = india.pay_evidence(company_ppo=13.8, jobs=[],
                            company_ppo_evidence="levels.fyi Fellow Engineer: $15,634 total comp (https://www.levels.fyi/x)")
    assert india.pay_verdict(ev)[0] == "unknown"
    ev = india.pay_evidence(company_ppo=26, jobs=[], company_ppo_evidence="levels.fyi SDE1: $29,500 total comp")
    assert india.pay_verdict(ev)[0] == "ok"          # it can still keep one


def test_a_us_posting_is_not_india_pay():
    us = SimpleNamespace(title="Software Engineer", is_senior=False, is_internship=False, salary_min_lpa=150,
                         salary_max_lpa=250, salary_raw="$170K – $245K", ppo_lpa=None, location="San Francisco",
                         currency=None)
    blr = SimpleNamespace(title="Software Engineer", is_senior=False, is_internship=False, salary_min_lpa=10,
                          salary_max_lpa=20, salary_raw="₹10L – ₹20L", ppo_lpa=None, location="Bengaluru",
                          currency=None)
    assert verdict(jobs=[us])[0] == "unknown"           # dollars in California prove nothing here
    assert verdict(jobs=[us, blr])[0] == "ok"           # the Bengaluru figure decides (floor 20 in these tests)
