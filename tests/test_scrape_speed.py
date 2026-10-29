"""2026-10-29, "what can be done to increase speed": the best bets lead the people queue, the
people step works several companies at once, the whole-table recomputes run at most hourly,
and a DuckDuckGo bot challenge rests that engine long enough to stop retrying it."""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta

import pytest
from sqlmodel import Session, SQLModel, create_engine

from jobhunter import referrals, tick
from jobhunter.db import Company, CompanyDecision, get_setting, set_setting
from jobhunter.research import web


@pytest.fixture()
def engine(tmp_path, monkeypatch):
    eng = create_engine(f"sqlite:///{tmp_path / 's.db'}")
    SQLModel.metadata.create_all(eng)
    monkeypatch.setattr(referrals, "get_session", lambda: Session(eng))
    monkeypatch.setattr(referrals, "init_db", lambda: None)
    monkeypatch.setattr(referrals.guardrails, "ready_counts", lambda s: {})
    return eng


def _decision(cid: int, pool: str, rank: int | None) -> CompanyDecision:
    return CompanyDecision(company_id=cid, pool=pool, verdict="target", p_offer=0.1, p_lo=0, p_hi=0.2, p_open=1,
                           p_reach=1, p_screen=1, p_call=1, p_loop=1, p_pay=1, dsa=0, info=1, rank=rank)


def test_best_bets_lead_the_people_queue_in_shortlist_order(engine):
    with Session(engine) as s:
        ids = []
        for i, prio in enumerate((90, 80, 70, 10, 5)):
            c = Company(name=f"C{i}", domain=f"c{i}.com", priority_score=prio)
            s.add(c)
            s.commit()
            s.refresh(c)
            ids.append(c.id)
        # the two lowest-priority companies are the best bets: Remote #1 and Bengaluru #2
        s.add(_decision(ids[4], "Remote", 1))
        s.add(_decision(ids[3], "Bengaluru", 2))
        s.add(_decision(ids[0], "Bengaluru", None))   # decided, outside the shortlist
        s.commit()
    q = referrals.people_queue()
    assert q[:2] == [ids[3], ids[4]]                  # Bengaluru before Remote, whatever the priority
    assert set(q[2:]) == {ids[0], ids[1], ids[2]}


def test_a_best_bet_rests_the_shortest_time(engine):
    with Session(engine) as s:
        c = Company(name="Rested", domain="r.com", tier="reject",
                    notes=f"[refer] {(datetime.now() - timedelta(days=4)).date().isoformat()}")
        s.add(c)
        s.commit()
        s.refresh(c)
        s.add(_decision(c.id, "Bengaluru", 1))
        s.commit()
        cid = c.id
    assert cid in referrals.people_queue()


def test_people_step_works_companies_at_once_and_counts_each_once(monkeypatch):
    seen, active, peak = [], [0], [0]
    lock = threading.Lock()

    def fake(cid, linkedin=True):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.15)
        with lock:
            active[0] -= 1
            seen.append(cid)
        return {"before": [0, 0], "after": [1, 1], "linkedin": 1}

    monkeypatch.setattr(referrals, "people_for_company", fake)
    monkeypatch.setattr(referrals, "_log_run", lambda kind, out: None)
    monkeypatch.setattr(referrals, "PEOPLE_WORKERS", 3)
    monkeypatch.setattr(web, "search_down", lambda: False)
    t = time.time()
    out = referrals.people_step(ids=list(range(1, 7)), budget_s=60)
    assert sorted(seen) == list(range(1, 7))
    assert out["companies"] == 6 and sorted(out["touched"]) == list(range(1, 7))
    assert out["added_ready"] == 6 and out["gained_email"] == 6 and out["linkedin_added"] == 6
    assert peak[0] == 3 and out["workers"] == 3
    assert time.time() - t < 0.15 * 6          # in parallel, not one after another


def test_people_step_stops_taking_companies_at_the_budget(monkeypatch):
    monkeypatch.setattr(referrals, "people_for_company",
                        lambda cid, linkedin=True: time.sleep(0.2) or {"before": [0, 0], "after": [0, 0]})
    monkeypatch.setattr(referrals, "_log_run", lambda kind, out: None)
    monkeypatch.setattr(referrals, "PEOPLE_WORKERS", 2)
    monkeypatch.setattr(web, "search_down", lambda: False)
    out = referrals.people_step(ids=list(range(1, 50)), budget_s=0.3)
    assert out["stopped"] and out["companies"] < 10


def test_recompute_runs_at_most_every_recompute_minutes(tmp_path, monkeypatch):
    eng = create_engine(f"sqlite:///{tmp_path / 't.db'}")
    SQLModel.metadata.create_all(eng)
    monkeypatch.setattr("jobhunter.db.get_session", lambda: Session(eng))
    monkeypatch.setattr(tick, "RECOMPUTE_MINUTES", 60)
    assert tick._recompute_due()                       # never ran
    tick._recomputed()
    assert not tick._recompute_due()
    with Session(eng) as s:
        set_setting(s, tick.RECOMPUTE_KEY, (datetime.now() - timedelta(minutes=61)).isoformat(timespec="seconds"))
        assert get_setting(s, tick.RECOMPUTE_KEY)
    assert tick._recompute_due()


def test_a_ddg_bot_challenge_rests_ddg_for_the_long_cooldown(monkeypatch):
    monkeypatch.setattr(web.channels, "record", lambda *a, **k: None)
    monkeypatch.setattr(web, "_jina_read_engine",
                        lambda name, url, timeout: "If this persists... bots use DuckDuckGo too")
    monkeypatch.setitem(web._kl_state, "jina-ddg", {"fails": 0, "cool_until": 0.0})
    assert web._jina_ddg_search("acme engineers", 5, 10) == []
    assert web._kl_state["jina-ddg"]["cool_until"] - time.monotonic() > web._DDG_CHALLENGE_COOLDOWN_S - 5
    assert web._kl_cooling("jina-ddg") and not web._kl_cooling("jina-startpage")


def test_catch_up_does_not_redraft_a_company_whose_draft_was_rejected(engine, monkeypatch):
    from jobhunter import shortlist
    from jobhunter.db import Contact, Email

    monkeypatch.setattr(shortlist, "get_session", lambda: Session(engine))
    monkeypatch.setattr(shortlist, "init_db", lambda: None)
    drafted = []
    monkeypatch.setattr(shortlist, "draft", lambda cid, job_id=None: drafted.append(cid) or {"email_id": 1})
    with Session(engine) as s:
        ids = []
        for name in ("Recro", "Fresh"):
            c = Company(name=name, domain=f"{name.lower()}.com")
            s.add(c)
            s.commit()
            s.refresh(c)
            ids.append(c.id)
            s.add(Contact(company_id=c.id, name="Asha Rao", email=f"asha@{name.lower()}.com", role_class="engineer",
                          confidence="verified", source="github"))
            s.add(_decision(c.id, "Bengaluru", len(ids)))
        s.add(Email(company_id=ids[0], contact_id=1, to_email="asha@recro.com", subject="s", body="b",
                    kind="cold", status="rejected"))
        s.commit()
    shortlist.catch_up(limit=2)
    assert drafted == [ids[1]]
