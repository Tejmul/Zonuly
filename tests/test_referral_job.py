"""The dedicated referral job (referrals.run_cycle, launchd com.zonuly.referrals) and the
People page's /api/leads: queue order, skip-at-target, the lock, claims, source/city filters."""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

from jobhunter import access, api, referrals, tick
from jobhunter.db import Company, Contact, utcnow


@pytest.fixture()
def engine(tmp_path, monkeypatch):
    eng = create_engine(f"sqlite:///{tmp_path / 'j.db'}")
    SQLModel.metadata.create_all(eng)
    for mod in (referrals, api):
        monkeypatch.setattr(mod, "get_session", lambda: Session(eng))
    monkeypatch.setattr(referrals, "init_db", lambda: None)
    monkeypatch.setattr(referrals, "_log_run", lambda *a, **k: None)
    return eng


def _ready(cid: int, n: int, *, email: bool = True, source: str = "linkedin-public",
           aff_source: str = "linkedin-headline", start: int = 0) -> list[Contact]:
    return [Contact(company_id=cid, name=f"Priya Sharma{'abcdefghij'[start + i]}",
                    email=f"p{start + i}@c{cid}.com" if email else None, source=source,
                    affiliation="likely-current", affiliation_source=aff_source,
                    affiliation_evidence="public LinkedIn headline: SDE II at Acme",
                    linkedin=f"https://www.linkedin.com/in/p-{cid}-{start + i}" if source == "linkedin-public" else None)
            for i in range(n)]


def _world(engine) -> dict[str, int]:
    rows = {
        "blr": Company(name="Blr Co", city="Bengaluru", hq_region="india", priority_score=50),
        "blr_hi": Company(name="Blr Hi", city="Bengaluru", hq_region="india", priority_score=90),
        "blr_star": Company(name="Blr Star", city="Bengaluru", hq_region="india", list_key="india-star",
                            list_rank=4, priority_score=10),
        "hyd": Company(name="Hyd Co", city="Hyderabad", hq_region="india", priority_score=99),
        "hyd_star": Company(name="Hyd Star", city="Hyderabad", hq_region="india", list_key="india-star", list_rank=1),
        "india": Company(name="Pune Co", hq_region="india", priority_score=95),
        "remote": Company(name="Remote Co", hq_region="remote", priority_score=100),
        "abroad": Company(name="Tokyo Co", hq_region="jp", priority_score=100),
        "done": Company(name="Done Co", city="Bengaluru", hq_region="india", priority_score=100),
        "touched": Company(name="Touched Co", city="Bengaluru", hq_region="india", priority_score=80,
                           contacts_found_at=utcnow() - timedelta(minutes=10)),
        "rested": Company(name="Rested Co", city="Bengaluru", hq_region="india", priority_score=70,
                          notes=f"[refer] {utcnow():%Y-%m-%d}: [0, 0]→[1, 0]"),
    }
    with Session(engine) as s:
        for c in rows.values():
            s.add(c)
        s.commit()
        ids = {k: c.id for k, c in rows.items()}
        for p in _ready(ids["done"], 3):            # at target: 3 ready, ≥1 with an address
            s.add(p)
        for p in _ready(ids["blr"], 2):             # short: 2 ready
            s.add(p)
        s.commit()
    return ids


def test_queue_order_is_bengaluru_then_hyderabad_then_india_then_remote_star_first(engine):
    ids = _world(engine)
    q = referrals.cycle_queue()
    name = {v: k for k, v in ids.items()}
    assert [name[i] for i in q] == ["blr_star", "blr_hi", "blr", "hyd_star", "hyd", "india", "remote"]


def test_skip_at_target_touched_rested_and_excluded(engine):
    ids = _world(engine)
    q = referrals.cycle_queue()
    assert ids["done"] not in q                      # 3 ready with an address: at target
    assert ids["touched"] not in q                   # another worker touched it in the last hour
    assert ids["rested"] not in q                    # passed today: resting
    assert ids["abroad"] not in q                    # not India, not a remote target
    assert ids["blr"] in q                           # 2 ready is short of 3
    assert ids["blr_hi"] not in referrals.cycle_queue(exclude={ids["blr_hi"]})
    # a company short of an address is still due even with 3 ready
    with Session(engine) as s:
        for p in _ready(ids["blr"], 1, email=False, start=5):
            s.add(p)
        s.commit()
    assert referrals._counts(ids["blr"]) == (3, 2)
    assert ids["blr"] not in referrals.cycle_queue()


def test_claims_keep_the_tick_and_the_job_off_each_other(engine):
    ids = _world(engine)
    assert referrals.claim(ids["blr"], "referrals", ttl_s=600)
    assert not referrals.claim(ids["blr"], "tick")              # held by the job
    assert ids["blr"] not in referrals.cycle_queue(owner="tick")
    assert ids["blr"] in referrals.cycle_queue(owner="referrals")
    assert referrals.claimed_by_others("tick") == {ids["blr"]}
    referrals.release(ids["blr"], "referrals")
    assert referrals.claim(ids["blr"], "tick")
    # expired claims do not count, and are pruned
    assert referrals.claim(ids["hyd"], "referrals", ttl_s=-5)
    assert ids["hyd"] not in referrals.claims()
    assert referrals.prune_claims() == 1


def test_run_cycle_ends_early_when_every_keyless_backend_cools(engine, monkeypatch):
    from jobhunter.research import web

    _world(engine)
    seen = []
    monkeypatch.setattr(web, "search_down", lambda: True)
    monkeypatch.setattr(referrals, "DOWN_TAIL", 2)
    monkeypatch.setattr(referrals, "refer_company",
                        lambda cid, search_ok=True: seen.append((cid, search_ok)) or {"bucket": "Bengaluru"})
    out = referrals.run_cycle(budget_s=60, limit=10)
    assert len(seen) == 2 and all(ok is False for _, ok in seen)     # no web search while it cools
    assert "ended early" in out["stopped"] and out["companies"] == 2
    assert referrals.claims() == {}                                   # the window is released


def test_run_cycle_counts_by_channel(engine, monkeypatch):
    from jobhunter.research import web

    _world(engine)
    monkeypatch.setattr(web, "search_down", lambda: False)
    monkeypatch.setattr(referrals, "refer_company", lambda cid, search_ok=True: {
        "bucket": "Bengaluru", "queries": 3, "added_by_channel": {"linkedin": 2, "github": 1},
        "emails_found": 1, "reached_target": True})
    out = referrals.run_cycle(budget_s=60, limit=2)
    assert out["companies"] == 2 and out["added_ready"] == 6 and out["linkedin_added"] == 4
    assert out["added_by_channel"] == {"linkedin": 4, "github": 2} and out["reached_target"] == 2


def test_lock_never_overlaps_itself(tmp_path, monkeypatch):
    lock = tmp_path / "referrals.lock"
    monkeypatch.setattr(tick, "REFER_LOCK", lock)
    monkeypatch.setattr(tick, "_record", lambda *a, **k: None)
    calls = []
    monkeypatch.setattr(referrals, "run_cycle", lambda **kw: calls.append(kw) or {"companies": 0})
    with tick._Lock(lock, "referral cycle"):
        out = tick.run_referrals()
        assert out["skipped"] == "another referral cycle is running" and not calls
        # the tick's own lock is a different file: the job runs beside a tick
        assert tick.LOCK != lock
    assert tick.run_referrals()["kind"] == "referrals" and len(calls) == 1


def test_the_agent_plist_runs_every_15_minutes_with_its_own_log():
    p = tick._refer_plist()
    assert p["Label"] == "com.zonuly.referrals" and p["StartInterval"] == 900 and p["RunAtLoad"] is True
    assert p["ProgramArguments"][-2:] == ["referrals", "run"]
    assert p["EnvironmentVariables"]["PATH"] == tick._plist("free")["EnvironmentVariables"]["PATH"]
    assert p["StandardOutPath"].endswith("referrals.log")


@pytest.mark.parametrize("source,aff,want", [
    ("linkedin-public", "linkedin-headline", "linkedin"), ("pattern", "linkedin-headline", "linkedin"),
    ("github", "org-member", "github"), ("pattern", "profile-company", "github"),
    ("site", "team-page", "team"), ("web", "team-page", "team"), ("public-page", "public-mention", "talks"),
    ("web", "company-address", "web")])
def test_lead_channel(source, aff, want):
    assert referrals.lead_channel(source, aff) == want


def test_leads_api_filters_by_source_and_city_and_paginates(engine):
    ids = _world(engine)
    with Session(engine) as s:
        for p in _ready(ids["hyd"], 2, source="github", aff_source="org-member"):
            s.add(p)
        for p in _ready(ids["remote"], 1, source="site", aff_source="team-page", email=False):
            s.add(p)
        s.add(Contact(company_id=ids["blr"], name="Old Hand", source="linkedin-public", affiliation="unconfirmed",
                      linkedin="https://www.linkedin.com/in/old"))
        s.commit()
    client = TestClient(api.app)
    r = client.get("/api/leads").json()
    # ready only: done 3 + blr 2 + hyd 2 + remote 1; Bengaluru first
    assert r["total"] == 8 and r["counts"] == {"total": 8, "linkedin": 5, "with_email": 7,
                                               "by_source": {"linkedin": 5, "github": 2, "team": 1, "talks": 0, "web": 0}}
    assert [i["bucket"] for i in r["items"]] == ["Bengaluru"] * 5 + ["Hyderabad"] * 2 + ["Remote"]
    li = client.get("/api/leads", params={"source": "linkedin"}).json()
    assert li["total"] == 5 and all(i["channel"] == "linkedin" and i["linkedin"].startswith("https://www.linkedin.com/in/")
                                     for i in li["items"])
    hyd = client.get("/api/leads", params={"city": "hyderabad"}).json()
    assert hyd["total"] == 2 and hyd["counts"]["linkedin"] == 0 and {i["channel"] for i in hyd["items"]} == {"github"}
    assert client.get("/api/leads", params={"city": "remote", "source": "team"}).json()["total"] == 1
    page = client.get("/api/leads", params={"limit": 3, "offset": 3}).json()
    assert len(page["items"]) == 3 and page["total"] == 8
    assert client.get("/api/leads", params={"who": "all"}).json()["total"] == 9
    assert client.get("/api/leads", params={"source": "nope"}).status_code == 422
    assert client.get("/api/leads", params={"city": "pune"}).status_code == 422


def test_leads_api_masks_addresses_on_the_public_instance(engine, monkeypatch):
    _world(engine)
    monkeypatch.setattr(access, "PUBLIC", True)
    monkeypatch.setattr(access, "_KEY", "sekret")
    r = TestClient(api.app).get("/api/leads").json()
    assert r["items"] and all("•••@" in i["email"] for i in r["items"] if i["email"])
