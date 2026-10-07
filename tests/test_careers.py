"""The careers pass: recognising boards and careers links, reading roles without inventing
any, the list-first fill order, and the evidence-linked valuation / headcount readers."""

from __future__ import annotations

from sqlmodel import Session, SQLModel, create_engine

from jobhunter import careers
from jobhunter.db import Company, Contact, fill_order
from jobhunter.scrapers import careers_ats as ca


def test_detect_ats_knows_the_indian_and_global_boards():
    assert ca.detect_ats("https://acme.keka.com/careers/jobdetails/123") == ("keka", "acme")
    assert ca.detect_ats('<iframe src="https://acme.darwinbox.in/ms/candidate/careers">') == ("darwinbox", "acme")
    assert ca.detect_ats("https://acme.zohorecruit.in/jobs/Careers") == ("zohorecruit", "acme")
    assert ca.detect_ats("https://apply.workable.com/acme-ai/j/ABC/") == ("workable", "acme-ai")
    assert ca.detect_ats("https://jobs.lever.co/stable-money1/uuid") == ("lever", "stable-money1")
    assert ca.detect_ats("https://boards.greenhouse.io/embed/job_board?for=acme") == ("greenhouse", "acme")
    assert ca.detect_ats("https://acme.freshteam.com/jobs") == ("freshteam", "acme")
    # path words are not boards
    assert ca.detect_ats("https://www.workable.com/pricing") is None
    assert ca.detect_ats("https://example.com/careers") is None


def test_careers_links_reads_paths_and_anchor_text_on_their_own_site_only():
    html = """<a href="/products/gig-hiring/">Gig hiring</a> <a href="/careers">Careers</a>
              <a href="https://other.com/careers">Partner careers</a> <a href="/team">Join us</a>
              <a href="https://acme.notion.site/Jobs-123">Open roles</a>"""
    found = ca.careers_links(ca.links(html, "https://acme.in"), "https://acme.in")
    assert found[0] == "https://acme.in/careers"
    assert "https://acme.in/team" in found and "https://acme.notion.site/Jobs-123" in found
    assert not any("gig-hiring" in u or "other.com" in u for u in found)


def test_jsonld_jobposting_is_read_as_structured_roles():
    html = """<script type="application/ld+json">{"@context":"https://schema.org","@graph":[
      {"@type":"JobPosting","title":"Backend Engineer","url":"https://acme.in/jobs/1",
       "jobLocation":{"address":{"addressLocality":"Bengaluru","addressCountry":"IN"}}},
      {"@type":"Organization","name":"Acme"}]}</script>"""
    jobs = ca.jsonld_jobs(html, "https://acme.in/careers", "Acme")
    assert [(j.title, j.url, j.location) for j in jobs] == [("Backend Engineer", "https://acme.in/jobs/1", "Bengaluru, IN")]


def test_posting_links_and_job_cards_need_no_model():
    page = ("[Senior Backend Engineer Bengaluru · Full-time](https://acme.in/careers/senior-backend-engineer) "
            "[View all jobs](https://acme.in/careers/all) [Careers](https://acme.in/careers)")
    jobs = careers.roles_from_links(page, "https://acme.in/careers", "Acme")
    assert [(j.title, j.location) for j in jobs] == [("Senior Backend Engineer", "Bengaluru")]

    cards = "Featured Roles\n\nGrowth Lead\n\nGrowth\n• On Site\n• Bengaluru\n\nPerks & Benefits\n\nMedical Insurance\n"
    jobs = careers.roles_from_lines(cards, "https://acme.in/careers", "Acme")
    assert [(j.title, j.location) for j in jobs] == [("Growth Lead", "Bengaluru")]


def test_model_roles_are_kept_only_when_literally_on_the_page(monkeypatch):
    from jobhunter import llm

    text = "Open positions\n\n### Software Engineer I\nHyderabad\n\n### Data Analyst\nRemote"
    monkeypatch.setattr(llm, "chat_json", lambda *a, **k: {"roles": [
        {"title": "Software Engineer I", "location": "Hyderabad", "apply_url": "https://invented.example/apply"},
        {"title": "Staff ML Engineer", "location": "Bengaluru"},          # not on the page
    ]})
    jobs, err = careers.roles_from_text("Acme", "https://acme.in/careers", text)
    assert err is None
    assert [(j.title, j.location) for j in jobs] == [("Software Engineer I", "Hyderabad")]
    assert jobs[0].url.startswith("https://acme.in/careers#")             # the invented link is dropped


def test_fill_order_puts_the_list_first_then_scrape_first_then_priority():
    rows = [Company(id=1, name="High", priority_score=90), Company(id=2, name="Bare", scrape_first=True),
            Company(id=3, name="Listed 2", list_key="india-star", list_rank=2),
            Company(id=4, name="Listed 1", list_key="india-star", list_rank=1, priority_score=5)]
    assert [c.id for c in sorted(rows, key=fill_order)] == [4, 3, 2, 1]


def test_careers_pending_reads_list_rows_first_and_skips_recent_reads(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}")
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(careers, "get_session", lambda: Session(engine))
    monkeypatch.setattr(careers, "init_db", lambda: None)
    with Session(engine) as s:
        s.add(Company(id=1, name="Big", website="https://big.dev", priority_score=99))
        s.add(Company(id=2, name="Listed", website="https://listed.in", list_key="india-star", list_rank=1,
                      tier="reject"))
        s.add(Company(id=3, name="Done", website="https://done.dev", priority_score=50,
                      notes=f"[careers] read {careers.utcnow():%Y-%m-%d}: https://done.dev/careers — 2 role(s)"))
        s.add(Company(id=4, name="Famous", website="https://famous.com", overrated=True))
        s.commit()
    assert careers.pending(None) == [2, 1]
    assert careers.pending(None, list_key="india-star") == [2]
    assert 3 in careers.pending(None, force=True)


def test_valuation_and_headcount_need_a_sentence_that_names_the_company():
    from jobhunter import enrich

    v = enrich.valuation_from("Zepto raised $340 million at a valuation of $5 billion, it said.", "Zepto")
    assert v and v[0] == 5000
    assert enrich.valuation_from("Rival Co is valued at $2 billion.", "Zepto") is None
    v = enrich.valuation_from("Acme's post-money valuation stood at ₹1,000 crore.", "Acme")
    assert v and 100 < v[0] < 130            # 1,000 crore INR ≈ $114M
    assert enrich.valuation_from("Acme Funding, Valuation $5.5M. Acme has raised $5.5M over 3 rounds.", "Acme") is None
    assert enrich.headcount_from("Acme has a team of 120 people in Bengaluru.", "Acme")[0] == 120
    assert enrich.headcount_from("Acme: company size 51-200 employees.", "Acme") is None   # a band is not a number


def test_good_referrers_count_only_people_who_can_refer(tmp_path):
    from jobhunter.contacts import roles

    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(Company(id=1, name="Acme"))
        for rc in ("engineer", "founder", "other", None, "recruiter"):
            s.add(Contact(company_id=1, name=f"P {rc}", source="test", role_class=rc))
        s.commit()
        assert roles.good_referrer_counts(s, [1]) == {1: 3}
