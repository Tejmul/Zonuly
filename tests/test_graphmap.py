"""The graph map (2026-10-14): /api/graph/map shaping — pending detection, the missing parts
and their reasons, the roll-up of pending ★ companies onto hubs and segments, the pipeline
chain, the project (context) view, shared-investor links, the per-tick cache — and the
one-company fill endpoint (a task, operator-only on a public instance).

The map's styleForNode (dashboard/lib/graph-style.ts) is a pure function of the leaf fields
asserted in test_leaf_contract_is_what_style_for_node_reads — keep the two in step."""

from __future__ import annotations

import json
import time
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

from jobhunter import access, api, fill, graphmap
from jobhunter.db import Company, Contact, Email, Job
from jobhunter.kg.store import Graph

DESC = "Acme builds payment rails for Indian merchants, from UPI to cards, with one API."


@pytest.fixture()
def world(tmp_path, monkeypatch):
    path = tmp_path / "g.db"
    engine = create_engine(f"sqlite:///{path}")
    SQLModel.metadata.create_all(engine)
    for mod in (graphmap, api):
        monkeypatch.setattr(mod, "get_session", lambda: Session(engine))
    monkeypatch.setattr(graphmap, "init_db", lambda: None)
    monkeypatch.setattr(graphmap, "KG_PATH", path)
    graphmap.invalidate()
    with Session(engine) as s:
        rows = {
            # complete, ★, India, drafted
            "a": Company(name="Acme", website="https://acme.in", description=DESC, city="Bengaluru",
                         list_key="india-star", list_rank=1, complete=True, complete_missing="[]",
                         hiring_status="verified", priority_score=80, best_selection_score=71,
                         filled_at=datetime(2026, 10, 14)),
            # ★, pending: no description, no referrer address — reasons from the fill pass
            "b": Company(name="Beta", website="https://beta.in", city="Hyderabad", list_key="india-star",
                         list_rank=2, complete=False, complete_missing='["description", "referrer_email"]',
                         fill_state=json.dumps({"description": "site blocks readers",
                                                "referrer_email": "1 ready referrer(s), none with an address",
                                                "linkedin": "LinkedIn backing off"}),
                         filled_at=datetime(2026, 10, 13), priority_score=60),
            # remote, never filled, no site
            "c": Company(name="Gamma", hq_region="us", complete=False,
                         complete_missing='["website", "description", "hiring", "referrer_email"]',
                         priority_score=40, tier="tier2"),
            # described, hiring missing — sits at "Described" in the pipeline
            "d": Company(name="Delta", website="https://delta.dev", description="Delta runs GPU inference for "
                         "AI teams at low latency and low cost.", hq_region="remote", complete=False,
                         complete_missing='["hiring", "referrer_email"]', filled_at=datetime(2026, 10, 12)),
            # hidden: overrated, quarantined, a ★ row under the pay bar, a reject
            "x1": Company(name="Huge", overrated=True, complete=False, complete_missing='["hiring"]'),
            "x2": Company(name="Junk", status="quarantined"),
            "x3": Company(name="Cheap", list_key="india-star", status="below_pay_bar"),
            "x4": Company(name="Nope", tier="reject"),
        }
        for c in rows.values():
            s.add(c)
        s.commit()
        ids = {k: c.id for k, c in rows.items()}
        s.add(Contact(company_id=ids["a"], name="Priya Sharma", email="priya@acme.in", source="linkedin-public",
                      affiliation="verified-current", email_kind="company", referral_rank=2))
        s.add(Contact(company_id=ids["b"], name="Rahul Verma", source="linkedin-public",
                      affiliation="verified-current", referral_rank=2))
        s.add(Job(company_id=ids["a"], company_name="Acme", title="Backend Engineer", url="https://acme.in/j/1",
                  source="careers"))
        s.add(Job(company_id=ids["c"], company_name="Gamma", title="ML Engineer", url="https://g.io/j/1",
                  source="ashby", remote_anywhere=True))
        s.commit()
        s.add(Email(contact_id=1, company_id=ids["a"], to_email="priya@acme.in", subject="s", body="b"))
        s.commit()
    with Graph(path) as g:
        for k in ("a", "b", "c"):
            g.upsert_node(f"company:{ids[k]}", "company", k, layer="data")
        g.upsert_node("investor:peak-xv", "investor", "Peak XV", layer="data")
        g.upsert_node("investor:solo", "investor", "Solo", layer="data")
        for k in ("a", "b"):
            g.upsert_edge(f"company:{ids[k]}", "FUNDED_BY", "investor:peak-xv", layer="data")
        g.upsert_edge(f"company:{ids['c']}", "FUNDED_BY", "investor:solo", layer="data")
        # the context layer
        g.upsert_node("stage:scrape", "stage", "1 · Scrape", props={"order": 1})
        g.upsert_node("stage:send", "stage", "10 · Send", props={"order": 10})
        g.upsert_edge("stage:scrape", "THEN", "stage:send")
        g.upsert_node("feature:boards", "feature", "ATS boards", props={"status": "built"})
        g.upsert_node("feature:ledger", "feature", "Send ledger", props={"status": "gap"})
        g.upsert_node("feature:old", "feature", "Dropped idea", props={"status": "dropped"})
        g.upsert_edge("feature:boards", "AT_STAGE", "stage:scrape")
        g.upsert_edge("feature:ledger", "AT_STAGE", "stage:send")
        g.upsert_edge("feature:old", "AT_STAGE", "stage:send")
        g.upsert_node("gap:1-send-ledger", "gap", "No send ledger")
        g.upsert_edge("gap:1-send-ledger", "WOULD_BUILD", "feature:ledger")
        g.upsert_node("constraint:send-cap-25", "constraint", "25 a day", props={"order": 1})
        g.upsert_edge("feature:ledger", "SERVES", "constraint:send-cap-25")
    return engine, ids


def _leaf(payload: dict, cid: int) -> dict:
    return next(l for l in payload["leaves"] if l.get("cid") == cid)


def test_market_view_marks_pending_with_parts_and_reasons(world):
    _, ids = world
    m = graphmap.build("market")
    cids = {l["cid"] for l in m["leaves"]}
    assert cids == {ids["a"], ids["b"], ids["c"], ids["d"]}          # hidden rows never drawn
    a, b, c = _leaf(m, ids["a"]), _leaf(m, ids["b"]), _leaf(m, ids["c"])
    assert a["status"] == "complete" and "missing" not in a and a["star"] and a["market"] == "india"
    assert a["referrers"] == 1 and a["referrers_email"] == 1 and a["roles"] == 1
    assert b["status"] == "filling" and b["missing"] == ["description", "referrer_email"]
    assert b["reasons"] == {"description": "site blocks readers",
                            "referrer_email": "1 ready referrer(s), none with an address"}
    assert b["notes"] == {"linkedin": "LinkedIn backing off"}           # not a bar part
    assert b["referrers"] == 1 and "referrers_email" not in b
    assert c["missing"] == ["website", "description", "hiring", "referrer_email"]
    assert c["no_site"] and c["no_description"] and "filled_at" not in c and "reasons" not in c
    assert c["market"] == "remote"                                      # a remote-anywhere posting
    assert _leaf(m, ids["d"])["market"] == "remote"
    assert m["counts"] == {"total": 4, "complete": 1, "filling": 3,
                           "missing": {"description": 2, "referrer_email": 3, "website": 1, "hiring": 2},
                           "star_pending": 1, "india_pending": 1, "remote_pending": 2, "never_filled": 1}


def test_roll_up_rings_the_hub_and_segment_holding_a_pending_star(world):
    _, ids = world
    m = graphmap.build("market")
    b = _leaf(m, ids["b"])
    hub = next(h for h in m["hubs"] if h["id"] == b["hub"])
    sub = next(s for s in m["subs"] if s["id"] == b["sub"])
    assert hub["star_pending"] >= 1 and sub["star_pending"] == 1 and sub["hub"] == hub["id"]
    assert sum(h["count"] for h in m["hubs"]) == 4 == sum(s["count"] for s in m["subs"])
    # a hub holding no pending ★ company carries no ring
    assert all(h["star_pending"] == 0 for h in m["hubs"] if h["id"] != b["hub"])
    # layers chain left to right, as the chokepoints map does
    chain = [l for l in m["links"] if l["kind"] == "chain"]
    assert [l["source"] for l in chain] == ["layer:infra", "layer:ai", "layer:dev", "layer:apps"]


def test_shared_investor_links_two_companies_not_a_lone_one(world):
    _, ids = world
    m = graphmap.build("market")
    inv = [l for l in m["links"] if l["kind"] == "investor"]
    assert inv == [{"source": f"company:{ids['a']}", "target": f"company:{ids['b']}", "kind": "investor",
                    "label": "Peak XV", "weight": 1}]
    assert m["investors"][0]["label"] == "Peak XV"


def test_pipeline_puts_each_company_at_its_furthest_stage(world):
    _, ids = world
    p = graphmap.build("pipeline")
    stage = {l["cid"]: l["stage"] for l in p["leaves"]}
    keys = [s["key"] for s in graphmap.PIPELINE]
    assert stage[ids["a"]] == keys.index("outreach")     # complete and drafted
    assert stage[ids["b"]] == keys.index("discovered")   # no description: the chain stops at its first gap
    assert stage[ids["c"]] == keys.index("discovered")
    assert stage[ids["d"]] == keys.index("described")    # site + description, no hiring proof
    hubs = {h["id"]: h for h in p["hubs"]}
    assert hubs["stage:discovered"]["reached"] == 4 and hubs["stage:outreach"]["reached"] == 1
    assert [l["weight"] for l in p["links"]] == [2, 1, 1, 1, 1]   # how many got past each gate
    assert hubs["stage:discovered"]["star_pending"] == 1


def test_project_view_reads_the_context_layer(world):
    p = graphmap.build("project")
    leaves = {l["id"]: l for l in p["leaves"]}
    assert set(leaves) == {"feature:boards", "feature:ledger", "gap:1-send-ledger"}   # dropped is off
    assert leaves["feature:boards"]["status"] == "complete"
    assert leaves["feature:ledger"]["status"] == "filling" and leaves["feature:ledger"]["missing"] == ["gap"]
    assert leaves["gap:1-send-ledger"]["hub"] == "stage:send"        # via the feature it would build
    hubs = {h["id"]: h for h in p["hubs"]}
    assert hubs["stage:scrape"]["row"] == 0 and hubs["constraint:send-cap-25"]["row"] == 1
    kinds = {(l["source"], l["kind"]) for l in p["links"]}
    assert ("stage:scrape", "chain") in kinds and ("feature:ledger", "serves") in kinds
    assert p["counts"]["filling"] == 2


def test_cache_holds_until_a_fill_lands(world):
    engine, ids = world
    first = graphmap.cached("market")
    assert graphmap.cached("market") is first
    with Session(engine) as s:
        c = s.get(Company, ids["c"])
        c.filled_at = datetime(2026, 10, 14, 12)
        s.add(c)
        s.commit()
    assert graphmap.cached("market") is not first


def test_leaf_contract_is_what_style_for_node_reads(world):
    """styleForNode reads: status (complete | filling), missing ⊆ parts, no_site /
    no_description (the dashed ring), star, market. Nothing else decides a pixel."""
    for view in ("market", "pipeline"):
        m = graphmap.build(view)
        parts = {p["key"] for p in m["parts"]}
        for l in m["leaves"]:
            assert l["status"] in ("complete", "filling")
            assert set(l.get("missing", [])) <= parts
            assert (l["status"] == "complete") == (not l.get("missing"))
            assert bool(l.get("no_site")) == ("website" in l.get("missing", []))
            assert bool(l.get("no_description")) == ("description" in l.get("missing", []))
            assert l["market"] in ("india", "remote", "abroad")
        for h in m["hubs"] + m["subs"]:
            assert h["count"] == h["complete"] + h["pending"]


def test_map_endpoint_and_fill_endpoint(world, monkeypatch):
    _, ids = world
    calls = []
    monkeypatch.setattr(fill, "run", lambda **kw: calls.append(kw) or {"companies": 1, "missing": {}})
    client = TestClient(api.app)
    r = client.get("/api/graph/map", params={"view": "pipeline"})
    assert r.status_code == 200 and r.json()["view"] == "pipeline"
    assert client.get("/api/graph/map", params={"view": "nope"}).status_code == 422

    r = client.post(f"/api/companies/{ids['b']}/fill")
    assert r.status_code == 200 and r.json()["company"] == "Beta"
    task_id = r.json()["task_id"]
    for _ in range(50):
        if api._tasks[task_id]["status"] != "running":
            break
        time.sleep(0.02)
    assert api._tasks[task_id]["status"] == "done" and api._tasks[task_id]["name"] == f"fill:{ids['b']}"
    assert calls == [{"ids": [ids["b"]], "people": True, "budget_s": 600}]
    assert client.post("/api/companies/999999/fill").status_code == 404


def test_fill_is_operator_only_on_a_public_instance(world, monkeypatch):
    _, ids = world
    monkeypatch.setattr(fill, "run", lambda **kw: {})
    monkeypatch.setattr(access, "PUBLIC", True)
    monkeypatch.setattr(access, "_KEY", "sekret")
    client = TestClient(api.app)
    assert client.post(f"/api/companies/{ids['b']}/fill").status_code == 403
    assert client.get("/api/graph/map").status_code == 200            # the map itself is public
    ok = client.post(f"/api/companies/{ids['b']}/fill", headers={"X-API-Key": "sekret"})
    assert ok.status_code == 200 and ok.json()["task_id"]


def test_a_running_fill_is_not_started_twice(world, monkeypatch):
    _, ids = world
    monkeypatch.setitem(api._tasks, "t-running", {"id": "t-running", "name": f"fill:{ids['c']}",
                                                  "status": "running", "started": "now", "result": None})
    r = TestClient(api.app).post(f"/api/companies/{ids['c']}/fill")
    assert r.json() == {"task_id": "t-running", "already_running": True, "company": "Gamma"}
