"""Warm paths, similar companies and the graph referrer signal (kg/warm.py, 2026-10-16).

The rules under test: a warm path is only ever read from the person's own text and keeps
the quote; a lookalike name ("Drivewealth") is not a shared employer; bots and drive-by
committers are not "the same person"; sync writes nothing when nothing changed and drops
an edge whose evidence went away; similarity is shared investors (rare ones count more)
and shared skills — never city alone; payloads carry no email addresses."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select

from jobhunter import api
from jobhunter.db import Company, Contact, Email, Job
from jobhunter.kg import sync as kgsync
from jobhunter.kg import warm
from jobhunter.kg.store import Graph

PROFILE = {
    "name": "Test Candidate",
    "education": [{"institution": "Newton School of Technology, Rishihood University", "years": "2023-2027"}],
    "experience": [{"company": "eWealth Tech LLC", "title": "AI Lead Intern"},
                   {"company": "MVRX Labs (YC-P26)", "title": "AI Product Engineer Intern"}],
    "skills": {"ai": ["LangChain", "RAG", "FAISS", "Python"], "infra": ["Docker", "Qdrant"]},
    "_resume_text": "Test Candidate\nHyderabad, Telangana\nNewton School of Technology",
}


@pytest.fixture()
def world(tmp_path, monkeypatch):
    path = tmp_path / "w.db"
    engine = create_engine(f"sqlite:///{path}")
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(warm, "_session", lambda: Session(engine))
    monkeypatch.setattr(warm, "_profile", lambda: PROFILE)
    monkeypatch.setattr(warm, "Graph", lambda *a, **k: Graph(path))
    warm._CACHE.clear()
    with Session(engine) as s:
        cos = {
            "target": Company(name="Inferly", domain="inferly.ai", city="Hyderabad", funding_stage="Seed", tier="tier1"),
            "engaged": Company(name="Vectorly", domain="vectorly.ai", city="Bengaluru", funding_stage="Seed"),
            "twin": Company(name="Embedly", domain="embedly.ai", city="Hyderabad", funding_stage="Seed", tier="tier1"),
            "far": Company(name="Farco", domain="farco.com", city="Bengaluru"),
            "hyped": Company(name="Hypeco", overrated=True),
            "oldjob": Company(name="Stripe", domain="stripe.com"),
        }
        for c in cos.values():
            s.add(c)
        s.commit()
        ids = {k: c.id for k, c in cos.items()}
        people = [
            # strongest: their own headline names the school
            Contact(company_id=ids["target"], name="Asha Rao", email="asha@inferly.ai", email_kind="company",
                    source="linkedin-public", affiliation="verified-current",
                    role="ML Engineer @ Inferly | Newton School of Technology '24",
                    linkedin="https://www.linkedin.com/in/asha-rao/"),
            # stack overlap only
            Contact(company_id=ids["target"], name="Ben Ode", email="ben@inferly.ai", email_kind="company",
                    source="github", affiliation="verified-current", role="Building RAG pipelines with LangChain"),
            # a lookalike name — Drivewealth is not eWealth
            Contact(company_id=ids["target"], name="Cy Drive", source="github", affiliation="unconfirmed",
                    role="Engineer", employer_claim="@Drivewealth"),
            # the same person (one LinkedIn) at the engaged company
            Contact(company_id=ids["engaged"], name="Asha Rao", email="asha@vectorly.ai", email_kind="company",
                    source="linkedin-public", affiliation="verified-current",
                    linkedin="https://linkedin.com/in/asha-rao"),
            # an alumna of the target, now elsewhere
            Contact(company_id=ids["far"], name="Dee Former", source="github", role="SDE @farco | ex-Inferly"),
            # a bot at three companies — never "the same person"
            *[Contact(company_id=ids[k], name="actions-user", handle="actions-user", source="github")
              for k in ("target", "engaged", "far")],
        ]
        for p in people:
            s.add(p)
        s.commit()
        s.add(Email(contact_id=people[3].id, company_id=ids["engaged"], to_email="asha@vectorly.ai",
                    subject="s", body="b", status="draft"))
        for k, title in (("target", "RAG LangChain FAISS Qdrant engineer"), ("twin", "LangChain RAG FAISS Qdrant dev"),
                         ("far", "Python Docker engineer"), ("hyped", "LangChain RAG FAISS Qdrant")):
            s.add(Job(company_id=ids[k], company_name=k, title=title, url=f"https://x/{k}", source="careers",
                      description=title))
        s.commit()
        contact_ids = [p.id for p in people]
    with Session(engine) as s:
        companies = s.exec(select(Company)).all()
        contacts = s.exec(select(Contact)).all()
        jobs = s.exec(select(Job)).all()
    skills = sorted({x for v in PROFILE["skills"].values() for x in v})
    with Graph(path) as g:
        g.upsert_node("profile:me", "profile", "me", layer="data")
        for sk in skills:
            g.upsert_node(f"skill:{sk.lower()}", "skill", sk, layer="data")
        kgsync.sync_companies(g, companies)
        for cid, inv in ((ids["target"], "investor:tiny-seed"), (ids["twin"], "investor:tiny-seed"),
                         (ids["engaged"], "investor:mega"), (ids["target"], "investor:mega"),
                         (ids["far"], "investor:mega"), (ids["hyped"], "investor:tiny-seed")):
            g.upsert_node(inv, "investor", inv.split(":")[1], layer="data")
            g.upsert_edge(f"company:{cid}", "FUNDED_BY", inv, layer="data")
        kgsync.sync_jobs(g, jobs, skills)
        kgsync.sync_contacts(g, contacts, {c.id: c.domain for c in companies if c.domain})
        out = warm.sync_warm(g, companies, contacts, profile=PROFILE)
    return {"engine": engine, "path": path, "ids": ids, "contacts": contact_ids, "sync": out,
            "companies": companies, "people": contacts}


def test_background_aliases_and_city():
    bg = warm.background(PROFILE)
    by = {o["label"]: o for o in bg["orgs"]}
    assert "Newton School" in by["Newton School of Technology"]["aliases"]
    assert "Rishihood" in by["Rishihood University"]["aliases"]
    assert by["eWealth Tech"]["as"] == "employer" and by["MVRX Labs"]["as"] == "employer"
    assert bg["city"]["city"] == "Hyderabad"
    assert "Python" not in bg["skills"] and "LangChain" in bg["skills"]   # generic skills never a hook


def test_sync_derives_evidence_edges_and_skips_lookalikes_and_bots(world):
    asha, ben, cy, asha2, dee = world["contacts"][:5]
    with Graph(world["path"]) as g:
        aff = {(e["src"], e["dst"]): e["props"] for e in g.all_edges(rel="AFFILIATED_WITH")}
        prev = {(e["src"], e["dst"]) for e in g.all_edges(rel="PREVIOUSLY_AT")}
        same = {(e["src"], e["dst"]): e["props"] for e in g.all_edges(rel="SAME_PERSON")}
        city = {(e["src"], e["dst"]) for e in g.all_edges(rel="IN_CITY")}
    assert (f"contact:{asha}", "org:newton-school-of-technology") in aff
    assert "Newton School of Technology" in aff[(f"contact:{asha}", "org:newton-school-of-technology")]["quote"]
    assert not any(src == f"contact:{cy}" for src, _ in aff)              # Drivewealth != eWealth
    assert ("profile:me", "org:ewealth-tech") in aff
    assert (f"contact:{dee}", f"company:{world['ids']['target']}") in prev
    assert same == {(f"contact:{asha}", f"contact:{asha2}"): {"via": "linkedin", "key": "asha-rao"}}
    assert (f"company:{world['ids']['target']}", "city:hyderabad") in city


def test_resync_writes_nothing_and_drops_stale_evidence(world):
    with Graph(world["path"]) as g:
        again = warm.sync_warm(g, world["companies"], world["people"], profile=PROFILE)
    assert all(v["added_or_changed"] == 0 and v["removed"] == 0 for v in again.values())
    asha = next(p for p in world["people"] if p.id == world["contacts"][0])
    asha.role = "ML Engineer @ Inferly"                                    # headline no longer names the school
    with Graph(world["path"]) as g:
        out = warm.sync_warm(g, world["companies"], world["people"], profile=PROFILE)
        assert out["AFFILIATED_WITH"]["removed"] == 1
        assert not g.edges(f"contact:{asha.id}", rel="AFFILIATED_WITH")


def test_warm_paths_rank_school_first_with_evidence_and_no_addresses(world):
    ids = world["ids"]
    out = warm.warm_paths(ids["target"])
    first = out["people"][0]
    assert first["name"] == "Asha Rao"
    types = [p["type"] for p in first["paths"]]
    assert types[0] == "shared_school" and "same_person_engaged" in types
    assert first["paths"][0]["evidence"] and "Newton School" in first["paths"][0]["evidence"]
    assert "I'm a student there" in first["paths"][0]["opener"]
    ben = next(r for r in out["people"] if r["name"] == "Ben Ode")
    assert ben["paths"][0]["type"] == "shared_stack" and "RAG" in ben["paths"][0]["fact"]
    assert first["graph_signal"] > ben["graph_signal"]
    kinds = {p["type"] for p in out["company_paths"]}
    assert {"alumni_elsewhere", "investor_engaged", "same_city"} <= kinds
    inv = next(p for p in out["company_paths"] if p["type"] == "investor_engaged")
    assert "Vectorly" in inv["fact"]
    assert "@" not in json.dumps(out["people"]).replace("@ Inferly", "").replace("@farco", "")


def test_similar_companies_rare_investor_and_skills_not_city_alone(world):
    ids = world["ids"]
    out = warm.similar_companies(ids["target"])
    names = [r["name"] for r in out["similar"]]
    assert names[0] == "Embedly"                                           # rare shared fund + same skills + city
    assert "Hypeco" not in names                                           # overrated: never recommended
    top = out["similar"][0]
    assert any("tiny-seed" in w for w in top["why"]) and any("skills" in w for w in top["why"])
    farco = next(r for r in out["similar"] if r["name"] == "Farco")         # only the mega fund in common
    assert farco["score"] < top["score"]


def test_next_companies_seeds_from_engagement(world):
    out = warm.next_companies()
    assert "Vectorly" in out["seeds"]
    assert all(r["name"] != "Vectorly" for r in out["companies"])


def test_brief_section_and_routes(world, monkeypatch):
    asha, ben, cy = world["contacts"][:3]
    sec = warm.brief_section(asha)
    assert sec[0]["type"] == "shared_school" and set(sec[0]) == {"type", "fact", "opener", "evidence", "source"}
    assert warm.brief_section(cy) == []
    client = TestClient(api.app)
    r = client.get(f"/api/kg/warm/company/{world['ids']['target']}")
    assert r.status_code == 200 and r.json()["people"][0]["name"] == "Asha Rao"
    assert client.get(f"/api/kg/similar/{world['ids']['target']}?limit=2").json()["similar"][0]["name"] == "Embedly"
    assert client.get(f"/api/kg/warm/contact/{ben}").json()["paths"][0]["type"] == "shared_stack"
    assert client.get("/api/kg/warm/company/999999").status_code == 404
