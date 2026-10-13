"""Contact discovery orchestration.

Order matters — each step is cheaper or more reliable than the next:
  1. GitHub commit metadata  -> real, verified engineer emails (free, unlimited-ish)
  2. Company website         -> recruiter aliases and public team emails (free)
  3. Pattern inference       -> apply a pattern learned in 1/2 to every other name (free)
  4. Hunter.io              -> only when 1-3 left us without a pattern (25/month)
  5. The open web           -> any "@their-domain" address a search can find — HR, a
                               founder, an engineer — when 1-4 left no pattern (free)
  6. Fill by pattern         -> every named person still without an address gets one
                               from the company's pattern, MX-checked, labelled guessed
Everything is stored with its source and confidence so the dashboard can rank.
"""

from __future__ import annotations

import asyncio
import logging

from sqlmodel import col, select

from jobhunter import CONFIG
from jobhunter.contacts import github_miner, hunter, patterns, roles, site_scraper, verify
from jobhunter.db import Company, Contact, Job, get_session, init_db, utcnow
from jobhunter.scrapers.base import make_client

log = logging.getLogger(__name__)

MAX_PER_COMPANY = int((CONFIG.get("contacts") or {}).get("max_per_company", 20))

_RECRUITER_HINT = ("recruit", "talent", "people ops", "hr", "hiring", "staffing")


def _looks_recruiter(text: str | None) -> bool:
    return bool(text) and any(h in text.lower() for h in _RECRUITER_HINT)


async def discover_for_company(company_id: int, *, max_contacts: int = MAX_PER_COMPANY, github: bool = True) -> dict:
    """Run every free source for one company and persist the results. `github=False`
    (the hourly GitHub budget is spent) skips the mining and leaves the company un-rested
    (contacts_found_at untouched) so the next pass mines it."""
    init_db()
    with get_session() as session:
        company = session.get(Company, company_id)
        if company is None:
            return {"error": f"no company {company_id}"}
        name, website, domain = company.name, company.website, company.domain
        # only a pattern the company's own addresses confirm is ever rendered (addresses.py)
        github_org, known_pattern = company.github_org, patterns.confirmed(company)
        held = session.exec(select(Contact).where(Contact.company_id == company_id)).all()
        existing = {c.email.lower() for c in held if c.email}
        # an address that bounced is never stored again (outreach/suppress.py)
        existing |= {c.dead_email.lower() for c in held if getattr(c, "dead_email", None)}
        # a person found without an address is kept by their GitHub login (dedup)
        existing_logins = {(c.handle or "").lower() for c in held if c.handle} | {
            (c.github or "").rstrip("/").split("/")[-1].lower() for c in held if c.github}

    stats = {"company": name, "github": 0, "site": 0, "pattern": 0, "hunter": 0, "verified": 0, "added": 0}
    people: list[dict] = []

    async with make_client() as http:
        # ---- 1. GitHub
        org, gh_people = (await github_miner.mine(http, name, website, org=github_org, limit=max_contacts)
                          if github else (github_org, []))
        for p in gh_people:
            people.append(
                {
                    "name": p.name,
                    "email": p.email,
                    "role": (p.bio or "")[:120] or None,
                    "github": f"https://github.com/{p.login}",
                    "x_handle": p.twitter,
                    "source": "github",
                    # the address tier is settled by the guardrails from the commit date
                    "confidence": "scraped",
                    "is_recruiter": _looks_recruiter(p.bio),
                    "employer_claim": (p.company or "").strip() or None,
                    # affiliation evidence: membership, the commit's date and repo
                    "handle": p.login, "org_member": p.member, "last_commit_at": p.last_commit_at,
                    "commit_repo": p.commit_repo, "profile_email": p.profile_email,
                    "name_source": "github-profile" if p.bio is not None or p.company is not None else "commit",
                }
            )
        stats["github"] = len(gh_people)

        # ---- 2. company website
        if website:
            for c in await site_scraper.scrape(http, website, domain):
                people.append(
                    {
                        "name": c.name,
                        "email": c.email,
                        "role": c.role,
                        "source": "site",
                        "confidence": "scraped",
                        "is_recruiter": c.is_recruiter,
                        # named next to an address on their own site: the company says so
                        "team_page": website if c.name else None,
                        "published_at": website, "name_source": "page" if c.name else None,
                    }
                )
                stats["site"] += 1

        # ---- 3. the pattern: only one already confirmed by ≥2 of their addresses (or one +
        # their own page). This run's addresses are voted on after they are stored (step 7).
        domain = domain or (website or "").split("//")[-1].split("/")[0].removeprefix("www.") or None
        pattern = known_pattern

        # ---- 4. Hunter, only if we still have no pattern and there are names to apply it to
        nameless = [p for p in people if p["name"] and not p["email"]]
        if not pattern and domain and nameless and hunter.available():
            result = await hunter.domain_search(http, domain)
            if result:
                # Hunter's own pattern is a claim, not evidence: its addresses are stored
                # and voted on with the rest (addresses.learn), the claim is not used
                for e in result.emails:
                    full = " ".join(x for x in (e.get("first_name"), e.get("last_name")) if x) or None
                    people.append(
                        {
                            "name": full,
                            "email": e["email"],
                            "role": e.get("position"),
                            "source": "hunter",
                            "confidence": "verified" if (e.get("confidence") or 0) >= 90 else "scraped",
                            "is_recruiter": _looks_recruiter(e.get("position")),
                        }
                    )
                    stats["hunter"] += 1

        # ---- 5. fill the gaps by a CONFIRMED pattern, MX-checked (no SMTP probing)
        if domain and pattern:
            for p in nameless:
                cands = patterns.candidates(p["name"], domain, pattern)
                if not cands:
                    continue
                email, verdict = verify.best_candidate(cands)
                if email:
                    p["email"] = email
                    p["confidence"] = verdict.confidence if verdict else "pattern-guessed"
                    p["smtp"] = p["confidence"]
                    p["source"] = "pattern"
                    p["verify_note"] = verdict.reason if verdict else ""
                    stats["pattern"] += 1

    # ---- persist, every row through the guardrails (name, address, affiliation)
    from jobhunter import guardrails

    with get_session() as session:
        company_row = session.get(Company, company_id)
        if domain and company_row is not None and not company_row.domain:
            company_row.domain = domain
        for p in people:
            email = (p.get("email") or "").lower().strip()
            login = (p.get("handle") or "").lower()
            if not email:
                # a GitHub person with no address is still a referrer when the evidence
                # places them there now (a public org member, a commit inside 12 months)
                # and they have a real name — the confirmed pattern can address them later
                if not (p["source"] == "github" and login and login not in existing_logins and p.get("name")
                        and (p.get("org_member") or p.get("last_commit_at"))):
                    continue
            elif email in existing:
                continue
            if email:
                existing.add(email)
            if login:
                existing_logins.add(login)
            g = guardrails.vet_contact(
                session, company=company_row, name=p.get("name"), email_addr=email, source=p["source"],
                channel="github" if p["source"] == "github" else p["source"], url=p.get("github") or website,
                handle=p.get("handle"), name_source=p.get("name_source"), org_member=bool(p.get("org_member")),
                employer_claim=p.get("employer_claim"), last_commit_at=p.get("last_commit_at"),
                commit_repo=p.get("commit_repo"), team_page=p.get("team_page"), published_at=p.get("published_at"),
                smtp=p.get("smtp") or (p["confidence"] if p["source"] == "hunter" else None),
                profile_email=bool(p.get("profile_email")))
            if g is None or not (g.get("email") or (g.get("name") and g["affiliation"] in guardrails.READY)):
                continue
            if g["confidence"] == "verified":
                stats["verified"] += 1
            stats[g["affiliation"]] = stats.get(g["affiliation"], 0) + 1
            # label them on the way in: an unranked contact list is not a queue
            verdict = roles.classify(p.get("role"), name=g.get("name"), email=g.get("email"),
                                     bio=p.get("verify_note"))
            if verdict.role_class == "other" and p["source"] == "github":
                # No bio, but they commit to the company's public repos: that is an
                # engineer, and the evidence is the commit history, not a guess.
                verdict = roles.RoleVerdict("engineer", verdict.seniority,
                                            roles.RANKS.get("engineer", roles.DEFAULT_RANK),
                                            "commits in the company's public repos", "source")
            if g.get("email_kind") == "generic" and g.get("email"):
                verdict = roles.RoleVerdict("other", None, 9, f"shared inbox {g['email'].split('@')[0]}@", "alias")
            stats[verdict.role_class] = stats.get(verdict.role_class, 0) + 1
            session.add(
                Contact(
                    company_id=company_id,
                    name=g.get("name"),
                    role=p.get("role"),
                    email=g.get("email"),
                    github=p.get("github"),
                    x_handle=p.get("x_handle"),
                    source=p["source"],
                    confidence=g["confidence"],
                    is_recruiter=verdict.role_class in ("recruiter", "tech_recruiter") or bool(p.get("is_recruiter")),
                    role_class=verdict.role_class,
                    seniority=verdict.seniority,
                    referral_rank=verdict.referral_rank,
                    role_evidence=verdict.evidence,
                    classified_at=utcnow(),
                    research_notes=p.get("verify_note"),
                    employer_claim=p.get("employer_claim"),
                    handle=g.get("handle"), name_source=g.get("name_source"), email_kind=g.get("email_kind"),
                    affiliation=g["affiliation"], affiliation_source=g["affiliation_source"],
                    affiliation_evidence=g["affiliation_evidence"], affiliation_checked_at=g["affiliation_checked_at"],
                    last_commit_at=g.get("last_commit_at"),
                )
            )
            stats["added"] += 1

        company = session.get(Company, company_id)
        if company:
            company.github_org = company.github_org or org
            company.domain = company.domain or domain
            if github:
                company.contacts_found_at = utcnow()
            session.add(company)
        session.commit()

    # ---- 6. no pattern and nobody on their domain? ask the open web (contacts/web_emails.py)
    from jobhunter.contacts import web_emails

    with get_session() as session:
        company = session.get(Company, company_id)
        have_pattern = bool(company and company.email_pattern)
        on_domain = bool(company and company.domain and session.exec(
            select(Contact.id).where(Contact.company_id == company_id,
                                     col(Contact.email).like(f"%@{company.domain}"))).first())
    if not have_pattern and not on_domain:
        try:
            w = web_emails.find(company_id)
            stats["web"] = w.get("addresses", 0)
            stats["web_people"] = w.get("people", 0) + w.get("attached", 0)
        except Exception as e:  # noqa: BLE001 — the open web is a bonus, never a blocker
            log.warning("contacts: web_emails for %s failed: %s", name, e)

    # ---- 7. everyone named and still without an address gets one from the pattern
    stats["by_pattern"] = fill_by_pattern(company_id)

    log.info("contacts: %s", stats)
    return stats


def fill_by_pattern(company_id: int) -> int:
    """Learn the company's address format from its own addresses and give every ready,
    named referrer without an address the confirmed pattern's address (addresses.fill):
    ≥2 agreeing examples or one + their own page, MX-checked, labelled pattern-guessed.
    Returns how many addresses were added."""
    from jobhunter.contacts import addresses

    try:
        return int(addresses.fill(company_id).get("added", 0))
    except Exception as e:  # noqa: BLE001 — an address pass must never fail discovery
        log.warning("contacts: pattern fill for %s failed: %s", company_id, e)
        return 0


def discover_for_high_matches(limit: int = 5, *, refresh_days: int = 30,
                              tiers: tuple[str, ...] = ("tier1", "tier2", "prospect")) -> list[dict]:
    """Find contacts at target companies that have a high-match job and no recent run.

    `tiers` is the company grade from jobhunter/targeting.py: a company we have already
    decided not to chase does not get a contact hunt. An ungraded company (tier None)
    still qualifies — grading may simply not have run yet — but a `reject` never does.
    """
    init_db()
    from datetime import timedelta

    from jobhunter.targeting import TIER_ORDER

    cutoff = utcnow() - timedelta(days=refresh_days)
    with get_session() as session:
        rows = session.exec(
            select(Job.company_id)
            .where(Job.status == "high_match", col(Job.company_id).is_not(None))
            .distinct()
        ).all()
        candidates = []
        for cid in rows:
            company = session.get(Company, cid)
            if company is None:
                continue
            if company.tier and company.tier not in tiers:
                continue
            if company.contacts_found_at is not None and company.contacts_found_at >= cutoff:
                continue
            candidates.append((TIER_ORDER.get(company.tier or "unknown", 9), -(company.ppo_lpa or 0), cid))
        candidates.sort()
        company_ids = [cid for _, _, cid in candidates][:limit]

    return [asyncio.run(discover_for_company(cid)) for cid in company_ids]


def discover(company_id: int) -> dict:
    return asyncio.run(discover_for_company(company_id))
