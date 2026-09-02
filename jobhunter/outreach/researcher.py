"""Per-person research — gather the concrete detail that makes a cold email land.

A referral ask works when it proves you looked at the person's actual work. This
pulls their top public repos and the company's own description of the role, then
has the LLM distil one specific, honest hook.
"""

from __future__ import annotations

import logging

import httpx

from jobhunter.contacts.github_miner import headers
from jobhunter.db import Company, Contact, Job, get_session, utcnow
from jobhunter.scrapers.base import get_json, make_client

log = logging.getLogger(__name__)

API = "https://api.github.com"


async def github_work(http: httpx.AsyncClient, github_url: str | None) -> dict:
    """Top public repos for a person, most-starred first."""
    if not github_url:
        return {}
    login = github_url.rstrip("/").split("/")[-1]
    repos = await get_json(
        http, f"{API}/users/{login}/repos?sort=pushed&per_page=20&type=owner", headers=headers()
    )
    if not repos:
        return {}
    ranked = sorted(
        (r for r in repos if not r.get("fork")),
        key=lambda r: r.get("stargazers_count", 0),
        reverse=True,
    )[:5]
    return {
        "login": login,
        "repos": [
            {
                "name": r.get("name"),
                "description": (r.get("description") or "")[:200],
                "language": r.get("language"),
                "stars": r.get("stargazers_count", 0),
                "url": r.get("html_url"),
            }
            for r in ranked
        ],
    }


HOOK_SYSTEM = """You find one specific, truthful detail from someone's public work that a
cold email can honestly open with. You never invent projects, numbers, or claims. If the
material is too thin to say anything specific, you say so rather than inventing a hook."""

HOOK_PROMPT = """PERSON
Name: {name}
Role/bio: {role}
Public repositories:
{repos}

COMPANY: {company}
ROLE THEY'RE HIRING FOR: {job_title}
What the job posting emphasises: {job_focus}

THE CANDIDATE (who will write to them):
{candidate}

Produce a research brief. Rules:
- `hook` must reference something REAL from the repos or bio above. If the repos list is
  empty or generic, set hook to null — do not fabricate.
- `shared_ground` must connect a genuine item in the candidate's background to this person's
  work or this company's problem. No flattery, no invented overlap.

Reply with exactly:
{{
  "hook": "<one sentence about their actual work, or null>",
  "shared_ground": "<one sentence linking the candidate's real experience to it>",
  "angle": "<what this email should ask for: 'referral' | 'advice' | 'intro'>",
  "notes": "<2-3 sentences of context worth remembering about this person>"
}}"""


def _fmt_repos(work: dict) -> str:
    repos = work.get("repos") or []
    if not repos:
        return "(no public repositories found)"
    return "\n".join(
        f"  - {r['name']} ({r['language'] or 'n/a'}, {r['stars']}*): {r['description'] or 'no description'}"
        for r in repos
    )


async def research_contact(contact_id: int, job_id: int | None = None) -> dict:
    """Gather context on one contact and cache it on the Contact row."""
    from jobhunter import llm, resume

    with get_session() as session:
        contact = session.get(Contact, contact_id)
        if contact is None:
            return {"error": f"no contact {contact_id}"}
        company = session.get(Company, contact.company_id)
        job = session.get(Job, job_id) if job_id else None
        name, role, github = contact.name, contact.role, contact.github
        company_name = company.name if company else "the company"
        job_title = job.title if job else "engineering roles"
        job_focus = ((job.description or "")[:700] if job else "") or "not available"

    async with make_client() as http:
        work = await github_work(http, github)

    brief = llm.chat_json(
        HOOK_PROMPT.format(
            name=name or "(unknown)",
            role=role or "(unknown)",
            repos=_fmt_repos(work),
            company=company_name,
            job_title=job_title,
            job_focus=job_focus,
            candidate=resume.profile_summary(max_chars=1200),
        ),
        HOOK_SYSTEM,
        temperature=0.3,
        num_predict=500,
        alias="judge",          # the hook has to be true, not just fluent
        purpose="contact-research",
        default={},
    )
    if not isinstance(brief, dict):
        brief = {}
    brief["repos"] = work.get("repos") or []

    import json

    with get_session() as session:
        contact = session.get(Contact, contact_id)
        if contact:
            contact.research_notes = json.dumps(brief)[:4000]
            contact.researched_at = utcnow()
            session.add(contact)
            session.commit()

    log.info("researched contact %s (%s): hook=%s", contact_id, name, bool(brief.get("hook")))
    return brief
