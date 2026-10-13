"""India job postings (jobhunter/scrapers/india_jobs.py, jobhunter/india_jobs.py).

Pure functions over saved fixtures — nothing is fetched and the live jobhunter.db is
never opened for writing here.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from jobhunter import admission, targeting
from jobhunter import normalize as norm
from jobhunter.scrapers import india_jobs as ij

FIX = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 13, 12, 0)


# ------------------------------------------------------------------ salary parsing

@pytest.mark.parametrize("text,lo,hi,cur", [
    ("₹37 LPA", 37, 37, "INR"),
    ("₹30–40 lakh", 30, 40, "INR"),
    ("CTC: 3,700,000 INR per annum", 37, 37, "INR"),
    ("Salary: ₹30,00,000 - ₹40,00,000 per year", 30, 40, "INR"),
    ("₹2,500,000.00/yr - ₹3,500,000.00/yr", 25, 35, "INR"),     # LinkedIn's "Base pay range"
    ("12 - 18 Lacs P.A.", 12, 18, "INR"),
])
def test_rupee_salaries(text, lo, hi, cur):
    s = norm.parse_salary(text)
    assert (s.min_lpa, s.max_lpa, s.currency) == (lo, hi, cur)


def test_dollar_salary_converts_to_lpa():
    s = norm.parse_salary("$45k")
    assert s.currency == "USD" and 35 < s.min_lpa < 45


# ------------------------------------------------------------------ who is not a company to chase

@pytest.mark.parametrize("name,text,industry,headcount,why", [
    ("Confidential", None, None, None, "confidential poster"),
    ("Hiring for a client", None, None, None, "confidential poster"),
    ("Weekday (YC W21)", None, None, None, "staffing / recruitment agency"),
    ("Apex Staffing Solutions", None, None, None, "staffing / recruitment agency"),
    ("Acme HR Consultants", None, None, None, "staffing / recruitment agency"),
    ("Talentify", None, "Staffing and Recruiting", None, "staffing / recruitment agency (LinkedIn industry: Staffing and Recruiting)"),
    ("Acme AI", "We are hiring for one of our clients, a Series B fintech in Bengaluru.", None, None,
     "posts on behalf of a client"),
    ("Brightpath Infotech", None, "IT Services and IT Consulting", None, "IT-services body shop"),
    ("Nineleaps", "an IT services company and staff augmentation partner", None, 500, "IT-services body shop"),
])
def test_body_shops_are_turned_away(name, text, industry, headcount, why):
    assert admission.body_shop(name, text, industry=industry, headcount=headcount) == why


@pytest.mark.parametrize("name,text,industry,headcount", [
    ("Gitforce", "Hi, we're a technology services company with more than 3 years of experience", "IT Services and IT Consulting", 50),
    ("Sarvam AI", "We build foundation models for India.", "Technology, Information and Internet", 200),
    ("Composio", None, None, None),
])
def test_product_and_small_companies_pass(name, text, industry, headcount):
    assert admission.body_shop(name, text, industry=industry, headcount=headcount) is None


# ------------------------------------------------------------------ LinkedIn parsing

def test_linkedin_search_cards_from_the_jina_fixture():
    cards = ij.parse_cards((FIX / "linkedin_search_gitforce.md").read_text(), now=NOW)
    assert len(cards) == 10
    gf = [c for c in cards if c.company == "Gitforce"]
    assert [c.title for c in gf] == ["Software Engineer – AI (Backend)", "Software Engineer – AI & Data Science"]
    first = gf[0]
    assert first.url == "https://www.linkedin.com/jobs/view/4475618763" and first.job_id == "4475618763"
    assert first.location == "Hyderabad, Telangana, India"
    assert first.company_url == "https://in.linkedin.com/company/gitforce"
    assert ij.company_slug(first.company_url) == "gitforce"
    assert first.posted_at.date() == datetime(2026, 10, 11).date()       # "2 days ago"
    assert admission.india_city(first.location) == "Hyderabad"


def test_linkedin_search_cards_from_html():
    html = """<ul><li><div class="base-card job-search-card" data-entity-urn="urn:li:jobPosting:4475630259">
      <a class="base-card__full-link" href="https://in.linkedin.com/jobs/view/sw-at-gitforce-4475630259?position=1"></a>
      <h3 class="base-search-card__title"> Software Engineer - Frontend Focus </h3>
      <h4 class="base-search-card__subtitle"><a href="https://in.linkedin.com/company/gitforce?trk=x">Gitforce</a></h4>
      <span class="job-search-card__location">Hyderabad, Telangana, India</span>
      <span class="job-search-card__salary-info">₹3,000,000.00/yr - ₹4,000,000.00/yr</span>
      <time class="job-search-card__listdate" datetime="2026-10-12">1 day ago</time></div></li></ul>"""
    (c,) = ij.parse_cards(html)
    assert (c.title, c.company, c.job_id) == ("Software Engineer - Frontend Focus", "Gitforce", "4475630259")
    assert c.company_url == "https://in.linkedin.com/company/gitforce"
    assert norm.parse_salary(c.salary_raw).max_lpa == 40


def test_linkedin_job_page():
    page = ij.parse_job_page((FIX / "linkedin_job_gitforce.md").read_text())
    assert page["seniority"] == "Entry level" and page["employment_type"] == "Full-time"
    assert page["industry"] == "IT Services and IT Consulting"
    assert "work-from-office role in Hyderabad" in page["description"]
    assert "Join or sign in" not in page["description"]
    assert page["salary_raw"] is None                     # the posting states no pay


def test_linkedin_job_page_base_pay_range():
    text = "About the job\n\nBuild things.\n\nBase pay range\n\n₹3,300,000.00/yr - ₹3,700,000.00/yr\n\nSeniority level\n\nEntry level\n"
    page = ij.parse_job_page(text)
    assert page["salary_raw"].startswith("₹3,300,000.00/yr")
    assert norm.parse_salary(page["salary_raw"]).max_lpa == 37


def test_linkedin_company_page_gives_the_website():
    info = ij.parse_company_page((FIX / "linkedin_company_gitforce.md").read_text())
    assert info["website"] == "https://gitforce.ai"
    assert info["hq"] == "Hyderabad, Telangana" and info["headcount"] == 50
    assert info["industry"] == "IT Services and IT Consulting"


def test_login_wall_and_challenge_are_walls():
    assert ij.walled('<meta http-equiv="refresh" content="1;url=https://www.linkedin.com" />')
    assert ij.walled("")
    assert not ij.walled((FIX / "linkedin_search_gitforce.md").read_text())


def test_search_url_filters_entry_level_past_week():
    u = ij.search_url("software engineer", ij.GEO["Bengaluru"])
    assert "f_E=1%2C2" in u and "f_TPR=r604800" in u and "Bengaluru%2C%20Karnataka" in u


# ------------------------------------------------------------------ boards and leads

def test_jobfound_band_is_parsed_as_a_listing():
    text = ("*   [](https://jobfound.org/job/gitforce-is-hiring-for-software-engineer-frontend-focus-hyderabad)G\n\n"
            "### Software Engineer - Frontend Focus\n\nGitforce·Hyderabad, Telangana·2 days ago \n"
            "Full-time 3-5 LPA Fresher Software Engineer   \n")
    (c,) = ij.parse_jobfound(text, now=NOW)
    assert (c["company"], c["location"], c["listed_band"], c["kind"]) == ("Gitforce", "Hyderabad, Telangana", "3-5 LPA", "Full-time")


def test_offer_posts_yield_names_not_numbers():
    names = ij.offer_leads("Got SDE 1 offer from Sarvam AI for 37 LPA in Bengaluru — should I take it?")
    assert "Sarvam AI" in names
    assert not any(n.lower() in ("bengaluru", "lpa", "sde") for n in names)


# ------------------------------------------------------------------ the stated-pay boost

def test_a_posting_stating_30_lpa_or_more_boosts_priority():
    jobs = [SimpleNamespace(title="SDE-1", is_senior=False, is_internship=False, salary_min_lpa=33, salary_max_lpa=37),
            SimpleNamespace(title="Staff Engineer", is_senior=True, is_internship=False, salary_min_lpa=80, salary_max_lpa=90),
            SimpleNamespace(title="SDE Intern", is_senior=False, is_internship=True, salary_min_lpa=40, salary_max_lpa=40)]
    stated = targeting.stated_pay(jobs)
    assert stated == (37.0, "SDE-1")
    kw = dict(overrated=None, stage=None, announced=None, amount_usd_m=None, valuation_usd_m=None,
              hiring_verified=False, roles=1, fresher_roles=1, anywhere_roles=0, pay_score=0, pay_band="unknown")
    base, _ = targeting.priority(**kw)
    boosted, why = targeting.priority(**kw, stated=stated)
    assert boosted == base + 15 and "posting states ₹37 LPA for SDE-1" in why
    low, _ = targeting.priority(**kw, stated=(24.0, "SDE-1"))
    assert low == base


def test_fingerprint_keeps_role_suffixes_and_drops_places():
    fp = norm.fingerprint
    roles = ["Software Engineer – AI (Backend)", "Software Engineer – AI & Data Science", "Software Engineer - Frontend Focus"]
    assert len({fp("Gitforce", t) for t in roles}) == 3
    same = ["Software Engineer", "Software Engineer - Bengaluru", "Software Engineer (Remote)",
            "Software Engineer - Hyderabad, India", "Software Engineer - R12345"]
    assert len({fp("Gitforce", t) for t in same}) == 1
