"""actors/: the LinkedIn parsers and the people merge — pure, no network."""

from jobhunter.actors import linkedin as L
from jobhunter.actors.people import Book, _matches

PROFILE = """<title>Asha Rao - Backend Engineer at Acme | LinkedIn</title>
<script type="application/ld+json">{"@context":"http://schema.org","@graph":[{"@type":"Person","name":"Asha Rao",
"address":{"@type":"PostalAddress","addressLocality":"Bengaluru, Karnataka, India"},
"worksFor":[{"@type":"Organization","name":"Acme","url":"https://www.linkedin.com/company/acme-ai","member":{"@type":"OrganizationRole","startDate":2024}},
            {"@type":"Organization","name":"OldCo","url":"https://www.linkedin.com/company/oldco","member":{"@type":"OrganizationRole","startDate":2021,"endDate":2024}}],
"alumniOf":[{"@type":"EducationalOrganization","name":"Newton School","url":"https://www.linkedin.com/school/newton/","member":{"@type":"OrganizationRole","startDate":2020,"endDate":2021}}]}]}</script>"""

CARDS = """<ul><li><div class="base-card" data-entity-urn="urn:li:jobPosting:42">
<a class="base-card__full-link absolute" href="https://in.linkedin.com/jobs/view/sde-at-acme-42?trk=x"></a>
<h3 class="base-search-card__title">  SDE I </h3>
<h4 class="base-search-card__subtitle"><a class="hidden-nested-link" href="https://in.linkedin.com/company/acme-ai?trk=y">Acme</a></h4>
<span class="job-search-card__location">Bengaluru</span><time class="job-search-card__listdate" datetime="2026-10-16">2 days ago</time>
</div></li></ul>"""


def test_profile_current_vs_former_and_school():
    p = L.parse_profile(PROFILE, "https://www.linkedin.com/in/asha-rao")
    assert p["name"] == "Asha Rao" and p["headline"] == "Backend Engineer at Acme"
    assert [j["company"] for j in p["current"]] == ["Acme"]
    assert [j["company"] for j in p["past"]] == ["OldCo"]
    assert p["education"][0]["school"] == "Newton School"


def test_works_at_needs_the_company_page_when_known():
    p = L.parse_profile(PROFILE, "https://www.linkedin.com/in/asha-rao")
    assert L._works_at(p, "Acme", "acme-ai", "acme.ai")["matched_by"] == "company_url"
    # a namesake company page: same name, different LinkedIn page → not a match
    assert L._works_at(p, "Acme", "acme-corp", "acme.com") is None
    # the former employer is never "works there now"
    assert L._works_at(p, "OldCo", "oldco", None) is None


def test_job_cards():
    cards = L._parse_cards(CARDS)
    assert cards == [{
        "job_id": "42", "title": "SDE I", "company": "Acme", "company_url": "https://in.linkedin.com/company/acme-ai",
        "location": "Bengaluru", "posted_on": "2026-10-16", "badge": None,
        "url": "https://in.linkedin.com/jobs/view/sde-at-acme-42",
    }]


def test_book_merges_name_into_profile_and_keeps_strongest():
    b = Book()
    b.add(name="Asha Rao", source="team", title="Backend Engineer", strength="verified-current")
    b.add(name="Asha Rao", source="search", linkedin_url="https://in.linkedin.com/in/asha-rao?x=1", strength="unverified")
    assert len(b.by_key) == 1
    rec = next(iter(b.by_key.values()))
    assert rec["status"] == "verified-current"
    assert rec["linkedin_url"] == "https://www.linkedin.com/in/asha-rao"
    assert rec["sources"] == ["team", "search"]


def test_filters():
    p = {"title": "Senior Recruiter", "headline": None, "location": "Hyderabad"}
    assert _matches(p, ["recruit"], None, None)
    assert not _matches(p, ["engineer"], None, None)
    assert not _matches(p, None, ["recruit"], None)
    assert not _matches(p, None, None, ["Bengaluru"])
