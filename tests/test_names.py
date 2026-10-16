"""contacts/names.py: a greeting name from the address or the GitHub profile — never invented."""

from __future__ import annotations

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from jobhunter import guardrails as g
from jobhunter.contacts import names
from jobhunter.db import Company, Contact, ExtractionReject, ExtractionWrite
from jobhunter.outreach import compose

SARVAM = Company(id=54, name="Sarvam AI", domain="sarvam.ai", email_pattern="{first}")
AAI = Company(name="AssemblyAI", domain="assemblyai.com", email_pattern="{f}{last}")
ACME = Company(name="Acme", domain="acme.com")


@pytest.mark.parametrize("addr,login,company,want,source", [
    ("kailash.bysani@affirm.com", "kbysani-affirm", ACME, "Kailash Bysani", "address"),
    ("johnathon.ayazian@benchling.com", None, ACME, "Johnathon Ayazian", "address"),
    ("damola_shomoye@acme.com", None, ACME, "Damola Shomoye", "address"),
    ("ashraf.chowdury09@gmail.com", "ashrafchowdury", ACME, "Ashraf Chowdury", "address"),
    ("adityachaudhary@sarvam.ai", "adityachaudhary-sarvam", SARVAM, "Aditya Chaudhary", "address"),
    ("ankush@sarvam.ai", "ankush566", SARVAM, "Ankush", "address-first"),
    ("shivesh@sarvam.ai", "shivesh-sarvam", SARVAM, "Shivesh", "address-first"),
    ("pushkar@sarvam.ai", "pushkar-sarvam", SARVAM, "Pushkar", "address-first"),
    ("zakki@assemblyai.com", "zakki-aai", AAI, "Zakki", "address-first"),
    ("shabnam.m@acme.com", None, ACME, "Shabnam", "address-first"),
    ("raviteja@acme.com", "ravi-teja", ACME, "Ravi Teja", "address"),
])
def test_names_the_address_says(addr, login, company, want, source):
    d = names.from_address(addr, login=login, company=company, pattern=company.email_pattern)
    assert d and d.name == want and d.source == source


@pytest.mark.parametrize("addr,login,company", [
    ("npatel@assemblyai.com", "npatel-aai", AAI),          # initial + surname: no "Hi N"
    ("kbysani@affirm.com", None, ACME),
    ("iroure@mux.com", "ignasio-mux", AAI),                # {f}{last} company, nothing confirms a first name
    ("caner@acme.com", None, ACME),                        # not in the list, no login, no {first} format
    ("dmt.nag@acme.com", None, ACME),
    ("first@acme.com", None, ACME), ("jane.d@acme.com", None, ACME), ("john@acme.com", None, ACME),
    ("support@acme.com", None, ACME), ("accommodations@acme.com", None, ACME),
    ("u003csupport@acme.com", None, ACME), ("acme@acme.com", None, ACME),
    ("ct-shweta.vyas@acme.com", None, ACME), ("vibhupandey28@gmail.com", "therealpandey", ACME),
    ("sales.blr@acme.com", None, ACME), ("hr.india@acme.com", None, ACME),
    ("lawschools@sarvam.ai", None, SARVAM), ("opensource@sarvam.ai", None, SARVAM),
    ("itadmin@sarvam.ai", None, SARVAM), ("janed@sarvam.ai", None, SARVAM),
    ("meerraja17@gmail.com", "Redskull-127", SARVAM),
    ("frenzy.madness@gmail.com", None, ACME), ("me.ranjitale@gmail.com", None, ACME),      # {first} is the company's format, not gmail's
])
def test_no_name_when_the_address_does_not_say_one(addr, login, company):
    assert names.from_address(addr, login=login, company=company, pattern=company.email_pattern) is None


def test_a_first_name_only_is_a_person_for_the_drafter_and_the_guardrails():
    assert g.first_name_only("Ankush") and not g.first_name_only("Support") and not g.first_name_only("Np")
    assert not g.first_name_only("ankush") and not g.first_name_only("Ankush Kumar")
    assert compose.first_name("Ankush") == "Ankush"
    p = Contact(company_id=54, name="Ankush", name_source="address-first", email="ankush@sarvam.ai",
                github="https://github.com/ankush566", source="github", affiliation="verified-current",
                affiliation_source="company-address", email_kind="company")
    assert g.is_first_only(p) and g.is_ready(p)
    plan = g.relabel_contact(p, SARVAM)
    assert "name" not in plan and "_name_reject" not in plan      # not wiped as a handle


@pytest.mark.parametrize("addr", ["u003csupport@benchling.com", "accommodations@benchling.com",
                                  "speechsupport@gnani.ai", "demo@acme.com", "your-account-manager@acme.com",
                                  "partnerwithus@acme.com", "gethelp@acme.com", "upload@acme.com"])
def test_inboxes_and_garbage_are_never_people(addr):
    chk = g.email(addr, domain="benchling.com")
    assert not chk or chk.value[1] == "generic"


def test_an_escaped_prefix_is_repaired_not_trusted():
    chk = g.email("u003ejenn.priske@redspace.com", domain="redspace.com")
    assert chk and chk.value == ("jenn.priske@redspace.com", "company") and "repaired-escape" in chk.flags
    assert not g.email("u003e@tailscale.com")
    p = Contact(company_id=1, name="Jenn Priske", email="u003ejenn.priske@redspace.com", source="site",
                affiliation="likely-current", email_kind="company")
    assert not g.is_ready(p)                     # not until the stored address is the real one


@pytest.fixture()
def eng(tmp_path):
    e = create_engine(f"sqlite:///{tmp_path / 'n.db'}")
    SQLModel.metadata.create_all(e)
    with Session(e) as s:
        s.add(Company(id=54, name="Sarvam AI", domain="sarvam.ai", email_pattern="{first}", city="Bengaluru",
                      list_key="india-star"))
        s.add(Company(id=58, name="Benchling", domain="benchling.com"))
        s.commit()
    return e


def _c(**kw) -> Contact:
    base = dict(source="github", affiliation="verified-current", affiliation_source="company-address",
                email_kind="company")
    base.update(kw)
    return Contact(**base)


def test_backfill_names_demotes_and_repairs(eng):
    with Session(eng) as s:
        s.add_all([
            _c(company_id=54, email="ankush@sarvam.ai", handle="ankush566", github="https://github.com/ankush566"),
            _c(company_id=54, email="adityachaudhary@sarvam.ai", handle="adityachaudhary-sarvam"),
            _c(company_id=54, email="kept@sarvam.ai", name="Kept Name", name_source="linkedin"),
            _c(company_id=58, email="u003csupport@benchling.com", source="site", affiliation="likely-current"),
            _c(company_id=58, email="accommodations@benchling.com", source="site", affiliation="likely-current"),
            _c(company_id=58, email="u003ejenn.priske@benchling.com", source="site", affiliation="generic",
               email_kind="generic", research_notes="address published at https://benchling.com/team"),
        ])
        s.commit()
    out = names.backfill(sessions=lambda: Session(eng))
    assert out["address"] == 1 and out["address_first"] == 1 and out["junk"] == 2 and out["repaired"] == 2
    with Session(eng) as s:
        by = {p.email: p for p in s.exec(select(Contact)).all()}
        assert (by["ankush@sarvam.ai"].name, by["ankush@sarvam.ai"].name_source) == ("Ankush", "address-first")
        assert by["adityachaudhary@sarvam.ai"].name == "Aditya Chaudhary"
        assert by["kept@sarvam.ai"].name == "Kept Name"                    # never overwritten
        assert by["support@benchling.com"].affiliation == "generic"
        assert by["accommodations@benchling.com"].affiliation == "generic"
        jenn = by["jenn.priske@benchling.com"]
        assert jenn.name == "Jenn Priske" and jenn.affiliation == "likely-current" and jenn.email_kind == "company"
        fields = {w.field for w in s.exec(select(ExtractionWrite)).all()}
        assert {"contact.name", "contact.first_name", "contact.email"} <= fields
    with Session(eng) as s:
        s.add(_c(company_id=54, email="lawschools@sarvam.ai", name="Lawschools", name_source="address-first"))
        s.commit()
    assert names.backfill(sessions=lambda: Session(eng))["cleared"] == 1     # a tightened rule takes it back
    again = names.backfill(sessions=lambda: Session(eng))
    assert again["address"] == again["address_first"] == again["junk"] == again["repaired"] == again["cleared"] == 0


class _Resp:
    def __init__(self, status, text=""):
        self.status_code, self.text = status, text


class _Client:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def get(self, url, timeout=None):
        self.calls.append(url)
        return self.pages.get(url.rsplit("/", 1)[-1], _Resp(404))


def _page(name):
    return _Resp(200, f'<span class="p-name vcard-fullname" itemprop="name">\n  {name}\n</span>')


def test_github_pass_names_budgets_and_backs_off(eng):
    with Session(eng) as s:
        s.add_all([
            _c(company_id=58, email="npatel@benchling.com", handle="npatel-b", github="https://github.com/npatel-b"),
            _c(company_id=54, email="x@sarvam.ai", handle="adityac", github="https://github.com/adityac"),
            _c(company_id=54, email="pushkar@sarvam.ai", name="Pushkar", name_source="address-first",
               handle="pushkar-sarvam", github="https://github.com/pushkar-sarvam"),
            _c(company_id=54, email="h@sarvam.ai", handle="nohandle", github="https://github.com/nohandle"),
            _c(company_id=54, email="s@sarvam.ai", handle="shivesh-sarvam", github="https://github.com/shivesh-sarvam"),
            _c(company_id=54, email="p@sarvam.ai", handle="pandey-x", github="https://github.com/pandey-x"),
        ])
        s.commit()
    client = _Client({"adityac": _page("Aditya Chaudhary"), "pushkar-sarvam": _page("Pushkar Singh"),
                      "nohandle": _page("nohandle"), "npatel-b": _Resp(429), "shivesh-sarvam": _page("Shivesh"),
                      "pandey-x": _page("Pandey")})
    out = names.github_pass(10, client=client, pause_s=0, sessions=lambda: Session(eng))
    # Sarvam first; the 429 (Benchling, last) stops the pass and cools it
    assert out["named"] == 1 and out["upgraded"] == 1 and out["first_only"] == 1 and out["no_name"] == 2
    assert out["stopped"] == "GitHub HTTP 429"
    assert client.calls[-1].endswith("npatel-b") and len(client.calls) == 6
    with Session(eng) as s:
        by = {p.handle: p for p in s.exec(select(Contact)).all()}
        assert (by["adityac"].name, by["adityac"].name_source) == ("Aditya Chaudhary", "github-profile")
        assert by["pushkar-sarvam"].name == "Pushkar Singh"
        assert by["nohandle"].name is None and by["npatel-b"].name is None
        assert (by["shivesh-sarvam"].name, by["shivesh-sarvam"].name_source) == ("Shivesh", "github-first")
        assert g.is_first_only(by["shivesh-sarvam"]) and by["pandey-x"].name is None   # a surname is not a greeting
        assert s.exec(select(ExtractionReject).where(ExtractionReject.channel == "github-name")).first()
    again = names.github_pass(10, client=client, pause_s=0, sessions=lambda: Session(eng))
    assert again["requests"] == 0 and "cooling" in again["stopped"]


def test_fill_vetted_names_a_new_contact_on_the_way_in():
    gd = {"name": None, "email": "ankush@sarvam.ai", "email_kind": "company", "handle": "ankush566"}
    assert names.fill_vetted(gd, SARVAM)["name"] == "Ankush" and gd["name_source"] == "address-first"
    named = {"name": "Real Name", "email": "ankush@sarvam.ai", "email_kind": "company"}
    assert names.fill_vetted(named, SARVAM)["name"] == "Real Name"
    assert names.fill_vetted(None, SARVAM) is None
