"""Contact discovery orchestration.

Order matters — each step is cheaper or more reliable than the next:
  1. GitHub commit metadata  -> real, verified engineer emails (free, unlimited-ish)
  2. Company website         -> recruiter aliases and public team emails (free)
  3. Pattern inference       -> apply a pattern learned in 1/2 to every other name (free)
  4. Hunter.io              -> only when 1-3 left us without a pattern (25/month)
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


async def discover_for_company(company_id: int, *, max_contacts: int = MAX_PER_COMPANY) -> dict:
    """Run every free source for one company and persist the results."""
    init_db()
    with get_session() as session:
        company = session.get(Company, company_id)
        if company is None:
            return {"error": f"no company {company_id}"}
        name, website, domain = company.name, company.website, company.domain
        github_org, known_pattern = company.github_org, company.email_pattern
        existing = {
            c.email.lower() for c in session.exec(
                select(Contact).where(Contact.company_id == company_id, col(Contact.email).is_not(None))
            ).all()
        }

    stats = {"company": name, "github": 0, "site": 0, "pattern": 0, "hunter": 0, "verified": 0, "added": 0}
    people: list[dict] = []

    async with make_client() as http:
        # ---- 1. GitHub
        org, gh_people = await github_miner.mine(http, name, website, org=github_org, limit=max_contacts)
        for p in gh_people:
            people.append(
                {
                    "name": p.name,
                    "email": p.email,
                    "role": (p.bio or "")[:120] or None,
                    "github": f"https://github.com/{p.login}",
                    "source": "github",
                    "confidence": "verified" if p.email else "scraped",
                    "is_recruiter": _looks_recruiter(p.bio),
                    "employer_claim": (p.company or "").strip() or None,
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
                    }
                )
                stats["site"] += 1

        # ---- 3. learn the pattern from whatever real addresses we now hold
        domain = domain or (website or "").split("//")[-1].split("/")[0].removeprefix("www.") or None
        pattern = known_pattern or (patterns.learn_from_contacts(people, domain) if domain else None)

        # ---- 4. Hunter, only if we still have no pattern and there are names to apply it to
        nameless = [p for p in people if p["name"] and not p["email"]]
        if not pattern and domain and nameless and hunter.available():
            result = await hunter.domain_search(http, domain)
            if result:
                pattern = result.pattern
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

        # ---- 5. fill the gaps by pattern, verifying each guess
        if domain:
            for p in nameless:
                cands = patterns.candidates(p["name"], domain, pattern)
                if not cands:
                    continue
                email, verdict = verify.best_candidate(cands)
                if email:
                    p["email"] = email
                    p["confidence"] = verdict.confidence if verdict else "pattern-guessed"
                    p["source"] = "pattern"
                    p["verify_note"] = verdict.reason if verdict else ""
                    stats["pattern"] += 1

    # ---- persist
    with get_session() as session:
        for p in people:
            email = (p.get("email") or "").lower().strip()
            if not email or email in existing:
                continue
            existing.add(email)
            if p["confidence"] == "verified":
                stats["verified"] += 1
            # label them on the way in: an unranked contact list is not a queue
            verdict = roles.classify(p.get("role"), name=p.get("name"), email=email,
                                     bio=p.get("verify_note"))
            if verdict.role_class == "other" and p["source"] == "github":
                # No bio, but they commit to the company's public repos: that is an
                # engineer, and the evidence is the commit history, not a guess.
                verdict = roles.RoleVerdict("engineer", verdict.seniority,
                                            roles.RANKS.get("engineer", roles.DEFAULT_RANK),
                                            "commits in the company's public repos", "source")
            stats[verdict.role_class] = stats.get(verdict.role_class, 0) + 1
            session.add(
                Contact(
                    company_id=company_id,
                    name=p.get("name"),
                    role=p.get("role"),
                    email=email,
                    github=p.get("github"),
                    source=p["source"],
                    confidence=p["confidence"],
                    is_recruiter=verdict.role_class in ("recruiter", "tech_recruiter") or bool(p.get("is_recruiter")),
                    role_class=verdict.role_class,
                    seniority=verdict.seniority,
                    referral_rank=verdict.referral_rank,
                    role_evidence=verdict.evidence,
                    classified_at=utcnow(),
                    research_notes=p.get("verify_note"),
                    employer_claim=p.get("employer_claim"),
                )
            )
            stats["added"] += 1

        company = session.get(Company, company_id)
        if company:
            company.github_org = company.github_org or org
            company.email_pattern = company.email_pattern or pattern
            company.domain = company.domain or domain
            company.contacts_found_at = utcnow()
            session.add(company)
        session.commit()

    log.info("contacts: %s", stats)
    return stats


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
