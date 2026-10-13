"""The composer: the personalisation contract, structures by role, subject rules, word
limits, the quality score, variant stats, the tracker's lifecycle and thread view."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from sqlmodel import Session, SQLModel, create_engine

from jobhunter import resume
from jobhunter.db import Company, Contact, Email, Job, Reply, utcnow
from jobhunter.outreach import compose, drafter, gates, learn, sender, suppress, tracker

MISTRAL = ("Fine-tuned Mistral 7B (REV A) using LoRA/QLoRA on 2,000+ curated Q&A pairs with system prompt injection, "
           "improving response accuracy from 20% to 92%; deployed on RunPod Serverless with Qdrant for per-user vector memory")
TEMPORAL = ("Built agentic lead-gen pipeline (Apify scraping + ICP filtering); migrated Trigger.dev to Temporal, "
            "cutting infra cost 95%")
ME = {"full_name": "Tejmul Movin", "first_name": "Tejmul", "school": "Newton School of Technology", "year": "2027",
      "resume_link": "drive.google.com/file/d/x/view", "bond": "CS+AI '27 grad",
      "shared_places": ["Newton School of Technology", "MVRX Labs"]}


def _brief():
    return {
        "stated": [
            {"text": "Fine-tune LLMs", "kind": "stated", "status": "covered", "evidence_skills": ["LLMs", "Fine-tuning"],
             "quote": "You will fine-tune LLMs for our voice agents.", "url": "https://acme.ai/jobs/1",
             "evidence": MISTRAL, "evidence_where": "AI Lead Intern @ eWealth Tech LLC", "weight": 1.0},
            {"text": "Workflow orchestration", "kind": "stated", "status": "covered",
             "evidence_skills": ["Workflow orchestration"], "quote": "Experience with Temporal or similar.",
             "url": "https://acme.ai/jobs/1", "evidence": TEMPORAL,
             "evidence_where": "AI Product Engineer Intern @ MVRX Labs (YC-P26)", "weight": 0.8},
            {"text": "Kubernetes in production", "kind": "stated", "status": "uncovered", "skills": ["Kubernetes"],
             "missing_skills": ["Kubernetes"], "quote": "You run Kubernetes in production.", "url": "https://acme.ai/jobs/1"},
        ],
        "implied": [], "forbidden": ["Kubernetes"],
        "building": [{"text": "Acme launches Voice Agents API for Indian languages", "url": "https://acme.ai/blog/launch",
                      "source": "web"}],
    }


def _person(**kw):
    p = {"id": 7, "name": "Priya Sharma", "role": "Senior Software Engineer at Acme", "role_class": "engineer",
         "role_evidence": "LinkedIn: Senior Software Engineer at Acme",
         "affiliation_evidence": "company address, committed to acme/voice-sdk on 2026-09-01",
         "repos": [], "email": "priya@acme.ai", "confidence": "verified", "affiliation": "verified-current"}
    p.update(kw)
    return p


def _company(**kw):
    c = {"id": 1, "name": "Acme AI", "website": "https://acme.ai", "description": "Acme AI builds voice agents for "
         "Indian languages used by banks and hospitals.", "funding_stage": "Series B", "funding_amount_usd_m": 40.0,
         "funding_evidence": "https://news.example/acme-raises-40m", "team_size": 120}
    c.update(kw)
    return c


JOB = {"id": 3, "title": "Backend Engineer - Voice Platform", "url": "https://acme.ai/jobs/1"}


# ------------------------------------------------------------------ the contract

@pytest.mark.parametrize("name,ok", [
    ("Priya Sharma", "Priya"), ("PRIYA SHARMA", "Priya"), ("J. Ramesh", "Ramesh"), ("Théo Galy-Fajou", "Théo"),
    ("kbysani", None), ("jdoe_42", None), ("愛してる", None), ("Platform Company", None), ("", None), (None, None),
    ("Acme Careers", None), ("kbysani-affirm", None),
])
def test_first_name_is_a_real_name_or_nothing(name, ok):
    assert compose.first_name(name) == ok


def test_a_full_contract_is_complete_and_uses_the_most_specific_hook():
    c = compose.contract(_person(), _company(), JOB, _brief(), ME)
    assert c.complete and c.missing == []
    assert c.first_name == "Priya" and c.recipient_role == "Senior Software Engineer" and c.target_role == "Backend Engineer"
    assert c.hook["type"] == "person_commit" and "acme/voice-sdk" in c.hook["sentence"]
    assert c.proofs[0]["proof"].startswith("fine-tuned Mistral 7B")          # the curated phrasing of the resume line
    assert {h["type"] for h in c.hook_alternatives} >= {"company_launch", "company_funding"}


@pytest.mark.parametrize("change,missing", [
    ({"person": {"name": "kbysani"}}, "first_name"),
    ({"person": {"role": None, "role_class": "other", "role_evidence": None}}, "recipient_role"),
    ({"job": None}, "target_role"),
    ({"brief": {"stated": [], "implied": [], "building": []}}, "need"),
])
def test_a_missing_slot_means_no_draft(change, missing):
    person = _person(**change.get("person", {}))
    brief = change.get("brief", _brief())
    job = change["job"] if "job" in change else JOB
    c = compose.contract(person, _company(), job, brief, ME)
    assert missing in c.missing and not c.complete


def test_no_hook_anywhere_means_no_draft():
    person = _person(affiliation_evidence="their profile says 'Acme'", role="Engineer")
    company = _company(description=None, funding_evidence="★ India list (hand-picked): Series B", funding_stage="Series B")
    brief = {**_brief(), "building": []}
    c = compose.contract(person, company, JOB, brief, ME)
    assert "hook" in c.missing


def test_a_practice_repo_is_not_a_hook_but_a_real_one_is():
    junk = [{"name": "neetcode-submissions", "description": "My NeetCode problem submissions", "stars": 9, "url": "u"}]
    good = [{"name": "fastsse", "description": "Server-sent events for FastAPI with backpressure", "stars": 40, "url": "u2"}]
    hs = compose.hooks(_person(repos=junk, affiliation_evidence=""), _company(), [], ME)
    assert "person_repo" not in {h["type"] for h in hs}
    hs = compose.hooks(_person(repos=good, affiliation_evidence=""), _company(), [], ME)
    assert hs[0]["type"] == "person_repo" and "fastsse" in hs[0]["sentence"]


def test_shared_ground_beats_company_hooks():
    hs = compose.hooks(_person(role="SDE @ Acme | Newton School of Technology '24", affiliation_evidence=""), _company(), [], ME)
    assert hs[0]["type"] == "shared_ground"


# ------------------------------------------------------------------ structures

@pytest.mark.parametrize("role_class,small,structure", [
    ("recruiter", False, "recruiter_direct"), ("tech_recruiter", True, "recruiter_direct"),
    ("founder", True, "proof_opinion"), ("founder", False, "founder_direct"),
    ("engineer", True, "proof_opinion"), ("senior_engineer", False, "referral_engineer"),
    ("eng_manager", False, "referral_engineer"),
])
def test_structure_is_chosen_by_who_they_are(role_class, small, structure):
    assert compose.choose_structure(role_class, small)[0] == structure


def test_small_is_a_small_team_or_an_early_round():
    assert compose.is_small({"team_size": 25}) and not compose.is_small({"team_size": 400})
    assert compose.is_small({"funding_stage": "Seed"}) and compose.is_small({"funding_stage": "Series A"})
    assert not compose.is_small({"funding_stage": "Series C"})


@pytest.mark.parametrize("role_class,team", [("engineer", 120), ("engineer", 20), ("founder", 120), ("recruiter", 120)])
def test_every_structure_fills_inside_the_word_band_and_passes_the_gate(role_class, team):
    person = _person(role_class=role_class, role=None if role_class != "engineer" else "Software Engineer")
    c = compose.contract(person, _company(team_size=team), JOB, _brief(), ME)
    assert c.complete
    f = compose.fill(c, ME)
    body = compose.assemble(f["message"], f["blurb_intro"], f["blurb"], "Tejmul Movin", suppress.OPT_OUT_LINE or None)
    corpus = "\n".join([resume.load_profile().get("_resume_text") or "", MISTRAL, TEMPORAL, "Acme AI acme/voice-sdk",
                        "Backend Engineer Voice Platform LLMs Fine-tuning Workflow orchestration 2027 40 Series B",
                        "Newton School of Technology Priya Sharma"])
    subject = compose.subject_options(c, ME)[0]["subject"]
    v = gates.check(body, corpus=corpus, signature="Tejmul Movin", forbidden=["Kubernetes"],
                    exclude=[f["blurb_intro"], f["blurb"], suppress.OPT_OUT_LINE], subject=subject)
    kinds = {x["kind"] for x in v.flags}
    assert gates.MIN_WORDS <= v.words <= gates.MAX_WORDS, (v.words, f["message"])
    assert not kinds & {"faithfulness", "uncovered_claim", "ai_tell", "dashes", "length", "subject", "blurb"}, v.flags
    assert f["message"].startswith("Hi Priya,") and f["message"].count("?") == 1
    assert "Kubernetes" not in body                                   # an uncovered need is never claimed
    assert c.resume_link in f["blurb"] and body.rstrip().endswith("Tejmul Movin")
    assert compose.keeps_contract(f["message"], c) == []


def test_the_gate_counts_the_message_not_the_blurb_or_the_opt_out():
    msg = "Hi Priya,\n\n" + " ".join(["word"] * 70) + "."
    blurb = '"' + " ".join(["note"] * 40) + '"'
    v = gates.check(f"{msg}\n\n{blurb}\n\nstop line", corpus="", exclude=[blurb, "stop line"])
    assert 70 <= v.words <= 73
    long_blurb = '"' + " ".join(["xx"] * 80) + '"'
    v2 = gates.check(f"{msg}\n\n{long_blurb}", corpus="", exclude=[long_blurb])
    assert any(f["kind"] == "blurb" for f in v2.flags)


# ------------------------------------------------------------------ subjects

@pytest.mark.parametrize("subject,bad", [
    ("Backend Engineer at Acme AI?", False), ("Priya, Backend Engineer at Acme AI?", False),
    ("Quick question", True), ("Re: your post", True), ("Referral!", True), ("hi", True),
    ("FREE referral opportunity for you", True), ("URGENT: Backend Engineer role", True),
    ("A very long subject line that goes on and on about the backend engineer role", True),
])
def test_subject_rules(subject, bad):
    assert bool(gates.subject_problems(subject)) is bad


def test_subject_options_follow_the_structure_and_rotate_stably():
    c = compose.contract(_person(role_class="engineer"), _company(team_size=10), JOB, _brief(), ME)
    opts = compose.subject_options(c, ME)
    assert c.structure == "proof_opinion" and opts[0]["pattern"] == "proof_take"
    assert all(not gates.subject_problems(o["subject"]) for o in opts)
    assert compose.pick(opts, 7) == compose.pick(opts, 7)
    assert len({compose.pick(opts, k) for k in range(30)}) == len(opts)       # every pattern gets sends


# ------------------------------------------------------------------ quality

def _scored(c, message, flags=()):
    f = compose.fill(c, ME)
    words = len(gates._WORD.findall(message))
    return compose.quality(message, "Backend Engineer at Acme AI?", c, list(flags), words, has_blurb=True,
                           has_resume=True) if message else None, f


def test_quality_rewards_a_specific_complete_email_and_marks_a_weak_one_rewrite():
    c = compose.contract(_person(), _company(), JOB, _brief(), ME)
    f = compose.fill(c, ME)
    good, _ = _scored(c, f["message"])
    assert good["score"] >= compose.GOOD_FROM and good["verdict"] == "good"
    weak = "Hi Priya,\n\n" + "I am writing to ask whether there might be any openings at your company that would suit someone like me and whether you could help me find one and what you think? " * 2
    bad, _ = _scored(c, weak, [{"kind": "faithfulness"}, {"kind": "ai_tell"}])
    assert bad["score"] < compose.REWRITE_BELOW and bad["verdict"] == "rewrite"
    assert set(good["parts"]) == {"personalisation", "hook", "length", "one_ask", "readability", "subject", "faithfulness"}


def test_a_company_hook_scores_below_a_personal_one():
    personal = compose.contract(_person(), _company(), JOB, _brief(), ME)
    company = compose.contract(_person(affiliation_evidence=""), _company(), JOB, _brief(), ME)
    a, _ = _scored(personal, compose.fill(personal, ME)["message"])
    b, _ = _scored(company, compose.fill(company, ME)["message"])
    assert a["parts"]["hook"]["score"] > b["parts"]["hook"]["score"]


def test_every_configured_proof_is_true_to_the_resume():
    """The short proof phrasings in config must name nothing the resume does not."""
    prof = resume.load_profile()
    corpus = (prof.get("_resume_text") or "") + "\n" + resume.profile_summary(prof, max_chars=8000)
    lines = [h for e in prof.get("experience") or [] for h in e.get("highlights") or []] + \
            [p.get("description") or "" for p in prof.get("projects") or []]
    for p in compose.PROOFS:
        assert any(p["match"].lower() in ln.lower() for ln in lines), p["match"]
        v = gates.check("I " + p["line"] + ".", corpus=corpus)
        assert not v.unsupported, (p["line"], v.unsupported)


# ------------------------------------------------------------------ the DB side: incomplete rows, variants, thread

@pytest.fixture()
def db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'c.db'}")
    SQLModel.metadata.create_all(engine)
    for mod in (drafter, tracker, sender):
        monkeypatch.setattr(mod, "get_session", lambda: Session(engine))
        monkeypatch.setattr(mod, "init_db", lambda: None)
    return engine


def _world(engine, *, name="Priya Sharma", brief=True):
    with Session(engine) as s:
        co = Company(name="Acme AI", domain="acme.ai", website="https://acme.ai", team_size=120,
                     description="Acme AI builds voice agents for Indian languages used by banks and hospitals.")
        s.add(co)
        s.commit()
        s.refresh(co)
        job = Job(title="Backend Engineer", company_id=co.id, company_name="Acme AI", url="https://acme.ai/jobs/1",
                  source="ashby", needs_brief=json.dumps(_brief()) if brief else None)
        p = Contact(company_id=co.id, name=name, email="priya@acme.ai", source="github", confidence="verified",
                    affiliation="verified-current", affiliation_source="org-member", email_kind="company",
                    role="Senior Software Engineer at Acme", role_class="engineer", referral_rank=1,
                    affiliation_evidence="company address, committed to acme/voice-sdk on 2026-09-01")
        s.add(job)
        s.add(p)
        s.commit()
        return co.id, p.id, job.id


def test_draft_for_writes_a_scored_draft_with_its_variant_record(db):
    _, pid, jid = _world(db)
    r = drafter.draft_for(pid, jid, research=False, use_model=False)
    assert r.get("email_id") and r["structure"] == "referral_engineer" and r["writer"] == "template"
    with Session(db) as s:
        e = s.get(Email, r["email_id"])
        assert e.status == "draft" and e.quality >= 80 and e.hook_type == "person_commit"
        assert e.subject_pattern and e.words and json.loads(e.contract)["first_name"] == "Priya"
        assert "Kubernetes" not in e.body and suppress.OPT_OUT_LINE in e.body


def test_a_handle_gets_an_incomplete_row_that_can_never_be_approved_or_sent(db):
    _, pid, jid = _world(db, name="kbysani")
    r = drafter.draft_for(pid, jid, research=False, use_model=False)
    assert r["incomplete"] and "first_name" in r["missing"]
    with Session(db) as s:
        e = s.get(Email, r["email_id"])
        assert e.status == "incomplete" and e.body == ""
    assert "error" in sender.approve(r["email_id"])
    sender.reject(r["email_id"])
    assert "error" in sender.approve(r["email_id"])            # rejected, still empty: never approvable
    assert "error" in sender.send_email(r["email_id"], ignore_window=True)


def test_variant_stats_wait_for_enough_sends():
    rows = [{"replied": i % 4 == 0, "positive": i % 8 == 0, "structure": "proof_opinion", "subject_pattern": "proof_take",
             "hook_type": "person_commit", "send_hour": 11, "quality": 90, "writer": "template", "kind": "cold"}
            for i in range(24)]
    rows += [{"replied": True, "positive": True, "structure": "founder_direct", "subject_pattern": "role_q",
              "hook_type": "company_funding", "send_hour": 15, "quality": 70, "writer": "model", "kind": "cold"}] * 3
    v = learn.variants(rows, min_sent=20)
    po, fd = v["by_structure"]["proof_opinion"], v["by_structure"]["founder_direct"]
    assert po["enough"] and po["reply_rate"] == 0.25 and po["sent"] == 24
    assert not fd["enough"] and fd["reply_rate"] is None and "not enough data yet" in fd["note"]
    assert "11:00–12:00" in v["by_send_hour"] and "80+" in v["by_quality"]


def test_the_thread_shows_the_mail_as_sent_the_follow_up_and_the_reply_with_its_reason(db):
    cid, pid, jid = _world(db)
    with Session(db) as s:
        e = Email(contact_id=pid, company_id=cid, job_id=jid, to_email="priya@acme.ai", subject="Referral?",
                  body="Hi Priya,\n\nexact body", kind="cold", status="replied", sent_at=utcnow() - timedelta(days=8),
                  structure="referral_engineer", quality=91, quality_detail=json.dumps({"score": 91, "verdict": "good", "parts": {}}))
        s.add(e)
        s.commit()
        s.refresh(e)
        f = Email(contact_id=pid, company_id=cid, to_email="priya@acme.ai", subject="Re: Referral?", body="Hi Priya, one more",
                  kind="followup", parent_email_id=e.id, status="sent", sent_at=utcnow() - timedelta(days=2))
        s.add(f)
        s.commit()
        s.refresh(f)
        s.add(Reply(email_id=f.id, gmail_message_id="m1", from_addr="Priya <priya@acme.ai>", body="Happy to refer you!",
                    sentiment="positive", sentiment_reason="agreed to refer | next: send the resume link"))
        s.commit()
        eid, fid = e.id, f.id
    t = tracker.thread(eid)
    assert [m["type"] for m in t["messages"]] == ["ours", "ours", "theirs"]
    assert t["messages"][0]["body"] == "Hi Priya,\n\nexact body" and t["messages"][1]["kind"] == "followup"
    assert t["messages"][2]["sentiment"] == "positive" and t["messages"][2]["reason"] == "agreed to refer"
    assert t["messages"][2]["next"] == "send the resume link"
    assert t["lifecycle"]["stage"] == "replied" and t["quality"]["score"] == 91
    assert tracker.thread(fid)["email_id"] == eid          # opened from the follow-up: the same conversation
    track = tracker.track()
    row = next(r for r in track["rows"] if r["email_id"] == eid)
    assert row["stage"] == "replied" and row["sentiment"] == "positive" and track["counts"]["reply_positive"] == 1

    from fastapi import HTTPException

    from jobhunter import api

    assert api.outreach_thread(eid)["messages"][2]["from"].startswith("Priya")
    with pytest.raises(HTTPException):
        api.outreach_thread(999999)


def test_lifecycle_waiting_bounced_and_opted_out(db):
    cid, pid, _ = _world(db)
    with Session(db) as s:
        sent = utcnow() - timedelta(days=3)
        w = Email(contact_id=pid, company_id=cid, to_email="a@acme.ai", subject="s", body="b", status="sent", sent_at=sent)
        b = Email(contact_id=pid, company_id=cid, to_email="b@acme.ai", subject="s", body="b", status="failed",
                  sent_at=sent, error="bounced: 550 no such user")
        s.add(w)
        s.add(b)
        s.commit()
        s.refresh(w)
        s.refresh(b)
        lw = tracker.lifecycle(w, [], [])
        assert lw["stage"] == "waiting" and lw["days_waiting"] == 3
        assert lw["followup_due"] == (sent + timedelta(days=5)).date().isoformat()
        assert tracker.lifecycle(b, [], [])["stage"] == "bounced"
        r = Reply(email_id=w.id, from_addr="a", body="stop", sentiment="negative",
                  sentiment_reason="asked not to be contacted again (\"stop\") | next: nothing")
        assert tracker.lifecycle(w, [r], [])["stage"] == "opted_out"


def test_a_funding_hook_quotes_the_amount_its_evidence_states_never_a_stored_total():
    hs = compose.hooks(_person(affiliation_evidence=""), _company(funding_amount_usd_m=263.0,
                       funding_evidence="Dwelly Raises $170M Series B to Automate UK Lettings"), [], ME)
    fund = next(h for h in hs if h["type"] == "company_funding")
    assert "$170M" in fund["sentence"] and "263" not in fund["sentence"]
    hs = compose.hooks(_person(affiliation_evidence=""), _company(funding_amount_usd_m=263.0,
                       funding_evidence="Acme announces its Series B round"), [], ME)
    assert "$" not in next(h for h in hs if h["type"] == "company_funding")["sentence"]
