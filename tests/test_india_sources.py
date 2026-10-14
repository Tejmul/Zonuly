"""The extra India discovery sources (jobhunter/india_sources.py): pure parsers and gates.

No network, no database — every page here is an inline fixture.
"""

from __future__ import annotations

import json

from jobhunter import india_sources as m


GH_ORG = """
<meta name="description" content="Open source Observability Platform. SigNoz has 158 repositories available. Follow their code on GitHub.">
<h1 class="h2 lh-condensed">
  SigNoz
</h1>
<span itemprop="location">SF/Bengaluru</span>
<a itemprop="url" class="Link--primary" title="https://signoz.io/" href="https://signoz.io/">https://signoz.io/</a>
"""


def test_github_org_page_parses_name_website_location_description():
    info = m.parse_gh_org(GH_ORG)
    assert info == {"name": "SigNoz", "website": "https://signoz.io/", "location": "SF/Bengaluru",
                    "description": "Open source Observability Platform."}
    assert m.gh_junk("SigNoz", info) is None


def test_github_orgs_that_are_not_companies_are_refused():
    assert m.gh_junk("x", {"name": "X"}) == "no website on its GitHub profile"
    assert "community" in m.gh_junk("gdg-bangalore", {"name": "GDG Bangalore", "website": "https://gdgblr.dev"})
    assert "university" in m.gh_junk("rvce", {"name": "RV College of Engineering", "website": "https://rvce.edu.in"}) \
        or m.gh_junk("rvce", {"name": "RV College of Engineering", "website": "https://rvce.edu.in"}) == "website is not a company site"
    assert m.gh_junk("acme", {"name": "Acme", "website": "https://acme.github.io"}) == "website is not a company site"
    # a hobby org pointing at somebody's personal site
    assert m.gh_junk("novalabs-in", {"name": "NovaLabs.in", "website": "https://www.saitejabandaru.com"}) \
        .startswith("website does not carry")
    assert "agency" in m.gh_junk("devshop", {"name": "DevShop", "website": "https://devshop.in",
                                             "description": "Custom software development company for startups"})


def _cutshort_page(posts):
    data = {"props": {"pageProps": {"dehydratedState": {"queries": [{"state": {"data": {"data": {"jobs": posts}}}}]}}}}
    return f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script>'


def _post(name, *, type_="Product", size="20-100", client=False, locs=("Bengaluru (Bangalore)",), founded=2021):
    return {"headline": "Backend Engineer", "locations": list(locs), "sanitizedComment": "<p>Build our API</p>",
            "companyDetails": {"_id": "1", "name": name, "type": type_, "size": size, "stage": "Raised funding",
                               "founded": founded, "alias": name.lower(), "hiringForClient": client,
                               "links": {"website": f"https://{name.lower()}.com"}}}


def test_cutshort_posts_parse_and_gate():
    rows = m.parse_cutshort(_cutshort_page([
        _post("AbleCredit"), _post("VY Systems", type_="Services"), _post("Planview", size="1000-5000"),
        _post("MNC", client=True), _post("Old Co", founded=1999), _post("Potentiam Offshore Solutions")]))
    assert [r["name"] for r in rows] == ["AbleCredit", "VY Systems", "Planview", "MNC", "Old Co",
                                         "Potentiam Offshore Solutions"]
    assert rows[0]["website"] == "https://ablecredit.com" and rows[0]["locations"] == ["Bengaluru (Bangalore)"]
    gates = [m.cs_gate(r) for r in rows]
    assert gates[0] is None
    assert "Services" in gates[1]
    assert gates[2].startswith("large enterprise")
    assert gates[3] == "posts on behalf of a client"
    assert gates[4].startswith("founded before")
    assert gates[5] == "agency / IT services / staffing"


def test_portfolio_links_and_pages():
    links = m.portfolio_items(
        {"key": "x", "url": "https://vc.example/portfolio", "mode": "links"},
        '<a href="https://acme.ai/">Acme</a><a href="https://www.linkedin.com/company/acme">in</a>'
        '<a href="/about">About</a><a href="https://acme.ai/team">Acme team</a><a href="https://beta.in">Beta</a>')
    assert [i["website"] for i in links] == ["https://acme.ai", "https://beta.in"]
    pages = m.portfolio_items(
        {"key": "y", "url": "https://www.kalaari.com/portfolio/", "mode": "pages", "prefix": "/portfolio/"},
        '<a href="/portfolio/agnext">AgNext</a><a href="/portfolio/agnext">again</a><a href="/team/x">t</a>')
    assert pages == [{"src": "y", "name": "Agnext", "page": "https://www.kalaari.com/portfolio/agnext"}]
    page = ('<a href="https://agnext.com/">site</a><a href="https://yourstory.com/2021/agnext">news</a>'
            '<a href="https://www.peppersquare.com/">design</a>')
    assert m.website_from_page(page, "https://www.kalaari.com/portfolio/agnext", "Agnext") == "https://agnext.com"


def test_own_site_city_needs_bengaluru_or_hyderabad_and_reads_addresses():
    text = m.page_text("<footer><p>Our office</p><p>Bengaluru</p><p>HSR Layout, 560102</p></footer>")
    assert "Bengaluru" in m.city_from_text(text, name="Acme")
    assert m.city_from_text(m.page_text("<p>We serve clients in Pune and Mumbai.</p>"), name="Acme") is None
    # Delhi named beside Bengaluru, nobody says where they are based: not evidence
    assert m.city_from_text("Offices: Delhi, Bengaluru", name="Acme") is None
    jsonld = '<script type="application/ld+json">{"address":{"streetAddress":"18th Main Rd, Indiranagar, ' \
             'Bengaluru, Karnataka 560008","addressLocality":"Bengaluru"}}</script>'
    assert "Indiranagar" in m.city_from_markup(jsonld, name="Acme")
    assert m.city_from_markup('<script>var city="Bengaluru";</script>', name="Acme") is None


def test_site_name_prefers_a_name_the_domain_carries():
    assert m.site_name({"name": None, "title": "Home | Acme Robotics — robots for farms"}, "https://acmerobotics.in") \
        == "Acme Robotics"
    assert m.site_name({"name": "Fundfina"}, "https://www.fundfina.com/") == "Fundfina"
    assert m.site_name({"title": "Welcome"}, "https://zz.io") is None


WF = """[![Image 4: Fibr AI company logo](https://img/x.jpg)](https://wellfound.com/company/getfibr)

AI-Agents Workforce for Conversion and Revenue Optimization 11-50 Employees

₹15L – ₹25L • 0.05% – 0.1%

In office • Bengaluru

2 years of exp

[![Image 5: TestAutomate company logo](https://img/y.jpg)](https://wellfound.com/company/testautomate)

AI-native testing tool 1-10 Employees

Remote only • Everywhere
"""


def test_wellfound_cards():
    cards = m.parse_wellfound(WF)
    assert [c["name"] for c in cards] == ["Fibr AI", "TestAutomate"]
    assert cards[0]["where"] == ["In office • Bengaluru"] and cards[0]["headcount"] == 11
    assert cards[0]["pay"] == ["₹15L – ₹25L • 0.05% – 0.1%"]
    assert cards[1]["where"] == []
    assert "fibr.ai" in m.guess_sites("Fibr AI", "getfibr") or "fibrai.com" in m.guess_sites("Fibr AI", "getfibr")
    assert m.guess_sites("Acme", "acme-ai")[0] == "acme.ai"
