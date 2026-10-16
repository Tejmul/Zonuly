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
GTM = ("Built GTM OS, an agentic system scraping high-engagement CEO and CTO posts + leads for 10+ YC clients, "
       "auto-routed to Slack, drove 85% rise in client engagement")
DNS = "Managed DNS configurations for 8 company websites, led infrastructure troubleshooting, mentored interns"
ME = {"full_name": "Tejmul Movin", "first_name": "Tejmul", "school": "Newton School of Technology", "year": "2027",
      "resume_link": "drive.google.com/file/d/x/view", "bond": "CS+AI '27 grad",
      "shared_places": ["Newton School of Technology", "MVRX Labs"], "work_lines": [MISTRAL, TEMPORAL, GTM, DNS],
      "internships": 4}
SIG = "Tejmul Movin\nFinal-year B.Tech CS+AI, Newton School of Technology\nlinkedin.com/in/tejmulmovin"

@pytest.fixture(autouse=True)
def _generated_direct_intro(monkeypatch):
    """These tests exercise the role-matched direct_intro (no fixed template) with an
    opt-out line; the user's word-for-word template has its own tests."""
    from jobhunter.outreach import compose as _c

    monkeypatch.setattr(_c, "DIRECT_TEMPLATE", "")
    monkeypatch.setattr(_c, "DIRECT_OPT_OUT", True)
    monkeypatch.setattr(_c, "DIRECT_SIGN_OFF", "Thank you for your time,")
    monkeypatch.setitem(_c.SIGN_OFF_BY, "direct_intro", "Thank you for your time,")



@pytest.fixture()
def auto(monkeypatch):
    """The research picker (advice / give / fit / recruiter) instead of the user's default."""
    monkeypatch.setattr(compose, "STRUCTURE_MODE", "auto")


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


def test_no_hook_anywhere_means_no_draft(auto):
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

@pytest.mark.parametrize("role_class,gift,structure", [
    ("recruiter", False, "recruiter_direct"), ("tech_recruiter", True, "recruiter_direct"),
    ("founder", False, "fit_question"), ("eng_manager", False, "fit_question"),
    ("engineer", False, "advice_first"), ("senior_engineer", False, "advice_first"), (None, False, "advice_first"),
    ("engineer", True, "give_first"), ("founder", True, "give_first"),
])
def test_the_research_picker_chooses_by_who_they_are(role_class, gift, structure):
    assert compose.choose_structure(role_class, False, gift, mode="auto")[0] == structure


@pytest.mark.parametrize("role_class", ["engineer", "founder", "eng_manager", "recruiter", None])
def test_the_users_own_pattern_is_the_default_for_everyone(role_class):
    assert compose.STRUCTURE_MODE == "direct_intro"
    assert compose.choose_structure(role_class, True, True)[0] == "direct_intro"


def test_small_is_a_small_team_or_an_early_round():
    assert compose.is_small({"team_size": 25}) and not compose.is_small({"team_size": 400})
    assert compose.is_small({"funding_stage": "Seed"}) and compose.is_small({"funding_stage": "Series A"})
    assert not compose.is_small({"funding_stage": "Series C"})


CORPUS = "\n".join([MISTRAL, TEMPORAL, GTM, DNS, "Acme AI acme/voice-sdk https://acme.ai/jobs/1",
                    "Backend Engineer Voice Platform LLMs Fine-tuning Workflow orchestration 2027 40 Series B",
                    "Newton School of Technology Priya Sharma Temporal Technologies drive.google.com/file/d/x/view",
                    "MVRX Labs (YC-P26) eWealth Tech"])


def _check(c, f, subject=None):
    body = compose.assemble(f["message"], SIG, suppress.OPT_OUT_LINE or None, c.structure)
    subject = subject or compose.subject_options(c, ME)[0]["subject"]
    v = gates.check(body, corpus=CORPUS + "\n" + (resume.load_profile().get("_resume_text") or ""), signature=SIG,
                    forbidden=["Kubernetes"], exclude=[suppress.OPT_OUT_LINE], subject=subject,
                    word_band=compose.word_band(c))
    return body, subject, v


@pytest.mark.parametrize("role_class", ["engineer", "founder", "eng_manager", "recruiter"])
def test_the_users_pattern_in_order_for_every_recipient(role_class):
    person = _person(role_class=role_class, role=None if role_class != "engineer" else "Software Engineer")
    c = compose.contract(person, _company(), JOB, _brief(), ME)
    assert c.complete and c.structure == "direct_intro"
    f = compose.fill(c, ME)
    body, subject, v = _check(c, f)
    m = f["message"]
    # 1 greeting, 2 who he is, 3 track record, 4 the polite ask naming the role, 5 the out, 6 the resume
    order = ["Hi Priya,", "My name is Tejmul, and I'm a final-year B.Tech CS+AI student at Newton School of Technology.",
             "I've done four internships so far.", "20% to 92%",
             "I saw the Backend Engineer opening at Acme AI, and I'd be really grateful if you would consider referring me.",
             "If it isn't something you can help with, I completely understand.",
             "Resume: drive.google.com/file/d/x/view"]
    at = [m.index(x) for x in order]
    assert at == sorted(at), m
    # 7 the opt-out line (preflight requires it), then "Thank you for your time," and the signature
    assert body.index(suppress.OPT_OUT_LINE) < body.index("Thank you for your time,\nTejmul Movin")
    assert body.rstrip().endswith("linkedin.com/in/tejmulmovin")
    assert subject == "Referral for Backend Engineer at Acme AI"
    kinds = {x["kind"] for x in v.flags}
    assert compose.DIRECT_MIN_WORDS <= v.words <= compose.DIRECT_MAX_WORDS, v.words
    assert not kinds & {"faithfulness", "uncovered_claim", "ai_tell", "dashes", "length", "subject"}, v.flags
    assert "Kubernetes" not in body and "Sir" not in body and "hope this finds" not in body
    assert "forward" not in body.lower() and "—" not in body
    assert compose.keeps_contract(m, c) == [] and compose.old_shape(m, c) == []


def test_the_track_record_leads_with_the_role_and_has_three_or_four_distinct_results():
    c = compose.contract(_person(), _company(), JOB, _brief(), ME)
    lines = [p["proof"] for p in c.record]
    assert 3 <= len(lines) <= 4
    assert lines[0].startswith("fine-tuned Mistral 7B")            # the role's first need comes first
    assert any("Temporal" in x for x in lines) and not any("DNS" in x for x in lines)   # never the weak line
    nums = [n for x in lines for n in __import__("re").findall(r"\d[\d,]*%?", x)]
    assert len(nums) == len(set(nums))                               # no result told twice


def test_the_humility_line_follows_the_evidence_about_experience():
    plain = compose.contract(_person(), _company(), JOB, _brief(), ME)
    assert plain.more_experience is None
    assert compose.OUT_PLAIN in compose.fill(plain, ME)["message"]
    senior = compose.contract(_person(), _company(), {**JOB, "title": "Senior Backend Engineer"}, _brief(), ME)
    assert senior.more_experience and compose.OUT_MORE_EXPERIENCE in compose.fill(senior, ME)["message"]
    b = _brief()
    b["stated"][0]["quote"] = "You have 3+ years of experience fine-tuning LLMs."
    years = compose.contract(_person(), _company(), JOB, b, ME)
    assert "3+ years" in years.more_experience
    assert "more experience than I have" in compose.fill(years, ME)["message"]


@pytest.mark.parametrize("role_class,team", [("engineer", 120), ("founder", 120), ("eng_manager", 20),
                                             ("recruiter", 120)])
def test_every_research_structure_is_short_humble_and_has_one_question(auto, role_class, team):
    person = _person(role_class=role_class, role=None if role_class != "engineer" else "Software Engineer")
    c = compose.contract(person, _company(team_size=team), JOB, _brief(), ME)
    assert c.complete and c.structure in ("advice_first", "fit_question", "recruiter_direct")
    f = compose.fill(c, ME)
    body, subject, v = _check(c, f)
    kinds = {x["kind"] for x in v.flags}
    assert 60 <= v.words <= 110, (v.words, f["message"])
    assert not kinds & {"faithfulness", "uncovered_claim", "ai_tell", "dashes", "length", "subject"}, v.flags
    assert f["message"].startswith("Hi Priya,") and f["message"].count("?") == 1
    assert len(compose.links(f["message"])) <= 1 and c.resume_link not in body       # resume after a reply
    assert compose.humility(f["message"])["markers"] == ["thanks", "early", "tentative"]
    assert compose.keeps_contract(f["message"], c) == []
    assert 2 <= len(subject.split()) <= 5 and not subject.lower().startswith("referral")
    assert c.resume_link in f["forward_note"]                          # the after-yes asset keeps it


def test_give_first_when_he_used_their_product(auto):
    c = compose.contract(_person(), _company(name="Temporal Technologies"), JOB, _brief(), ME)
    assert c.structure == "give_first" and c.gift["thing"] == "Temporal"
    f = compose.fill(c, ME)
    m = f["message"]
    assert "I've used Temporal myself" in m and "might be naive" in m and m.count("?") == 1
    assert compose.subject_options(c, ME)[0]["subject"] == "A note on using Temporal"
    body, _, v = _check(c, f)
    assert not {x["kind"] for x in v.flags} & {"faithfulness", "length", "ai_tell"}, (v.flags, v.words)
    # a place he worked is shared ground, never a gift
    assert compose.gifts(_person(), _company(name="MVRX Labs"), ME) == []


def test_old_template_shapes_are_caught_and_scored_down(auto):
    c = compose.contract(_person(), _company(), JOB, _brief(), ME)
    old = ("Hi Priya,\n\nI came across your commits to acme/voice-sdk on GitHub. I'm Tejmul, applying for the Backend "
           "Engineer role at Acme AI. The posting asks for LLMs, and I fine-tuned Mistral 7B from 20% to 92%. Would you "
           "be open to referring me for it, or pointing me to whoever is hiring? If it's easier, here's a short note you "
           "can forward: \"Tejmul. Resume: drive.google.com/file/d/x/view\"")
    shape = compose.old_shape(old, c)
    assert any("forwardable" in x for x in shape) and any("resume" in x for x in shape)
    assert any("referral ask" in x for x in shape)
    new = compose.fill(c, ME)["message"]
    qo = compose.quality(old, "Referral for Backend Engineer at Acme AI?", c, [], len(gates._WORD.findall(old)))
    qn = compose.quality(new, compose.subject_options(c, ME)[0]["subject"], c, [], len(gates._WORD.findall(new)))
    assert qn["score"] > qo["score"] + 20 and qo["parts"]["shape"]["score"] <= 2
    assert compose.humility("Respected Sir, kindly refer me")["grovel"]


def test_volume_two_per_company_one_per_role():
    ts = [{"company_id": 1, "job_id": 10, "n": 1}, {"company_id": 1, "job_id": 10, "n": 2},
          {"company_id": 1, "job_id": 11, "n": 3}, {"company_id": 1, "job_id": 12, "n": 4},
          {"company_id": 2, "job_id": 20, "n": 5}]
    assert [t["n"] for t in compose.volume(ts)] == [1, 3, 5]


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
    ("Backend Engineer at Acme AI?", False), ("Referral for Backend Engineer at Acme AI", False),
    ("Quick question", True), ("Re: your post", True), ("Referral!", True), ("hi", True),
    ("FREE referral opportunity for you", True), ("URGENT: Backend Engineer role", True),
    ("A very long subject line that goes on and on about the backend engineer role", True),
])
def test_subject_rules(subject, bad):
    assert bool(gates.subject_problems(subject)) is bad


def test_subject_options_follow_the_structure_and_rotate_stably(auto):
    c = compose.contract(_person(role_class="engineer"), _company(team_size=10), JOB, _brief(), ME)
    opts = compose.subject_options(c, ME)
    assert c.structure == "advice_first" and opts[0] == {"pattern": "hook", "subject": "your voice-sdk commits"}
    assert all(not gates.subject_problems(o["subject"]) and len(o["subject"].split()) <= 5 for o in opts)
    assert compose.pick(opts, 7) == compose.pick(opts, 7)
    assert len({compose.pick(opts, k) for k in range(30)}) == len(opts)       # every pattern gets sends


# ------------------------------------------------------------------ quality

def _scored(c, message, flags=()):
    f = compose.fill(c, ME)
    words = len(gates._WORD.findall(message))
    subject = compose.subject_options(c, ME)[0]["subject"]
    return compose.quality(message, subject, c, list(flags), words) if message else None, f


def test_quality_rewards_a_specific_complete_email_and_marks_a_weak_one_rewrite():
    c = compose.contract(_person(), _company(), JOB, _brief(), ME)
    f = compose.fill(c, ME)
    good, _ = _scored(c, f["message"])
    assert good["score"] >= compose.GOOD_FROM and good["verdict"] == "good"
    weak = "Hi Priya,\n\n" + "I am writing to ask whether there might be any openings at your company that would suit someone like me and whether you could help me find one and what you think? " * 2
    bad, _ = _scored(c, weak, [{"kind": "faithfulness"}, {"kind": "ai_tell"}])
    assert bad["score"] < compose.REWRITE_BELOW and bad["verdict"] == "rewrite"
    assert set(good["parts"]) == {"personalisation", "hook", "length", "one_ask", "humility", "shape", "readability",
                                  "subject", "faithfulness"}


def test_the_users_pattern_is_not_penalised():
    c = compose.contract(_person(), _company(), JOB, _brief(), ME)
    good, f = _scored(c, compose.fill(c, ME)["message"])
    assert good["score"] >= 90, good
    assert good["parts"]["shape"]["score"] == 10 and good["parts"]["one_ask"]["score"] == 10
    assert good["parts"]["subject"]["score"] == 5 and good["parts"]["hook"]["score"] == 15


def test_a_company_hook_scores_below_a_personal_one(auto):
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
    assert r.get("email_id") and r["structure"] == "direct_intro" and r["writer"] == "template"
    with Session(db) as s:
        e = s.get(Email, r["email_id"])
        assert e.status == "draft" and e.quality >= 80 and e.hook_type == "person_commit", (e.quality, e.quality_detail)
        assert e.subject_pattern and e.words and json.loads(e.contract)["first_name"] == "Priya"
        assert "Kubernetes" not in e.body and suppress.OPT_OUT_LINE in e.body
        assert "Resume: " in e.body and "Thank you for your time,\n" in e.body


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


TEMPLATE = ("Hi {first_name},\n\nI'm Tejmul, a final-year Computer Science and AI student at Newton School of Technology.\n\n"
            "In my recent internships I shipped 3 production agentic systems at MVRX Labs (YC P26), fine-tuned Mistral 7B "
            "at eWealth Tech (accuracy from 20% to 92%), and moved a product to AWS Lambda, cutting infrastructure costs "
            "by 95%.\n\nI saw the {role} opening at {company}, and I'd be really grateful if you would consider referring "
            "me. I'd love the chance to contribute to your team and learn from the work you're doing.\n\n"
            "Resume: {resume_link}")


def test_the_users_own_template_word_for_word(monkeypatch):
    monkeypatch.setattr(compose, "DIRECT_TEMPLATE", TEMPLATE)
    c = compose.contract(_person(), _company(), JOB, _brief(), ME)
    msg = compose.fill(c, ME)["message"]
    assert msg.startswith(f"Hi {c.first_name},\n\nI'm Tejmul, a final-year Computer Science and AI student")
    assert f"I saw the {c.target_role} opening at {c.company}, and I'd be really grateful" in msg
    assert msg.endswith(f"Resume: {c.resume_link}")
    body = compose.assemble(msg, "Tejmul Movin\nlinkedin.com/in/x", None, "direct_intro")
    assert body.endswith("Thank you for your time,\nTejmul Movin\nlinkedin.com/in/x")   # sign-off from config


def test_the_users_own_template_passes_the_gates_and_scores_well(monkeypatch):
    monkeypatch.setattr(compose, "DIRECT_TEMPLATE", TEMPLATE)
    c = compose.contract(_person(), _company(), JOB, _brief(), ME)
    good, flags = _scored(c, compose.fill(c, ME)["message"])
    assert good["score"] >= 75, good          # high enough for pattern auto-approval
