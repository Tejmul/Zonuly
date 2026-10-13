"""Email drafting — referral asks and follow-ups.

Every draft lands in the review queue, never the outbox. The prompt is written to
keep a small local model honest: it is given the candidate's real background and
told, repeatedly, that inventing anything is worse than being generic.
"""

from __future__ import annotations

import json
import logging
import re

from sqlmodel import col, select

from jobhunter import CONFIG, admission, guardrails, openrouter
from jobhunter.db import Company, Contact, Email, Job, get_session, init_db, utcnow

log = logging.getLogger(__name__)

_O = CONFIG["outreach"]
USER_NAME = _O.get("user_name") or "(set outreach.user_name in config.yaml)"
USER_HEADLINE = _O.get("user_headline") or ""
USER_LINKS = _O.get("user_links") or []
USER_EMAIL = _O.get("user_email") or ""

FOLLOWUP_SYSTEM = """You write one short, polite, low-pressure follow-up to an unanswered email.
It must be 40-60 words, must not guilt the recipient, must not repeat the original pitch at
length, and must make clear this is the last time you'll write. No em-dashes. JSON only."""

FOLLOWUP_PROMPT = """The candidate {user_name} sent this {days} days ago to {contact_name} at {company},
and got no reply:

SUBJECT: {subject}
---
{body}
---

Write a brief follow-up that sits on the same thread. Add one small new piece of value or
context if you can do so truthfully from the original email; otherwise just be brief.

Reply with exactly:
{{"body": "<the follow-up body, plain text, no signature block>"}}"""


def signature() -> str:
    lines = [USER_NAME]
    if USER_HEADLINE:
        lines.append(USER_HEADLINE)
    if USER_LINKS:
        lines.append(" | ".join(USER_LINKS))
    if USER_EMAIL:
        lines.append(USER_EMAIL)
    return "\n".join(lines)


_EM_DASH = re.compile(r"\s*[—–]\s*")
_GREETING_DUP = re.compile(r"^(hi|hey|hello|dear)\b", re.I)


_HANDLE = re.compile(r"[\d_@]|^[a-z]+(?:-[a-z]+)+$")   # "kbysani-affirm", "jdoe_42": a login, not a name


def greeting_name(name: str | None, email: str | None = None) -> str:
    """The word after "Hi". A real name gives its first word; a GitHub handle or an
    empty name falls back to the address ("kailash.bysani@affirm.com" -> "Kailash"),
    and "there" when neither is usable. 2026-09-22: a draft opened "Hi kbysani"."""
    raw = (name or "").strip()
    if raw and " " in raw and not _HANDLE.search(raw.split()[0].lower()):
        return raw.split()[0]
    if raw and raw.isalpha() and not _HANDLE.search(raw.lower()) and len(raw) >= 3:
        return raw
    local = (email or "").split("@")[0].lower()
    for token in re.split(r"[._\-+]", local):
        if token.isalpha() and len(token) >= 3:
            return token.capitalize()
    return "there"


def _clean_body(body: str, contact_name: str | None) -> str:
    body = _EM_DASH.sub(", ", body or "").strip()
    # models like to append their own sign-off; we add a real one
    body = re.sub(
        r"\n+\s*(best|thanks|regards|cheers|sincerely|best regards|thank you)[,!.]?\s*\n.*$",
        "",
        body,
        flags=re.I | re.DOTALL,
    ).strip()
    if not _GREETING_DUP.match(body):
        first = (contact_name or "").split()[0] if contact_name else "there"
        body = f"Hi {first},\n\n{body}"
    return body


def _compose(body: str, contact_name: str | None) -> str:
    return f"{_clean_body(body, contact_name)}\n\nThanks,\n{signature()}"


DRAFT_EXPIRY_DAYS = int(_O.get("draft_expiry_days", 7))
CONTACT_COOLDOWN_DAYS = int(_O.get("contact_cooldown_days", 30))
COMPANY_COOLDOWN_DAYS = int(_O.get("company_cooldown_days", 30))


def _company_facts(company: Company | None, job: Job | None) -> tuple[str, dict]:
    """The company's own words the draft may use, and the same as structured evidence."""
    if company is None:
        return "(nothing known)", {}
    facts: dict = {}
    lines = []
    if company.description:
        facts["description"] = {"text": company.description, "source": company.website or "their site"}
        lines.append(f"- What they build: {company.description}")
    if company.story and (company.story_evidence or "").startswith("http"):
        snippet = company.story[:400]
        facts["origin"] = {"text": snippet, "source": company.story_evidence}
        lines.append(f"- How they started (from {company.story_evidence}): {snippet}")
    if company.funding_stage or company.funding_amount_usd_m:
        f = " ".join(p for p in [company.funding_stage, f"${company.funding_amount_usd_m:g}M" if company.funding_amount_usd_m else None] if p)
        facts["funding"] = {"text": f, "source": company.funding_evidence or "their pages"}
        lines.append(f"- Funding: {f}")
    if company.hiring_status in ("verified", "role_missing") and company.hiring_evidence:
        facts["hiring"] = {"text": company.hiring_evidence, "source": company.careers_url or company.hiring_claim_url}
        lines.append(f"- Hiring, on their own page: {company.hiring_evidence[:200]}")
    if company.hiring_claim_by and company.hiring_claim_source in ("x", "hn"):
        facts["hiring_post"] = {"text": f"{company.hiring_claim_by} posted that they are hiring", "source": company.hiring_claim_url}
        lines.append(f"- {company.hiring_claim_by} posted on {company.hiring_claim_source.upper()} that they are hiring")
    if job:
        where = job.location or ("remote" if job.remote else None)
        facts["role"] = {"text": job.title + (f" ({where})" if where else ""), "source": job.url}
        if job.remote_anywhere:
            facts["remote_anywhere"] = {"text": "the posting says the role can be done from anywhere", "source": job.url}
            lines.append("- The posting says the role can be done from anywhere")
    return ("\n".join(lines) or "(nothing beyond the name)"), facts


def needs_hook(raw: str | None) -> dict:
    """The covered needs, angle and forbidden skills from a role's stored needs brief
    (Job.needs_brief, written by needs.py). Read as JSON — the drafter never imports the
    analysis layer. Only COVERED (or partly covered) needs with a resume line are offered;
    uncovered ones become the forbidden list the gate enforces."""
    empty = {"block": "(no needs brief for this role — write from the facts above)", "angle": "(none)",
             "forbidden": [], "corpus": [], "evidence": None}
    if not raw:
        return empty
    try:
        brief = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return empty
    needs = [n for n in (brief.get("implied") or []) + (brief.get("stated") or [])
             if n.get("status") in ("covered", "partial") and n.get("evidence")]
    needs.sort(key=lambda n: (n.get("status") != "covered", -(n.get("weight") or 0), n.get("kind") != "implied"))
    lines, corpus, used = [], [], []
    for n in needs:
        if len(lines) >= 4:
            break
        what = ", ".join(n.get("evidence_skills") or []) or n.get("text") or ""
        lines.append(f"- They need {what}. Their words: \"{(n.get('quote') or '')[:180]}\" "
                     f"The candidate: \"{n['evidence'][:280]}\" ({n.get('evidence_where')})")
        corpus += [n.get("quote") or "", n["evidence"], n.get("evidence_where") or "", what]
        used.append({"need": n.get("text"), "status": n.get("status"), "quote": n.get("quote"), "url": n.get("url"),
                     "evidence": n["evidence"], "where": n.get("evidence_where")})
    angles = brief.get("angles") or []
    forbidden = [str(x) for x in (brief.get("forbidden") or [])]
    if not lines:
        return {**empty, "forbidden": forbidden}
    return {"block": "\n".join(lines), "angle": angles[0] if angles else "(none)", "forbidden": forbidden,
            "corpus": corpus + angles[:1],
            "evidence": {"covered": used, "angle": angles[0] if angles else None, "forbidden": forbidden,
                         "synthesis": brief.get("synthesis")}}


def _cooldown(session, contact_id: int, company_id: int, candidate_id: int) -> str | None:
    """MOTIV §6: never both of us to one person in a month; one ask per company per month."""
    from datetime import timedelta

    since = utcnow() - timedelta(days=CONTACT_COOLDOWN_DAYS)
    recent = session.exec(
        select(Email).where(Email.contact_id == contact_id, col(Email.status).in_(["approved", "sent", "replied"]),
                            col(Email.created_at) >= since)
    ).first()
    if recent:
        who = "you" if recent.candidate_id == candidate_id else "your teammate"
        return f"{who} already wrote to this person on {recent.created_at:%d %b} — {CONTACT_COOLDOWN_DAYS}-day cooldown"
    since_c = utcnow() - timedelta(days=COMPANY_COOLDOWN_DAYS)
    recent_c = session.exec(
        select(Email).where(Email.company_id == company_id, Email.candidate_id == candidate_id, Email.kind == "cold",
                            col(Email.status).in_(["approved", "sent", "replied"]), col(Email.created_at) >= since_c)
    ).first()
    if recent_c:
        return f"you already asked someone at this company on {recent_c.created_at:%d %b} — one ask per company per {COMPANY_COOLDOWN_DAYS} days"
    return None


POLISH_SYSTEM = """You polish a short cold email from a student engineer. Every fact in the draft is verified:
keep all of them and add none. No flattery, no buzzwords, no "I hope this finds you well", no "I'm reaching out",
no em-dashes. Short sentences (under 18 words), plain words, 60-110 words. Exactly one question: the ask.
Reply with JSON only."""

POLISH_PROMPT = """STRUCTURE: {label}. Why: {why}.
TO: {first}, {role} at {company}

THE DRAFT:
---
{message}
---

Make it read like one person writing to another. Keep, word for word: the greeting "Hi {first},", {keep}every
number, the role name "{role_title}", and the ask: "{ask}". {bullets}
Do not add a signature, a sign-off, a note to forward, links or a P.S.

Reply with exactly: {{"message": "<the polished message, plain text, starting with Hi {first},>"}}"""


def _polish_call(prompt: str) -> dict | None:
    from jobhunter import llm

    return llm.chat_json(prompt, POLISH_SYSTEM, temperature=0.4, num_predict=500, alias="writer",
                         purpose="draft", default=None)


# ------------------------------------------------------------------ the evidence for one email

_ENG_TITLE = re.compile(r"engineer|developer|\bsde\b|\bswe\b|scientist|machine learning|\bml\b|\bai\b|\bllm|data\b|"
                        r"backend|back-end|front.?end|full.?stack|software|platform|devops|\bsre\b|research|programmer|"
                        r"graduate|new grad|fresher|trainee", re.I)
_NOT_ENG_TITLE = re.compile(r"manager|director|head of|\bvp\b|sales|account|design|marketing|recruit|finance|legal|pilot|"
                            r"mechanical|manufacturing|hardware|firmware|field|operations|support|success|solutions? "
                            r"(?:engineer|consultant|architect)|technician|electrical|civil|fpga|\brf\b|embedded|analog|"
                            r"asic|chief|principal|staff\b|\blead\b|android|ios\b|telecom|chemical|process engineer|structural|"
                            r"construction|automotive|quality|\bqa\b|test engineer|network engineer|sales", re.I)


def pick_job(session, company_id: int) -> Job | None:
    """The role to ask about: an engineering title (not sales, design, hardware, management),
    fresher before senior, remote-from-anywhere first, then selection score, then match."""
    jobs = session.exec(select(Job).where(Job.company_id == company_id)).all()
    ok = [j for j in jobs if _ENG_TITLE.search(j.title or "") and not _NOT_ENG_TITLE.search(j.title or "")]
    ok.sort(key=lambda j: (bool(j.is_senior), not j.remote_anywhere, -(j.selection_score if j.selection_score is not None else -1),
                           -(j.match_score if j.match_score is not None else -1), j.id))
    return ok[0] if ok else None


def me() -> dict:
    """The sender, from the profile and config: name, school, year, resume link, the places
    that count as shared ground."""
    from jobhunter import resume

    p = resume.load_profile()
    edu = (p.get("education") or [{}])[0]
    school = (edu.get("institution") or "").split(",")[0].strip() or "my university"
    year = (re.findall(r"(20\d\d)\s*$", edu.get("years") or "") or [""])[0]
    full = p.get("name") if USER_NAME.startswith("(") else USER_NAME
    resume_link = next((l.split(":", 1)[1].strip() for l in USER_LINKS if l.lower().startswith("resume")), None)
    employers = [re.sub(r"\s*\(.*?\)|\s+(LLC|Inc\.?|Ltd\.?)$", "", e.get("company") or "").strip()
                 for e in p.get("experience") or []]
    rest = [x.strip() for x in (edu.get("institution") or "").split(",")[1:]]
    return {"full_name": full, "first_name": (full or "").split()[0], "school": school, "year": year,
            "resume_link": resume_link, "bond": f"CS+AI '{year[-2:]} grad" if year else "CS+AI grad",
            "shared_places": [school, *rest, *[e for e in employers if e]]}


def cold_signature() -> str:
    """Name, headline, LinkedIn. The resume link is in the forwardable note, so the mail
    carries two links in all (playbook: 1–2 links, a LinkedIn link in the signature)."""
    lines = [USER_NAME]
    if USER_HEADLINE:
        lines.append(USER_HEADLINE)
    lines += [l for l in USER_LINKS if "linkedin" in l.lower()][:1]
    return "\n".join(lines)


def _repos_for(contact: Contact, brief: dict, research: bool) -> list[dict]:
    """Their own public repos: cached on the contact, else read from GitHub (no model)."""
    if brief.get("repos"):
        return brief["repos"]
    if not (research and contact.github):
        return []
    import asyncio

    from jobhunter.outreach.researcher import github_work
    from jobhunter.scrapers.base import make_client

    async def _go():
        async with make_client() as http:
            return await github_work(http, contact.github)

    try:
        work = asyncio.run(_go()) or {}
    except Exception as e:  # noqa: BLE001 — a hook source failing is not a draft failing
        log.debug("github repos for %s: %s", contact.github, e)
        return []
    return work.get("repos") or []


def gather(contact_id: int, job_id: int | None = None, *, research: bool = True) -> dict:
    """Everything one email may use, read from the DB (and the person's public repos).
    {"error": ...} when no email may go to this person at all."""
    init_db()
    with get_session() as session:
        contact = session.get(Contact, contact_id)
        if contact is None or not contact.email:
            return {"error": "contact not found or has no email"}
        if not guardrails.is_ready(contact):
            return {"error": f"not a confirmed-current referrer ({contact.affiliation or 'unchecked'}: "
                             f"{contact.affiliation_evidence or 'no evidence on record'})"}
        company = session.get(Company, contact.company_id)
        if company is not None and (company.status == "below_pay_bar" or (
                not company.list_key and (company.overrated or company.status))):
            why = (company.status_reason if company.status == "below_pay_bar" else None) or \
                company.overrated_reason or company.status_reason or company.status or "overrated"
            return {"error": f"{company.name} is not a target ({why})"}
        if company is not None and not company.list_key and admission.body_shop(company.name, industry=company.description):
            return {"error": f"{company.name} is not a target (staffing / recruitment agency or body shop)"}
        job = session.get(Job, job_id) if job_id else None
        if job is None:
            job = pick_job(session, contact.company_id)
        notes: dict = {}
        if contact.research_notes:
            try:
                notes = json.loads(contact.research_notes)
                notes = notes if isinstance(notes, dict) else {}
            except json.JSONDecodeError:
                notes = {}
        repos = _repos_for(contact, notes, research)
        if repos and not notes.get("repos") and (not contact.research_notes or notes):
            notes["repos"] = repos            # cache only over empty or JSON notes, never over prose
            contact.research_notes = json.dumps(notes)[:4000]
            contact.researched_at = utcnow()
            session.add(contact)
            session.commit()
        brief = None
        if job is not None and job.needs_brief:
            try:
                brief = json.loads(job.needs_brief)
            except json.JSONDecodeError:
                brief = None
        person = {"id": contact.id, "name": contact.name, "role": contact.role, "role_class": contact.role_class,
                  "role_evidence": contact.role_evidence, "affiliation_evidence": contact.affiliation_evidence,
                  "x_handle": contact.x_handle, "github": contact.github, "linkedin": contact.linkedin,
                  "repos": repos, "email": contact.email, "confidence": contact.confidence,
                  "affiliation": contact.affiliation, "role_source": contact.linkedin or contact.github}
        comp = {"id": company.id if company else None, "name": company.name if company else None,
                "website": company.website if company else None, "description": company.description if company else None,
                "funding_stage": company.funding_stage if company else None,
                "funding_amount_usd_m": company.funding_amount_usd_m if company else None,
                "funding_evidence": company.funding_evidence if company else None,
                "team_size": company.team_size if company else None,
                "hiring_claim_by": company.hiring_claim_by if company else None,
                "hiring_claim_source": company.hiring_claim_source if company else None,
                "hiring_claim_url": company.hiring_claim_url if company else None,
                "hiring_evidence": company.hiring_evidence if company else None,
                "list_key": company.list_key if company else None, "story": company.story if company else None}
        jd = None
        if job is not None:
            jd = {"id": job.id, "title": job.title, "url": job.url, "location": job.location, "remote": job.remote,
                  "remote_anywhere": job.remote_anywhere, "market": job.selection_market,
                  "selection_score": job.selection_score, "is_senior": job.is_senior}
    return {"person": person, "company": comp, "job": jd, "brief": brief, "me": me(),
            "research_hook": notes.get("hook")}


def _corpus(g: dict, c) -> str:
    from jobhunter import resume

    prof = resume.load_profile()
    p, co, j = g["person"], g["company"], g["job"] or {}
    bits = [prof.get("_resume_text") or "", resume.profile_summary(max_chars=6000), USER_NAME, USER_HEADLINE,
            " ".join(USER_LINKS), p.get("name"), p.get("role"), p.get("role_evidence"), p.get("affiliation_evidence"),
            " ".join(f"{r.get('name')} {r.get('description') or ''}" for r in p.get("repos") or []),
            co.get("name"), co.get("description"), co.get("funding_stage"), co.get("funding_evidence"),
            f"{co['funding_amount_usd_m']:g}" if co.get("funding_amount_usd_m") else None,
            j.get("title"), g["me"].get("school"), g["me"].get("year")]
    for b in (g.get("brief") or {}).get("building") or []:
        bits += [b.get("text"), b.get("quote")]
    for n in c.proofs:
        bits += [n.get("quote"), n.get("resume_line"), n.get("what"), n.get("need")]
    return "\n".join(x for x in bits if x)


def write(g: dict, *, use_model: bool = True, key=None) -> dict:
    """One email from gathered evidence: the contract, the deterministic fill, the model's
    polish when it is available and keeps the contract, the gate and the quality score.
    Nothing is stored here. `incomplete` = a slot is missing and no email is written."""
    from jobhunter.outreach import compose, gates, suppress

    c = compose.contract(g["person"], g["company"], g["job"], g["brief"], g["me"])
    out: dict = {"contract": c.as_dict(), "structure": c.structure, "structure_why": c.structure_why,
                 "not_claimed": compose.not_claimed(g["brief"], c)}
    if not c.complete:
        out.update(incomplete=True, missing=c.missing, missing_labels=[compose.CONTRACT_LABEL[m] for m in c.missing])
        return out
    me_ = g["me"]
    filled = compose.fill(c, me_)
    subjects = compose.subject_options(c, me_)
    i = compose.pick(subjects, key if key is not None else g["person"]["id"])
    subject = subjects[i]["subject"] if i >= 0 else f"{c.target_role} at {c.company}"
    pattern = subjects[i]["pattern"] if i >= 0 else "role_plain"
    sig = cold_signature()
    corpus = _corpus(g, c)
    forbidden = [str(x) for x in ((g["brief"] or {}).get("forbidden") or [])]
    opt = suppress.OPT_OUT_LINE or None

    def evaluate(message: str) -> dict:
        body = compose.assemble(message, filled["blurb_intro"], filled["blurb"], sig, opt)
        v = gates.check(body, corpus=corpus, signature=sig, address_confidence=g["person"].get("confidence"),
                        affiliation=g["person"].get("affiliation"), forbidden=forbidden,
                        exclude=[filled["blurb_intro"], filled["blurb"], opt or ""], subject=subject)
        q = compose.quality(message, subject, c, v.flags, v.words, has_blurb=True, has_resume=bool(c.resume_link))
        return {"message": message, "body": body, "flags": v.flags, "words": v.words, "quality": q,
                "blocked": v.blocked}

    tpl = evaluate(filled["message"])
    chosen, writer, model_note, model_version = tpl, "template", None, None
    if use_model:
        keep = f"the words \"{c.hook['anchor']}\", " if c.hook.get("anchor") else ""
        prompt = POLISH_PROMPT.format(
            label=compose.STRUCTURES[c.structure]["label"], why=c.structure_why, first=c.first_name,
            role=c.recipient_role, company=c.company, message=filled["message"], keep=keep,
            role_title=c.target_role, ask=compose.ask_line(c),
            bullets="Keep the bulleted list of shipped work as a list." if c.structure == "proof_opinion" else "")
        try:
            data = _polish_call(prompt)
        except openrouter.BudgetExceeded as e:
            data, model_note = None, f"model ceiling: {str(e)[:120]}"
            out["budget"] = True
        except Exception as e:  # noqa: BLE001 — the template stands
            data, model_note = None, f"model error: {type(e).__name__}"
        msg = str((data or {}).get("message") or "").strip() if isinstance(data, dict) else ""
        if msg:
            msg = _EM_DASH.sub(", ", msg)
            msg = re.sub(r"\n+\s*(best|thanks|regards|cheers|sincerely|thank you)[,!.]?\s*(\n.*)?$", "", msg,
                         flags=re.I | re.DOTALL).strip()
            dropped = compose.keeps_contract(msg, c)
            if dropped:
                model_note = "model draft dropped: " + ", ".join(dropped)
            else:
                model_version = evaluate(msg)
                bad = {"faithfulness", "uncovered_claim", "ai_tell"} & {f["kind"] for f in model_version["flags"]}
                if bad:
                    model_note = "model draft flagged: " + ", ".join(sorted(bad))
                elif model_version["quality"]["score"] >= tpl["quality"]["score"]:
                    chosen, writer = model_version, "model"
                else:
                    model_note = "model draft scored lower than the template"
        elif model_note is None:
            model_note = "model gave nothing usable"
    out.update(subject=subject, subject_pattern=pattern, subjects=subjects, hook_type=c.hook["type"],
               writer=writer, polish_pending=bool(use_model and writer == "template" and out.get("budget")),
               model_note=model_note, template=tpl, model=model_version, **{k: chosen[k] for k in (
                   "message", "body", "flags", "words", "quality", "blocked")},
               blurb=filled["blurb"], blurb_intro=filled["blurb_intro"], signature=sig, opt_out=opt)
    return out


def draft_for(contact_id: int, job_id: int | None = None, *, research: bool = True,
              candidate_id: int = 1, force: bool = False, use_model: bool = True,
              record_incomplete: bool = True, mark_polish: bool = False) -> dict:
    """Create one draft email in the review queue (never approved): the personalisation
    contract filled from evidence, the structure for who they are, the gate's flags and
    the quality score stored with it. A missing slot writes no email — an "incomplete"
    row says what is missing instead (it can never be approved or sent)."""
    from datetime import timedelta

    init_db()
    with get_session() as session:
        dup = session.exec(select(Email).where(
            Email.contact_id == contact_id, Email.kind == "cold",
            col(Email.status).in_(["draft", "approved", "sending", "sent", "replied"]))).first()
        if dup:
            return {"error": "already drafted for this contact", "email_id": dup.id}
        contact = session.get(Contact, contact_id)
        if not force and contact is not None:
            why = _cooldown(session, contact_id, contact.company_id, candidate_id)
            if why:
                return {"error": why}

    g = gather(contact_id, job_id, research=research)
    if g.get("error"):
        return g
    r = write(g, use_model=use_model)
    p, co, j = g["person"], g["company"], g["job"]
    with get_session() as session:
        for old in session.exec(select(Email).where(Email.contact_id == contact_id, Email.status == "incomplete")).all():
            session.delete(old)
        if r.get("incomplete"):
            if not record_incomplete:
                session.commit()
                return {"incomplete": True, "missing": r["missing"], "missing_labels": r["missing_labels"]}
            row = Email(contact_id=contact_id, company_id=co["id"], job_id=j["id"] if j else None, to_email=p["email"],
                        subject="", body="", kind="cold", status="incomplete", candidate_id=candidate_id,
                        review_flags=json.dumps([{"kind": "incomplete", "detail": m} for m in r["missing_labels"]]),
                        contract=json.dumps(r["contract"], default=str), structure=r["structure"],
                        address_confidence=p.get("confidence"), expires_at=utcnow() + timedelta(days=DRAFT_EXPIRY_DAYS))
            session.add(row)
            session.commit()
            session.refresh(row)
            return {"incomplete": True, "email_id": row.id, "missing": r["missing"], "missing_labels": r["missing_labels"]}
        evidence = {"person": {"evidence": p.get("role_evidence"), "hook": r["contract"]["hook"],
                               "repos": [x.get("name") for x in p.get("repos") or []][:3]},
                    "company": {"name": co["name"], "description": co.get("description"),
                                "funding": co.get("funding_evidence")},
                    "role": {"title": j["title"], "url": j.get("url")} if j else None,
                    "needs": {"covered": r["contract"]["proofs"], "not_claimed": r["not_claimed"]}}
        email = Email(
            contact_id=contact_id, company_id=co["id"], job_id=j["id"] if j else None, to_email=p["email"],
            subject=r["subject"][:180], body=r["body"], kind="cold", status="draft",
            review_flags=json.dumps(r["flags"]), evidence=json.dumps(evidence, default=str),
            expires_at=utcnow() + timedelta(days=DRAFT_EXPIRY_DAYS), address_confidence=p.get("confidence"),
            candidate_id=candidate_id, structure=r["structure"], subject_pattern=r["subject_pattern"],
            hook_type=r["hook_type"], words=r["words"], quality=r["quality"]["score"],
            quality_detail=json.dumps(r["quality"]), writer=r["writer"], polish_pending=bool(r["polish_pending"] or (mark_polish and r["writer"] == "template")),
            contract=json.dumps(r["contract"], default=str),
        )
        session.add(email)
        session.commit()
        session.refresh(email)
        log.info("drafted email %s -> %s (%s) q=%s %s", email.id, p["email"], co["name"], r["quality"]["score"], r["writer"])
        out = {"email_id": email.id, "subject": email.subject, "to": email.to_email, "flags": r["flags"],
               "words": r["words"], "quality": r["quality"]["score"], "verdict": r["quality"]["verdict"],
               "structure": r["structure"], "writer": r["writer"]}
        if r.get("budget"):
            out["budget"] = True
        return out


def polish_pending(limit: int = 10) -> dict:
    """Re-write template drafts with the model once it is available again. Still drafts;
    the old one is replaced only when the model's version keeps the contract and scores
    at least as well."""
    init_db()
    with get_session() as session:
        rows = session.exec(select(Email).where(Email.status == "draft", Email.polish_pending == True)  # noqa: E712
                            .limit(limit)).all()
        ids = [(e.id, e.contact_id, e.job_id) for e in rows]
    out = {"tried": 0, "polished": 0, "stopped": None}
    for eid, cid, jid in ids:
        g = gather(cid, jid, research=False)
        if g.get("error"):
            continue
        r = write(g)
        out["tried"] += 1
        if r.get("budget"):
            out["stopped"] = "model ceiling"
            break
        if r.get("writer") == "model":
            with get_session() as session:
                e = session.get(Email, eid)
                if e and e.status == "draft":
                    e.body, e.review_flags, e.words = r["body"], json.dumps(r["flags"]), r["words"]
                    e.quality, e.quality_detail = r["quality"]["score"], json.dumps(r["quality"])
                    e.writer, e.polish_pending = "model", False
                    session.add(e)
                    session.commit()
                    out["polished"] += 1
    return out


def draft_for_bets(limit: int = 10, *, candidate_id: int = 1) -> list[dict]:
    """One draft per company that is ready to ask: hiring proven, a fresher role, remote from
    anywhere (or India), and a lead with a usable address — the best lead, the best role."""
    from jobhunter import network

    init_db()
    atlas = network.build()
    bets = [c for c in atlas["companies"] if c["bet"]]
    out: list[dict] = []
    with get_session() as session:
        for c in bets:
            if len(out) >= limit:
                break
            company = session.get(Company, c["id"])
            people = session.exec(select(Contact).where(Contact.company_id == c["id"])).all()
            leads = network._reachable(company, people)
            leads.sort(key=lambda p: ((p.referral_rank or 9), p.affiliation != "verified-current",
                                      p.confidence != "verified", p.name is None))
            if not leads:
                continue
            job = session.exec(
                select(Job).where(Job.company_id == c["id"])
                .order_by(col(Job.is_senior), col(Job.remote_anywhere).desc(), col(Job.match_score).desc())
            ).first()
            lead = leads[0]
            r = draft_for(lead.id, job.id if job else None, candidate_id=candidate_id)
            out.append({"company": company.name, "lead": lead.name, **r})
            if r.get("budget"):
                break   # every later draft would be refused the same way
    return out


def draft_followup(email_id: int) -> dict:
    """Queue a single polite follow-up on an unanswered thread."""
    init_db()
    from jobhunter import llm

    with get_session() as session:
        parent = session.get(Email, email_id)
        if parent is None or parent.status != "sent":
            return {"error": "parent email not found or not sent"}
        if parent.followup_sent:
            return {"error": "follow-up already queued for this thread"}
        contact = session.get(Contact, parent.contact_id)
        company = session.get(Company, parent.company_id)
        days = (utcnow() - (parent.sent_at or parent.created_at)).days
        contact_name = greeting_name(contact.name, contact.email) if contact else None
        payload = {
            "user_name": USER_NAME,
            "days": days,
            "contact_name": contact_name or "there",
            "company": company.name if company else "the company",
            "subject": parent.subject,
            "body": parent.body,
        }
        parent_to, parent_subject = parent.to_email, parent.subject
        contact_id, company_id, job_id = parent.contact_id, parent.company_id, parent.job_id
        thread_id = parent.gmail_thread_id

    data = llm.chat_json(
        FOLLOWUP_PROMPT.format(**payload), FOLLOWUP_SYSTEM, temperature=0.5, num_predict=300,
        alias="writer", purpose="followup", default=None,
    )
    if not isinstance(data, dict) or not data.get("body"):
        return {"error": "LLM produced no usable follow-up"}

    with get_session() as session:
        email = Email(
            contact_id=contact_id,
            company_id=company_id,
            job_id=job_id,
            to_email=parent_to,
            subject=parent_subject if parent_subject.lower().startswith("re:") else f"Re: {parent_subject}",
            body=_compose(str(data["body"]), contact_name),
            kind="followup",
            parent_email_id=email_id,
            status="draft",
            gmail_thread_id=thread_id,
        )
        session.add(email)
        parent = session.get(Email, email_id)
        parent.followup_sent = True   # mark now so we never queue a second one
        session.add(parent)
        session.commit()
        session.refresh(email)
        log.info("drafted follow-up %s for email %s", email.id, email_id)
        return {"email_id": email.id, "subject": email.subject}


def queue_followups(after_days: int | None = None, limit: int = 10) -> list[dict]:
    """Find sent emails that have gone quiet and queue one follow-up each."""
    init_db()
    from datetime import timedelta

    days = after_days if after_days is not None else int(_O.get("followup_after_days", 5))
    if int(_O.get("max_followups", 1)) < 1:
        return []
    cutoff = utcnow() - timedelta(days=days)

    with get_session() as session:
        candidates = session.exec(
            select(Email).where(
                Email.kind == "cold",
                Email.status == "sent",
                Email.followup_sent == False,  # noqa: E712
                col(Email.sent_at).is_not(None),
                col(Email.sent_at) < cutoff,
            ).limit(limit)
        ).all()
        ids = [e.id for e in candidates]

    return [draft_followup(eid) for eid in ids]


def draft_for_high_matches(limit: int = 5, *, per_company: int = 2) -> list[dict]:
    """Draft referral asks to the best contacts at companies with high-match jobs."""
    init_db()
    out: list[dict] = []
    with get_session() as session:
        company_ids = session.exec(
            select(Job.company_id).where(Job.status == "high_match", col(Job.company_id).is_not(None)).distinct()
        ).all()

        targets: list[tuple[int, int]] = []
        for cid in company_ids:
            contacts = session.exec(
                select(Contact).where(Contact.company_id == cid, col(Contact.email).is_not(None))
            ).all()
            # confirmed-current referrers only; verified addresses first, then named
            contacts = [c for c in contacts if guardrails.is_ready(c)]
            contacts.sort(key=lambda c: (c.affiliation != "verified-current", c.confidence != "verified", c.name is None))
            already = {
                e.contact_id for e in session.exec(select(Email).where(Email.company_id == cid)).all()
            }
            picked = [c for c in contacts if c.id not in already][:per_company]
            best_job = session.exec(
                select(Job).where(Job.company_id == cid, Job.status == "high_match")
                .order_by(col(Job.match_score).desc())
            ).first()
            for c in picked:
                targets.append((c.id, best_job.id if best_job else None))

    for contact_id, job_id in targets[:limit]:
        r = draft_for(contact_id, job_id)
        out.append(r)
        if r.get("budget"):
            break   # every later draft would be refused the same way
    return out
