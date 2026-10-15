"""contacts/team_people.py — YC founders and team pages, no web search, no login — and the
referral job's 2026-10-16 queue fixes (address-only companies no longer hold the head)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from jobhunter import referrals
from jobhunter.contacts import team_people as tp
from jobhunter.db import Company, Contact, utcnow


def test_name_title_pairs_from_a_team_page():
    lines = ["# Meet the team", "Ajit Kumar", "Founder & CEO", "Praveen Yadav", "Founder & CTO",
             "Harkirat Singh", "Founding Engineer", "Read more", "Product", "Cookie Settings",
             "Product Terms of Use", "Solutions Leader", "Solutions Consultant"]
    got = tp.people_from_lines(lines, "Lytmus AI", "lytmus.ai")
    assert [(p["name"], p["title"]) for p in got] == [
        ("Ajit Kumar", "Founder & CEO"), ("Praveen Yadav", "Founder & CTO"), ("Harkirat Singh", "Founding Engineer")]


def test_advisors_investors_board_and_testimonials_are_not_staff():
    lines = ["# Our advisors", "Rahul Mehta", "Former CTO, Flipkart", "# Investors", "Anita Rao", "Partner",
             "# Leadership", "Priya Nair", "Head of Engineering", "Hong Ping Yeo", "Non-executive Director",
             "Peter Cohan", "Author and Founder,", "Sunil Das", "VP Sales at Salesforce",
             "Mr. Arjun Menon", "Chief Technology Officer"]
    got = {p["name"]: p["title"] for p in tp.people_from_lines(lines, "Acme", "acme.in")}
    assert got == {"Priya Nair": "Head of Engineering", "Arjun Menon": "Chief Technology Officer"}


def test_title_ok_allows_this_company_and_refuses_another():
    assert tp.title_ok("CEO & Co-Founder, RevRag AI", "Revrag", "revrag.ai")
    assert not tp.title_ok("CTO at Google", "Revrag", "revrag.ai")
    assert not tp.title_ok("hr@treebohotels.com", "Treebo", "treebo.com")
    assert not tp.title_ok("We are a team of engineers building the future", "Acme", None)


def test_jsonld_person():
    raw = ('<script type="application/ld+json">{"@graph":[{"@type":"Person","name":"Neha Gupta",'
           '"jobTitle":"Staff Engineer"},{"@type":"Person","name":"Ravi K","jobTitle":"Investor"}]}</script>')
    assert tp.people_from_jsonld(raw, "Acme", "acme.in") == [{"name": "Neha Gupta", "title": "Staff Engineer"}]


def test_team_links_same_site_only_best_first():
    home = ('<a href="/about-us">About</a><a href="/careers">Careers</a><a href="https://other.com/team">x</a>'
            '<a href="/company/leadership">Leadership</a><a href="/blog/team-offsite">Blog</a>')
    assert tp.team_links(home, "https://acme.in") == ["https://acme.in/company/leadership", "https://acme.in/about-us"]


def test_yc_founders_active_and_latest_company_only():
    co = {"ycdc_status": "Active", "founders": [
        {"full_name": "Dev Mandal", "title": "Founder/CEO", "is_active": True,
         "linkedin_url": "https://www.linkedin.com/in/dev?x=1", "latest_yc_company": {"href": "https://www.ycombinator.com/companies/markov"}},
        {"full_name": "Gone Person", "title": "Founder", "is_active": False, "latest_yc_company": {}},
        {"full_name": "Moved On", "title": "CTO", "is_active": True,
         "latest_yc_company": {"href": "https://www.ycombinator.com/companies/newco"}}]}
    assert tp.founders_from_yc(co, "markov") == [
        {"name": "Dev Mandal", "title": "Founder/CEO", "linkedin": "https://www.linkedin.com/in/dev"}]
    assert tp.founders_from_yc({**co, "ycdc_status": "Inactive"}, "markov") == []


@pytest.fixture()
def engine(tmp_path, monkeypatch):
    eng = create_engine(f"sqlite:///{tmp_path / 'j.db'}")
    SQLModel.metadata.create_all(eng)
    for mod in (referrals, tp):
        monkeypatch.setattr(mod, "get_session", lambda: Session(eng))
    monkeypatch.setattr(referrals, "init_db", lambda: None)
    monkeypatch.setattr(referrals, "_log_run", lambda *a, **k: None)
    return eng


def test_save_labels_team_page_verified_and_yc_likely_and_confirms_unconfirmed(engine):
    from jobhunter import guardrails

    with Session(engine) as s:
        c = Company(name="Lytmus AI", website="https://lytmus.ai", domain="lytmus.ai", city="Bengaluru",
                    hq_region="india")
        s.add(c)
        s.commit()
        cid = c.id
        s.add(Contact(company_id=cid, name="Bharath Raj", source="github", affiliation="unconfirmed",
                      affiliation_source="none", affiliation_evidence="no evidence they work there now"))
        s.commit()
    r = tp._save(cid, [{"name": "Ajit Kumar", "title": "Founder & CEO", "url": "https://lytmus.ai/team"},
                       {"name": "Bharath Raj", "title": "Founding AI Engineer", "url": "https://lytmus.ai/team"}],
                 kind="team")
    assert r == {"added": 1, "confirmed": 1}
    r = tp._save(cid, [{"name": "Praveen Yadav", "title": "Founder/CTO", "linkedin": None,
                        "url": "https://www.ycombinator.com/companies/lytmus"}], kind="yc")
    assert r["added"] == 1
    with Session(engine) as s:
        rows = {p.name: p for p in s.exec(select(Contact)).all()}
    assert rows["Ajit Kumar"].affiliation == "verified-current" and rows["Ajit Kumar"].affiliation_source == "team-page"
    assert rows["Bharath Raj"].affiliation == "verified-current"
    assert rows["Praveen Yadav"].affiliation == "likely-current" and rows["Praveen Yadav"].source == "yc-page"
    assert all(guardrails.is_ready(p) for p in rows.values())
    assert referrals.lead_channel("yc-page", "public-mention") == "team"


def test_queue_puts_companies_short_of_people_before_address_only(engine):
    with Session(engine) as s:
        full = Company(name="Full Co", city="Bengaluru", hq_region="india", priority_score=99)
        empty = Company(name="Empty Co", city="Bengaluru", hq_region="india", priority_score=1)
        part = Company(name="Part Co", city="Bengaluru", hq_region="india", priority_score=50,
                       notes=f"[refer-partial] {utcnow() - timedelta(hours=2):%Y-%m-%dT%H:%M}: x")
        for c in (full, empty, part):
            s.add(c)
        s.commit()
        for i in range(3):
            s.add(Contact(company_id=full.id, name=f"Priya Sharma{'abc'[i]}", source="linkedin-public",
                          affiliation="likely-current", affiliation_source="linkedin-headline",
                          linkedin=f"https://www.linkedin.com/in/p{i}"))
        s.commit()
        ids = (full.id, empty.id, part.id)
    q = referrals.cycle_queue()
    assert q == [ids[1], ids[0]]           # nobody-yet first; resting partial pass skipped


def test_exa_worth_it():
    assert referrals.exa_worth_it("Rupeek")
    assert not referrals.exa_worth_it("Blume Ventures")
    assert not referrals.exa_worth_it("MetaShot a gaming startup in Bangalore")
    assert not referrals.exa_worth_it("Jupiter: 1-app For Everything Money")


def test_deadline_ends_a_hung_company():
    import time

    with pytest.raises(referrals.CompanyTimeout):
        referrals.with_deadline(lambda: time.sleep(5), 1)
    assert referrals.with_deadline(lambda: 7, 1) == 7


def test_exa_card_format_current_under_company_heading_and_ex_headlines():
    from jobhunter.contacts.linkedin_public import people_from_results

    card = ("# Piyush Chauhan\n\nDirector of Engineering @ Rupeek | Fintech\n\n## Experience\n\n"
            "### [Rupeek](https://www.linkedin.com/company/rupeek)\n\n#### Director of Engineering (Current)\n\nJan 2023")
    old = ("# Sangharsh Boudhh\n\nPrincipal Engineer | Ex- Google, Rupeek, Founder, Ola | IITB\n\n## Experience\n\n"
           "### [Lightbeam](https://www.linkedin.com/company/lightbeamai)\n\n#### Principal Engineer\n\n"
           "### [Rupeek](https://www.linkedin.com/company/rupeek)\n\n#### SDE\n")
    moved = ("# Paras Nagpal\n\nSDE-2 @Flipkart | Ex - Myntra , Rupeek\n\n"
             "### [Flipkart](https://www.linkedin.com/company/flipkart)\n\n#### SDE 2 (Current)\n")
    rows = [{"url": "https://linkedin.com/in/piyush", "title": "Piyush Chauhan", "snippet": card},
            {"url": "https://linkedin.com/in/sb", "title": "Sangharsh Boudhh", "snippet": old},
            {"url": "https://linkedin.com/in/pn", "title": "Paras Nagpal", "snippet": moved}]
    got = people_from_results(rows, "Rupeek", "rupeek.com")
    assert [(p["name"], p["role"]) for p in got] == [("Piyush Chauhan", "Director of Engineering")]


def test_deadline_is_not_swallowed_by_a_channels_broad_except():
    import time

    def channel():
        try:
            time.sleep(5)
        except Exception:  # noqa: BLE001 — what every channel in refer_company does
            return "swallowed"
        return "finished"

    with pytest.raises(referrals.CompanyTimeout):
        referrals.with_deadline(channel, 1)
