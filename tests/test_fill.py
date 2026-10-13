"""The fill pass (2026-10-14): a description with no model — meta / og / JSON-LD / About /
LinkedIn, refusing cookie banners, headlines and industry labels; a combined line only with
both parts' sources; hiring proof from ANY role on their own page; the ★ → India → remote order."""

from __future__ import annotations

import json

from sqlmodel import Session, SQLModel, create_engine

from jobhunter import careers, fill, pipeline
from jobhunter.db import Company
from jobhunter.scrapers.base import RawJob

HOME = """<html><head>
<meta name="description" content="Acme builds payment rails for Indian merchants, from UPI to cards, with one API and same-day settlement.">
<meta property="og:description" content="We use cookies to improve your experience. Accept all cookies.">
<meta name="twitter:description" content='Acme raises $20 million in Series A funding led by Peak XV'>
<script type="application/ld+json">{"@context":"https://schema.org","@graph":[
  {"@type":"WebPage","description":"Home"},
  {"@type":"Organization","name":"Acme","description":"Acme is the payments infrastructure company that lets Indian businesses accept, move and reconcile money."}]}</script>
</head><body><nav><p>Products Pricing Docs Careers Blog Login and many other navigation links here</p></nav>
<p>Short.</p>
<p>Trusted by more than ten thousand merchants across India, Acme settles payouts the same day with full reconciliation.</p>
<a href="/about-us">About</a> <a href="https://www.linkedin.com/company/acme-pay/">in</a>
<a href="https://linkedin.com/company/acme-pay">LinkedIn</a>
<footer><p>© 2026 Acme. All rights reserved. Privacy policy and terms of service apply.</p></footer>
</body></html>"""


def test_meta_og_twitter_and_jsonld_are_read():
    meta = fill.meta_candidates(HOME, "https://acme.in")
    assert [c.kind for c in meta] == ["meta", "og", "twitter"]
    assert meta[0].text.startswith("Acme builds payment rails")
    ld = fill.jsonld_candidates(HOME, "https://acme.in")
    assert [c.text for c in ld] == ["Acme is the payments infrastructure company that lets Indian businesses "
                                    "accept, move and reconcile money."]
    paras = fill.paragraph_candidates(HOME, "https://acme.in")
    assert [p.text[:20] for p in paras] == ["Trusted by more than"]     # nav, footer, short lines out
    assert fill.about_url(HOME, "https://acme.in") == "https://acme.in/about-us"
    assert fill.linkedin_slug(HOME) == "acme-pay"


def test_best_description_refuses_cookie_headline_and_label_and_keeps_the_url():
    cands = fill.page_candidates(HOME, "https://acme.in")
    best, refused = fill.best_description(cands, name="Acme", domain="acme.in")
    assert best is not None and best.kind == "jsonld" and best.url == "https://acme.in"
    assert any("cookie" in r for r in refused) and any("funding headline" in r for r in refused)
    label = [fill.Cand("Acme is a Financial Services company.", "linkedin", "https://linkedin.com/company/acme")]
    best, refused = fill.best_description(label, name="Acme", domain="acme.in")
    assert best is None and "industry label" in refused[0]


def test_combined_description_needs_both_parts_and_names_both_sources():
    short = [fill.Cand("Payments for every Indian merchant.", "meta", "https://acme.in")]
    best, _ = fill.best_description(short, name="Acme", domain="acme.in",
                                    what="Payments and banking infrastructure", what_source="data/lists/india-star.yaml")
    assert best is not None and best.kind == "combined"
    assert best.text == "Acme — Payments and banking infrastructure. Payments for every Indian merchant."
    assert best.url == "https://acme.in + data/lists/india-star.yaml"
    # no list line → nothing combined, nothing invented
    best, _ = fill.best_description(short, name="Acme", domain="acme.in")
    assert best is None
    # a cookie banner is never half of a combined line
    cookie = [fill.Cand("We use cookies.", "meta", "https://acme.in")]
    assert fill.best_description(cookie, name="Acme", domain="acme.in", what="Payments")[0] is None


def test_linkedin_extra_and_jobs_page():
    page = ("# Digantara\n[See jobs](https://www.linkedin.com/jobs/digantara-jobs-worldwide?f_C=14578249&trk=top-card)"
            "\nView all 185 employees](x)\n\nFounded\n2020\n")
    info = fill.parse_linkedin_extra(page)
    assert info == {"founded": 2020, "company_id": "14578249", "members": 185,
                    "jobs_url": "https://www.linkedin.com/jobs/digantara-jobs-worldwide?f_C=14578249"}
    assert fill.parse_jobs_page("Title: Digantara Jobs at Digantara in Worldwide (25 Open Roles) | LinkedIn",
                                "Digantara") == 25
    assert fill.parse_jobs_page("Title: LinkedIn: Log In or Sign Up\n\nJoin now", "Digantara") is None


def _db(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}")
    SQLModel.metadata.create_all(engine)
    for mod in (careers, fill):
        monkeypatch.setattr(mod, "get_session", lambda: Session(engine))
        monkeypatch.setattr(mod, "init_db", lambda: None)
    return engine


def test_any_open_role_on_their_own_page_is_hiring_proof(monkeypatch, tmp_path):
    engine = _db(monkeypatch, tmp_path)
    with Session(engine) as s:
        s.add(Company(id=1, name="Acme", website="https://acme.in", domain="acme.in"))
        s.commit()
    f = careers.Find(careers_url="https://acme.in/careers", via="link")
    monkeypatch.setattr(careers, "discover", lambda *a, **k: f)
    roles = [RawJob(company_name="Acme", title="Sales Manager", url="https://acme.in/careers/sales", source="careers",
                    location="Mumbai"),
             RawJob(company_name="Acme", title="Accountant", url="https://acme.in/careers/acc", source="careers",
                    location="Delhi")]
    seen = {}

    def read(ff, name, *, use_model=True):
        seen["use_model"] = use_model
        return roles, "posting links", None
    monkeypatch.setattr(careers, "_read_roles", read)

    def persist(jobs, stats):
        stats.kept = 0                      # neither is engineering in BLR / HYD / remote
        return stats
    monkeypatch.setattr(pipeline, "persist", persist)
    out = careers.run_company(1, use_model=False)
    assert seen["use_model"] is False and out["found"] == 2
    with Session(engine) as s:
        c = s.get(Company, 1)
        assert c.hiring_status == "verified"
        assert json.loads(c.hiring_roles) == ["Sales Manager", "Accountant"]
        assert c.careers_url == "https://acme.in/careers"
        assert (c.open_roles_count, c.relevant_roles_count) == (2, 0)


def test_queue_is_star_list_then_india_then_remote(monkeypatch, tmp_path):
    engine = _db(monkeypatch, tmp_path)
    with Session(engine) as s:
        s.add(Company(id=1, name="Remote Co", hq_region="us", priority_score=99, tier="tier1"))
        s.add(Company(id=2, name="Blr Co", city="Bengaluru", priority_score=10))
        s.add(Company(id=3, name="Star Two", list_key="india-star", list_rank=2))
        s.add(Company(id=4, name="Star One", list_key="india-star", list_rank=1))
        s.add(Company(id=5, name="Rejected", hq_region="us", tier="reject", priority_score=100))
        s.add(Company(id=6, name="Hidden", list_key="india-star", status="merged"))
        s.commit()
    assert fill.queue() == [4, 3, 2, 1]
    assert fill.queue(only=1) == [2]


def test_labelled_job_titles_are_read_without_a_model():
    text = ("**Job Title: InfoSec Compliance Manager****Job Type****:**Full-time\n**Location****:**Hyderabad\n"
            "Position: Backend Engineer\nRole: we move fast\n")
    jobs = careers.roles_from_labels(text, "https://ozonetel.com/jobs/", "Ozonetel")
    assert [j.title for j in jobs] == ["InfoSec Compliance Manager", "Backend Engineer"]


def test_darwinbox_feed_is_read_as_roles():
    import asyncio

    from jobhunter.scrapers import careers_ats as ca

    class R:
        status_code = 200

        def __init__(self, data):
            self._d = data

        def json(self):
            return self._d

    class Http:
        async def get(self, url, **kw):
            jobs = [{"id": "a1", "title": "Hospital Relationship Manager",
                     "tool_tip_locations": "['Bengaluru, Karnataka, India ']"}] if "page=1" in url else []
            return R({"status": "success", "message": {"jobscount": 1, "jobs": jobs}})

    st, jobs = asyncio.run(ca._darwinbox(Http(), "redhealth", "Red.Health"))
    assert st == ca.HIT and [(j.title, j.location) for j in jobs] == [
        ("Hospital Relationship Manager", "Bengaluru, Karnataka, India")]
    assert jobs[0].url.endswith("/jobDetails/a1")
