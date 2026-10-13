"""Selection confidence (confidence.py) and the needs brief (needs.py).

Pure functions over SimpleNamespace rows and a fixed test profile — the live jobhunter.db
and profile/profile.json are never read, and no model or network is called.
"""

from __future__ import annotations

import json
from datetime import timedelta
from types import SimpleNamespace

import pytest

from jobhunter import confidence as cf
from jobhunter import needs
from jobhunter.db import utcnow
from jobhunter.outreach import drafter, gates

PROFILE = {
    "name": "Tejmul Movin",
    "experience": [
        {"title": "AI Product Engineer Intern", "company": "MVRX Labs (YC-P26)",
         "highlights": ["Built agentic lead-gen pipeline (Apify scraping + ICP filtering); migrated Trigger.dev to Temporal, cutting infra cost 95%",
                        "Shipped 3 production agentic systems solo for a live GTM agency serving 10+ YC clients"]},
        {"title": "AIML Intern", "company": "TheSmartBridge",
         "highlights": ["Engineered data pipelines using Python, Pandas, AWS (S3, Lambda), reducing manual processing time by 70%",
                        "Built a RAG-powered chatbot using vector embeddings, FAISS, and LoRA fine-tuning"]},
    ],
    "projects": [{"name": "Zonuly", "description": "Multi-agent system with OpenRouter LLMs and FastAPI", "tech": ["OpenRouter"]}],
    "skills": {"ai_ml": ["LangChain", "LLM", "RAG"], "backend": ["FastAPI", "Next.js"], "languages": ["Python", "TypeScript"]},
}

JD = """About us
We are building AI agents for logistics teams.

What you'll do
- Build LLM-powered agents in Python
- Own our RAG pipeline end to end

Requirements
- 0-2 years of experience
- Python and LangChain
- Kubernetes in production

Nice to have
- Experience with Terraform
"""


def job(**kw):
    base = dict(id=1, title="AI Engineer", company_name="Acme", company_id=None, location="Bengaluru", remote=False,
                remote_anywhere=False, description=JD, url="https://jobs.example/acme/1", source="ashby",
                posted_at=utcnow() - timedelta(days=3), scraped_at=utcnow(), salary_min_lpa=None, salary_max_lpa=None,
                salary_raw=None, is_internship=False, stipend_inr_month=None, ppo_lpa=None, match_score=None,
                embed_sim=0.08, needs_brief=None, needs_brief_at=None)
    base.update(kw)
    return SimpleNamespace(**base)


def company(**kw):
    base = dict(id=7, name="Acme", website="https://acme.example", domain="acme.example", hiring_status="verified",
                hiring_evidence="Open roles: AI Engineer", careers_url="https://acme.example/careers", overrated=False,
                overrated_reason=None, underrated=True, list_key=None, status=None, status_reason=None,
                do_not_contact=None, dnc_reason=None, team_size=None, team_size_evidence=None, funding_stage="Seed",
                funding_amount_usd_m=4.0, funding_evidence=None, valuation_usd_m=None, ppo_lpa=None, ppo_evidence=None,
                pay_power_band=None, pay_power_why=None, hq_region="india", city="Bengaluru", description=None,
                story=None, story_evidence=None, needs_context=None, needs_context_at=None, github_org=None)
    base.update(kw)
    return SimpleNamespace(**base)


def score(j, c=None, ref=None):
    c = company() if c is None else c
    brief = needs.build_brief(j, c, profile=PROFILE)
    return cf.compute(j, c, ref=ref or {}, brief=brief)


# ------------------------------------------------------------------ fresher eligibility

@pytest.mark.parametrize("text,min_years,fresher", [
    ("We want 0-2 years of experience in backend work.", 0, True),
    ("You have 5+ years of professional experience shipping software.", 5, False),
    ("Open to 2026 graduates from any college.", None, True),
    ("Freshers welcome!", None, True),
    ("3+ years experience building data systems; Senior: 6+ years of experience.", 3, False),
    ("Generous equity with a 4-year vesting schedule.", None, False),
    ("Minimum of two years of industry experience.", 2, False),
])
def test_experience_parsing(text, min_years, fresher):
    exp = needs.parse_experience(text)
    assert exp.min_years == min_years
    assert exp.fresher is fresher


# ------------------------------------------------------------------ markets and weights

def test_market_picks_the_weight_profile():
    india = score(job(location="Bengaluru"))
    remote = score(job(location="Remote — anywhere in the world", remote=True, remote_anywhere=True))
    assert india.market == "india" and remote.market == "remote"
    assert {t.key for t in india.terms} == set(cf.weights("india"))
    assert {t.key for t in remote.terms} == set(cf.weights("remote"))
    assert "city" in {t.key for t in india.terms} and "timezone" in {t.key for t in remote.terms}
    assert abs(sum(cf.weights("india").values()) - 100) < 1e-6
    assert abs(sum(cf.weights("remote").values()) - 100) < 1e-6


def test_a_ready_referrer_is_worth_its_weight_in_each_market():
    for loc, market in (("Bengaluru", "india"), ("Remote, worldwide", "remote")):
        j = job(location=loc, remote_anywhere=market == "remote")
        none = score(j, ref={})
        ready = score(j, ref={"people": 3, "ready": 1, "ready_verified": 1, "best": "A (engineer, verified)"})
        assert ready.market == market
        assert ready.raw - none.raw == pytest.approx(cf.weights(market)["referral"], abs=0.2)


def test_fresher_role_beats_the_same_role_asking_for_years():
    fresh = score(job())
    seasoned = score(job(description=JD.replace("0-2 years", "4+ years")))
    assert fresh.score > seasoned.score


# ------------------------------------------------------------------ blockers cap

def test_visa_and_country_restrictions_cap_a_remote_role():
    j = job(location="Remote - United States", remote=True,
            description=JD + "\nYou must be authorized to work in the United States.")
    s = score(j)
    kinds = {b.kind for b in s.blockers}
    assert {"visa", "remote_country"} <= kinds
    assert s.score <= cf.CAPS["visa"] and s.band == "long_shot"
    assert s.main_blocker()


def test_latam_only_remote_is_not_remote_from_india():
    s = score(job(location="Remote (LATAM)", remote=True, remote_anywhere=True))
    assert s.market == "remote"
    assert any(b.kind == "remote_country" for b in s.blockers)


def test_five_plus_years_and_senior_titles_cap():
    s = score(job(description=JD.replace("0-2 years", "5+ years")))
    assert s.score <= cf.CAPS["years_5plus"]
    s = score(job(title="Senior Staff Engineer", description=JD.replace("- 0-2 years of experience\n", "")))
    assert s.score <= cf.CAPS["senior_title"]
    # a "senior" title that itself asks ≤ 2 years is capped more gently, and says so
    s = score(job(title="Senior Associate AI Engineer"))
    assert any(b.kind == "senior_title" and "≤ 2 years" in b.why for b in s.blockers)


def test_onsite_abroad_is_capped_and_overrated_companies_too():
    s = score(job(location="San Francisco, CA"), company(hq_region="us"))
    assert s.market == "abroad" and s.score <= cf.CAPS["onsite_elsewhere"]
    s = score(job(), company(overrated=True, overrated_reason="famous"))
    assert s.score <= cf.CAPS["overrated"]


def test_india_pay_under_the_floor_caps():
    s = score(job(salary_min_lpa=8, salary_max_lpa=12, salary_raw="₹8L – ₹12L"))
    assert any(b.kind == "below_pay" for b in s.blockers)
    ok = score(job(salary_min_lpa=20, salary_max_lpa=30, salary_raw="₹20L – ₹30L"))
    assert ok.score > s.score and not any(b.kind == "below_pay" for b in ok.blockers)


def test_every_term_says_why():
    s = score(job())
    assert all(t.why for t in s.terms)
    d = s.as_dict()
    assert d["band_label"] in ("High", "Medium", "Low", "Long shot")
    assert len(d["top_reasons"]) <= 3


# ------------------------------------------------------------------ gap mapping

def test_needs_map_to_resume_lines_or_uncovered():
    b = needs.build_brief(job(), company(), profile=PROFILE)
    by_skill = {}
    for n in b["stated"]:
        for s in n["skills"]:
            by_skill.setdefault(s, n)
    assert by_skill["Python"]["status"] in ("covered", "partial")
    assert "Python" in (by_skill["Python"]["evidence"] or "")
    assert by_skill["Kubernetes"]["status"] == "uncovered" and by_skill["Kubernetes"]["evidence"] is None
    lc = next(n for n in b["stated"] if n["skills"] == ["Python", "LangChain"])
    assert lc["status"] == "partial"           # LangChain is only on the skills list, never shipped
    assert "Kubernetes" in b["forbidden"] and "Python" not in b["forbidden"]
    # every need carries its quote and where it was read
    assert all(n["quote"] and n["url"] for n in b["stated"] + b["implied"])


def test_alternatives_need_only_one():
    j = job(description="Requirements\n- Exposure to cloud platforms such as AWS, Azure, or GCP\n")
    b = needs.build_brief(j, None, profile=PROFILE)
    n = b["stated"][0]
    assert n["status"] == "covered" and not n["missing_skills"]


def test_helps_is_built_only_from_covered_needs():
    b = needs.build_brief(job(), company(), profile=PROFILE)
    assert b["helps"] and "Kubernetes" not in b["helps"] and "Terraform" not in b["helps"]
    assert "Tejmul" in b["helps"]


# ------------------------------------------------------------------ the model: fallback and rejection

def test_brief_falls_back_without_a_model(monkeypatch):
    from jobhunter import llm

    def ceiling(*a, **k):
        raise llm.BudgetExceeded("free-models-per-day")

    monkeypatch.setattr(llm, "chat_json", ceiling)
    b = needs.build_brief(job(), company(), synth=True, profile=PROFILE)
    assert b["synthesis"] == "pending"
    assert b["helps"] and b["stated"]          # the deterministic brief stands
    assert "model" not in b


def test_model_synthesis_claiming_an_uncovered_need_is_dropped(monkeypatch):
    from jobhunter import llm

    monkeypatch.setattr(llm, "chat_json", lambda *a, **k: {
        "building": "Acme is building AI agents for logistics teams.",
        "helps": "Tejmul ran Kubernetes clusters in production for years.",
        "angles": ["Lead with Kubernetes."], "implied": []})
    b = needs.build_brief(job(), company(), synth=True, profile=PROFILE)
    assert "helps_model" not in b
    assert any("Kubernetes" in r for r in b["model"].get("rejected", []))
    assert all("Kubernetes" not in a for a in b["angles"])


# ------------------------------------------------------------------ the drafter and the gate

def test_gate_flags_a_draft_claiming_an_uncovered_skill():
    corpus = "Tejmul built agentic pipelines in Python. Kubernetes in production."   # the JD quote is in the corpus
    body = "Hi Sam,\n\nI built agentic pipelines in Python and have run Kubernetes in production. Would you refer me?"
    v = gates.check(body, corpus=corpus, forbidden=["Kubernetes"])
    assert "Kubernetes" in v.forbidden_hits
    assert any(f["kind"] == "uncovered_claim" for f in v.flags) and v.blocked
    clean = gates.check("Hi Sam,\n\nI built agentic pipelines in Python. Would you refer me?", corpus=corpus,
                        forbidden=["Kubernetes"])
    assert not clean.forbidden_hits


def test_drafter_hook_offers_only_covered_needs():
    b = needs.build_brief(job(), company(), profile=PROFILE)
    hook = drafter.needs_hook(json.dumps(b))
    assert "Kubernetes" in hook["forbidden"]
    assert "Kubernetes" not in hook["block"]
    assert hook["corpus"] and hook["evidence"]["covered"]
    assert all(c["status"] in ("covered", "partial") for c in hook["evidence"]["covered"])
    assert drafter.needs_hook(None)["forbidden"] == []
