"""Pattern approval and the review flow: a proposal becomes a draft, a draft becomes a
pattern (with the user's edits as its template), a matching draft is auto-approved only
when the switch is on and every gate passes, and revoking sends it back to review."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select

from jobhunter import proposals
from jobhunter.db import Company, Contact, Email, EmailPattern, EmailProposal, Job
from jobhunter.outreach import compose, drafter, patterns, sender

MISTRAL = ("Fine-tuned Mistral 7B (REV A) using LoRA/QLoRA on 2,000+ curated Q&A pairs with system prompt injection, "
           "improving response accuracy from 20% to 92%; deployed on RunPod Serverless with Qdrant for per-user vector memory")

@pytest.fixture(autouse=True)
def _generated_direct_intro(monkeypatch):
    """These tests exercise the role-matched direct_intro (no fixed template) with an
    opt-out line; the user's word-for-word template has its own tests."""
    from jobhunter.outreach import compose as _c

    monkeypatch.setattr(_c, "DIRECT_TEMPLATE", "")
    monkeypatch.setattr(_c, "DIRECT_OPT_OUT", True)
    monkeypatch.setattr(_c, "DIRECT_SIGN_OFF", "Thank you for your time,")
    monkeypatch.setitem(_c.SIGN_OFF_BY, "direct_intro", "Thank you for your time,")



def _brief(co: str):
    return {"stated": [{"text": "Fine-tune LLMs", "kind": "stated", "status": "covered",
                        "evidence_skills": ["LLMs", "Fine-tuning"], "quote": "You will fine-tune LLMs for our agents.",
                        "url": f"https://{co}.ai/jobs/1", "evidence": MISTRAL,
                        "evidence_where": "AI Lead Intern @ eWealth Tech LLC", "weight": 1.0}],
            "implied": [], "forbidden": ["Kubernetes"], "building": []}


@pytest.fixture()
def db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'p.db'}")
    SQLModel.metadata.create_all(engine)
    for mod in (drafter, sender, patterns, proposals):
        monkeypatch.setattr(mod, "get_session", lambda: Session(engine))
        monkeypatch.setattr(mod, "init_db", lambda: None)
    monkeypatch.setattr(patterns, "_OVERRIDE", True)
    return engine


def _person(engine, *, company: str, name: str, repo: str) -> tuple[int, int]:
    slug = company.split()[0].lower()
    with Session(engine) as s:
        co = Company(name=company, domain=f"{slug}.ai", website=f"https://{slug}.ai", team_size=120,
                     description=f"{company} builds voice agents used by banks and hospitals.")
        s.add(co)
        s.commit()
        s.refresh(co)
        job = Job(title="Backend Engineer", company_id=co.id, company_name=company, url=f"https://{slug}.ai/jobs/1",
                  source="ashby", needs_brief=json.dumps(_brief(slug)))
        p = Contact(company_id=co.id, name=name, email=f"{name.split()[0].lower()}@{slug}.ai", source="github",
                    confidence="verified", affiliation="verified-current", affiliation_source="org-member",
                    email_kind="company", role=f"Senior Software Engineer at {company}", role_class="engineer",
                    referral_rank=1, affiliation_evidence=f"company address, committed to {slug}/{repo} on 2026-09-01")
        s.add(job)
        s.add(p)
        s.commit()
        return p.id, job.id


def _edited(body: str) -> str:
    # the user's own line, generic enough to hold for anyone of this kind
    return body.replace("\n\nMy name is ", "\n\nI'd value even a one-line answer.\n\nMy name is ", 1)


# ------------------------------------------------------------------ templates

def test_a_template_keeps_the_users_words_and_leaves_the_persons_facts_open():
    vals = {"first_name": "Priya", "company": "Acme AI", "role": "Backend Engineer",
            "hook": "Saw your commits to acme/voice-sdk at Acme AI.", "proof1": "fine-tuned Mistral 7B"}
    t = patterns.to_template("Hi Priya,\n\nSaw your commits to acme/voice-sdk at Acme AI. I like it.\n\n"
                             "- Fine-tuned Mistral 7B\nThe Backend Engineer role at Acme AI?", vals)
    assert "{{hook}}" in t and "{{Proof1}}" in t and "Priya" not in t and "Acme AI" not in t and "I like it." in t
    out, missing = patterns.render(t, {**vals, "first_name": "Rahul", "company": "Beta Labs",
                                       "hook": "Saw your commits to beta/infra.", "proof1": "cut latency 88%"})
    assert not missing and out.startswith("Hi Rahul,") and "Cut latency 88%" in out and "Beta Labs?" in out
    assert patterns.render("{{proof2}} x", vals)[1] == ["proof2"]


# ------------------------------------------------------------------ the flow

def test_an_approved_pattern_auto_approves_the_next_email_of_its_kind_and_revoking_undoes_it(db):
    p1, j1 = _person(db, company="Acme AI", name="Priya Sharma", repo="voice-sdk")
    p2, j2 = _person(db, company="Beta Labs", name="Rahul Verma", repo="infra-kit")
    first = drafter.draft_for(p1, j1, research=False, use_model=False)
    assert "pattern" not in first           # no pattern yet: waits for review
    with Session(db) as s:
        e1 = s.get(Email, first["email_id"])
        assert e1.status == "draft" and e1.segment == "direct_intro:abroad"
        body = _edited(e1.body)
    r = patterns.approve_from(first["email_id"], body=body)
    t = r["pattern"]["body_template"]
    assert r["approved"] and r["pattern"]["edited"] and "{{role}}" in t and "{{company}}" in t and "{{proof1}}" in t
    assert "Priya" not in t and "{{out_line}}" in t

    second = drafter.draft_for(p2, j2, research=False, use_model=False)
    assert second["pattern"]["auto_approved"], second
    with Session(db) as s:
        e2 = s.get(Email, second["email_id"])
        assert e2.status == "approved" and e2.approved_by == "pattern" and e2.pattern_id == r["pattern"]["id"]
        assert e2.body.startswith("Hi Rahul,") and "one-line answer" in e2.body and "Priya" not in e2.body
        assert "Auto-approved by pattern" in e2.pattern_note
    listed = patterns.list_patterns()["patterns"][0]
    assert listed["auto_approved"] == 1 and listed["waiting_to_send"] == 1

    out = patterns.revoke(r["pattern"]["id"])
    assert out["back_to_review"] == 1
    with Session(db) as s:
        e2 = s.get(Email, second["email_id"])
        assert e2.status == "draft" and e2.approved_by is None and "revoked" in e2.pattern_note
        assert s.get(Email, first["email_id"]).status == "approved"     # his own approval is his
        assert s.get(EmailPattern, r["pattern"]["id"]).status == "revoked"


def test_with_the_switch_off_every_email_waits_for_review(db, monkeypatch):
    p1, j1 = _person(db, company="Acme AI", name="Priya Sharma", repo="voice-sdk")
    p2, j2 = _person(db, company="Beta Labs", name="Rahul Verma", repo="infra-kit")
    first = drafter.draft_for(p1, j1, research=False, use_model=False)
    assert patterns.approve_from(first["email_id"])["approved"]
    monkeypatch.setattr(patterns, "_OVERRIDE", False)
    second = drafter.draft_for(p2, j2, research=False, use_model=False)
    assert "pattern" not in second
    with Session(db) as s:
        assert s.get(Email, second["email_id"]).status == "draft"
    assert "error" in patterns.apply_to_waiting(1)


def test_a_pattern_that_loses_the_role_is_refused(db):
    p1, j1 = _person(db, company="Acme AI", name="Priya Sharma", repo="voice-sdk")
    first = drafter.draft_for(p1, j1, research=False, use_model=False)
    with Session(db) as s:
        body = s.get(Email, first["email_id"]).body.replace("the Backend Engineer opening", "an opening")
    r = patterns.approve_from(first["email_id"], body=body)
    assert "error" in r and "role" in r["error"]


def test_a_pattern_that_loses_the_hook_is_refused(db, monkeypatch):
    monkeypatch.setattr(compose, "STRUCTURE_MODE", "auto")
    p1, j1 = _person(db, company="Acme AI", name="Priya Sharma", repo="voice-sdk")
    first = drafter.draft_for(p1, j1, research=False, use_model=False)
    with Session(db) as s:
        e = s.get(Email, first["email_id"])
        hook = json.loads(e.contract)["hook"]["sentence"]
        body = e.body.replace(hook, "I like what your team ships.")
    r = patterns.approve_from(first["email_id"], body=body)
    assert "error" in r and "hook" in r["error"]
    with Session(db) as s:
        assert s.get(Email, first["email_id"]).status == "draft" and not s.exec(select(EmailPattern)).all()


def test_a_failing_gate_leaves_the_pattern_email_for_review(db):
    p1, j1 = _person(db, company="Acme AI", name="Priya Sharma", repo="voice-sdk")
    p2, j2 = _person(db, company="Beta Labs", name="Rahul Verma", repo="infra-kit")
    first = drafter.draft_for(p1, j1, research=False, use_model=False)
    with Session(db) as s:
        body = s.get(Email, first["email_id"]).body.replace("\n\nMy name is ", "\n\nI also know Kubernetes well.\n\nMy name is ", 1)
    assert patterns.approve_from(first["email_id"], body=body)["approved"]
    second = drafter.draft_for(p2, j2, research=False, use_model=False)
    assert not second["pattern"]["auto_approved"] and "waiting for your review" in second["pattern"]["note"]
    with Session(db) as s:
        assert s.get(Email, second["email_id"]).status == "draft"


def test_save_and_undo_an_approval(db):
    p1, j1 = _person(db, company="Acme AI", name="Priya Sharma", repo="voice-sdk")
    eid = drafter.draft_for(p1, j1, research=False, use_model=False)["email_id"]
    assert patterns.save(eid, subject="Backend Engineer at Acme AI?")["saved"]
    assert sender.approve(eid)["approved"]
    assert "error" in patterns.save(eid, subject="x")             # approved: undo first
    assert patterns.unapprove(eid)["unapproved"]
    with Session(db) as s:
        e = s.get(Email, eid)
        assert e.status == "draft" and e.subject == "Backend Engineer at Acme AI?" and e.approved_at is None


def test_a_proposal_becomes_a_draft_with_the_exact_text_and_shows_its_state(db):
    p1, j1 = _person(db, company="Acme AI", name="Priya Sharma", repo="voice-sdk")
    g = drafter.gather(p1, j1, research=False)
    r = drafter.write(g, use_model=False)
    payload = proposals._payload(g, r, 1)
    with Session(db) as s:
        row = EmailProposal(run_id="t", rank=1, contact_id=p1, company_id=g["company"]["id"], job_id=j1,
                            market="abroad", structure=r["structure"], payload=json.dumps(payload, default=str))
        s.add(row)
        s.commit()
        s.refresh(row)
        pid = row.id
    assert proposals.latest()["items"][0]["draft"]["state"] == "none"
    out = proposals.to_draft(pid)
    assert out["draft"]["state"] == "draft" and out["draft"]["body"] == payload["body"]
    assert out["draft"]["label"] == "Waiting for your review"
    assert proposals.to_draft(pid)["email_id"] == out["email_id"]          # a second click finds the same draft
    sender.approve(out["email_id"])
    assert proposals.latest()["items"][0]["draft"]["label"].startswith("Approved")


def test_the_routes(db):
    from jobhunter.outreach.routes import router
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)
    c = TestClient(app)
    p1, j1 = _person(db, company="Acme AI", name="Priya Sharma", repo="voice-sdk")
    eid = drafter.draft_for(p1, j1, research=False, use_model=False)["email_id"]
    assert c.get("/api/outreach/review/count").json()["waiting"] == 1
    assert c.post(f"/api/outreach/emails/{eid}/unapprove").status_code == 400
    r = c.post(f"/api/outreach/patterns/from-email/{eid}", json={})
    assert r.status_code == 200 and r.json()["pattern"]["label"].startswith("Who I am")
    assert c.get("/api/outreach/review/count").json() == {"waiting": 0, "approved_unsent": 1,
                                                          "auto_approved_unsent": 0, "pattern_approval": True}
    pats = c.get("/api/outreach/patterns").json()
    assert pats["enabled"] and len(pats["patterns"]) == 1
    assert c.post(f"/api/outreach/patterns/{pats['patterns'][0]['id']}/revoke").json()["revoked"]
