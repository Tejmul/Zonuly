"""Is the hiring claim real? — corroborating a hiring post against the company itself.

A founder posts "we're hiring engineers!" on X every single day. An aggregator lists a
role that was filled two months ago. Neither is evidence, and drafting a referral email
against a role that does not exist is how you burn a contact you only get to use once.

So a claim is only a claim until the company's *own* surface confirms it:

    1. their ATS board (Greenhouse / Lever / Ashby) — the strongest signal, because a
       live board with open postings is the company's own machine-readable statement
    2. their careers page on their own domain — read it, look for hiring language and
       role titles that are actually there

The verdict, stored on `company.hiring_status`:

    verified        their own board or careers page lists open roles right now
    role_missing    they are hiring, but not the role the claim named
    not_authorized  no corroboration anywhere they control — the claim stands alone
    unreachable     we could not reach them; OUR failure, not theirs, so it is never
                    treated as a negative
    unchecked       never run

`not_authorized` is the label Tejmul asked for: the post exists, the company's own
pages do not back it, so nothing is drafted against it.

Layering: scrapers + research (acquisition) below, pipeline/api above. No model call —
this is a fetch and a regex, and it stays free.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import timedelta
from urllib.parse import urlparse

from sqlmodel import select

from jobhunter import CONFIG
from jobhunter.db import Company, Job, get_session, init_db, utcnow
from jobhunter.research import agent as research_agent
from jobhunter.research import web
from jobhunter.scrapers import discover as ats_discover
from jobhunter.scrapers.base import make_client

log = logging.getLogger(__name__)

_HV = ((CONFIG.get("targeting") or {}).get("hiring_verification") or {})
CAREERS_PATHS: list[str] = _HV.get("careers_paths") or ["/careers", "/jobs", "/join-us"]
MAX_AGE_DAYS = int(_HV.get("max_age_days", 21))
TIMEOUT = int(_HV.get("timeout", 20))

# The strongest possible disconfirmation: the company saying so in its own words.
_SAYS_NO_OPENINGS = re.compile(
    r"\b(no (?:current(?:ly)? )?open (?:positions|roles|jobs)|do not (?:currently )?have any open|"
    r"don'?t (?:currently )?have any open|no open(?:ings)? at (?:this|the) (?:time|moment)|"
    r"not (?:currently )?hiring|no vacancies|positions? (?:are )?closed)\b",
    re.I,
)


def _best_signal(signals: list[str], roles: list[str]) -> str:
    """The tersest true sentence. A page with no full stops yields one 300-char blob;
    a listed role title says the same thing in five words and is worth more."""
    short = [s for s in signals if len(s) <= 180]
    if short:
        return min(short, key=len)[:300]
    if roles:
        return f"open roles listed: {', '.join(roles[:5])}"[:300]
    return (signals[0][:300] if signals else "")


def _quote_around(text: str, pattern: re.Pattern[str]) -> str:
    """The company's own sentence, so the verdict can always be shown, never asserted."""
    m = pattern.search(text)
    if not m:
        return ""
    start = max(0, text.rfind(".", 0, m.start()) + 1)
    end = text.find(".", m.end())
    return re.sub(r"\s+", " ", text[start : end + 1 if end != -1 else m.end() + 120]).strip()[:300]


VERIFIED, ROLE_MISSING, NOT_AUTHORIZED, UNREACHABLE, UNCHECKED = (
    "verified", "role_missing", "not_authorized", "unreachable", "unchecked",
)


@dataclass
class Verification:
    status: str = UNCHECKED
    evidence: str | None = None          # what their own page actually said
    careers_url: str | None = None
    roles: list[str] = field(default_factory=list)
    open_count: int | None = None
    checked: list[str] = field(default_factory=list)   # every URL we actually looked at

    def as_dict(self) -> dict:
        return {
            "status": self.status, "evidence": self.evidence, "careers_url": self.careers_url,
            "roles": self.roles, "open_count": self.open_count, "checked": self.checked,
        }


# A company.website scraped off an aggregator is often not the company's site at all:
# HN and friends leave behind ATS hosts, Greenhouse shortlinks and job-board domains.
# Reading one of those and concluding "they are not hiring" is a false accusation — the
# page we read was never theirs. So these hosts can never produce `not_authorized`.
_ATS_HOST = re.compile(
    r"(^|\.)(greenhouse\.io|grnh\.se|lever\.co|ashbyhq\.com|dover\.com|workable\.com|"
    r"breezy\.hr|recruitee\.com|teamtailor\.com|bamboohr\.com|smartrecruiters\.com|"
    r"jobvite\.com|icims\.com|myworkdayjobs\.com|workday\.com|rippling\.com|"
    r"paylocity\.com|jazz\.co|applytojob\.com|pinpointhq\.com)$", re.I)
_AGGREGATOR_HOST = re.compile(
    r"(^|\.)(devitjobs\.uk|remoteok\.com|remoteok\.io|weworkremotely\.com|ycombinator\.com|"
    r"linkedin\.com|indeed\.com|glassdoor\.com|wellfound\.com|angel\.co|otta\.com|"
    r"builtin\.com|dice\.com|monster\.com|naukri\.com|instahyre\.com|cutshort\.io|"
    r"bit\.ly|tinyurl\.com|t\.co)$", re.I)


def _site_problem(website: str | None) -> str | None:
    """Why this URL cannot answer "are they hiring?", or None if it can."""
    if not website:
        return "no website on file"
    host = (urlparse(website if "://" in website else "https://" + website).hostname or "").lower()
    if not host:
        return f"unusable website on file ({website})"
    if _ATS_HOST.search(host):
        return f"the website on file ({host}) is an applicant-tracking host, not their own site"
    if _AGGREGATOR_HOST.search(host):
        return f"the website on file ({host}) is a job aggregator, not their own site"
    return None


def _domain_of(website: str | None) -> str | None:
    if not website:
        return None
    host = urlparse(website if "://" in website else "https://" + website).hostname
    return (host or "").removeprefix("www.") or None


# ------------------------------------------------------------------ the two probes

# Every board names the title differently; this is the whole difference between them.
_TITLE_KEYS = ("title", "text", "name")


async def _board_titles(http, ats: str, slug: str, limit: int = 40) -> list[str]:
    """The role titles actually listed on a live board — what a claim is checked against."""
    from jobhunter.scrapers.base import get_json

    data = await get_json(http, ats_discover.PROBES[ats].format(slug=slug))
    if data is None:
        return []
    jobs = data if isinstance(data, list) else (data.get("jobs") or [])
    titles: list[str] = []
    for job in jobs:
        if not isinstance(job, dict):
            continue
        for key in _TITLE_KEYS:
            value = job.get(key)
            if isinstance(value, str) and value.strip():
                titles.append(value.strip())
                break
        if len(titles) >= limit:
            break
    return titles


async def _check_ats(name: str, website: str | None, ats: str | None, slug: str | None) -> Verification | None:
    """Their own ATS board. A live board with postings is the company saying it itself."""
    v = Verification()
    async with make_client() as http:
        hit = None
        if ats and slug:
            hit = await ats_discover.probe(http, ats, slug) if ats in ats_discover.PROBES else None
            v.checked.append(ats_discover.PROBES.get(ats, "{slug}").format(slug=slug))
        if hit is None:
            hit = await ats_discover.find_ats(http, name, website)
        if hit is None:
            return None
        found_ats, found_slug, count = hit
        v.checked.append(ats_discover.PROBES[found_ats].format(slug=found_slug))
        # pull the titles too: "they are hiring" and "they are hiring THIS" are
        # different claims, and only the titles can answer the second one
        v.roles = await _board_titles(http, found_ats, found_slug)
    v.status = VERIFIED
    v.open_count = count
    v.careers_url = ats_discover.PROBES[found_ats].format(slug=found_slug)
    v.evidence = f"{found_ats} board '{found_slug}' is live with {count} open posting(s)"
    return v


def _check_careers(website: str | None, *, fresh: bool = False) -> Verification:
    """Their careers page on their own domain, read and searched for real hiring language."""
    v = Verification()
    problem = _site_problem(website)
    if problem:
        # We could not look at anything they control, so we have not disproved anything.
        # `unreachable` is our failure to check; `not_authorized` is their page saying no.
        v.status = UNREACHABLE
        v.evidence = f"{problem} — nothing they control was read, so the claim is untested"
        return v

    # `website` is not always a root — a scraper may have stored the deep link the
    # posting was found at. Appending "/careers" to that yields nonsense, so the paths
    # are built from the origin, and the given URL is tried first in case it already
    # IS the careers page.
    parsed = urlparse(website if "://" in website else "https://" + website)
    base = f"{parsed.scheme or 'https'}://{parsed.hostname}"
    candidates = ([website.rstrip("/")] if (parsed.path or "").strip("/") else []) + [
        base + path for path in CAREERS_PATHS
    ]

    reached_any = False
    for url in candidates:
        v.checked.append(url)
        page = web.read(url, fresh=fresh, timeout=TIMEOUT, prefer="direct")
        text = page.get("text") or ""
        if not text:
            continue
        reached_any = True
        if _SAYS_NO_OPENINGS.search(text):
            v.status = NOT_AUTHORIZED
            v.careers_url = page.get("url") or url
            v.evidence = _quote_around(text, _SAYS_NO_OPENINGS)
            return v
        signals, found_roles = research_agent.hiring_signals(text, limit=8)
        if signals or found_roles:
            v.status = VERIFIED
            v.careers_url = page.get("url") or url
            v.roles = found_roles
            v.evidence = _best_signal(signals, found_roles)
            return v

    # the front page is the last resort — some companies only say it there
    v.checked.append(base)
    page = web.read(base, fresh=fresh, timeout=TIMEOUT, prefer="direct")
    if page.get("text"):
        reached_any = True
        signals, found_roles = research_agent.hiring_signals(page["text"], limit=8)
        if signals or found_roles:
            v.status = VERIFIED
            v.careers_url = page.get("url") or base
            v.roles = found_roles
            v.evidence = _best_signal(signals, found_roles)
            return v

    if not reached_any:
        v.status = UNREACHABLE
        v.evidence = f"could not read any of {len(v.checked)} page(s) on {base} — our failure, not theirs"
    else:
        v.status = NOT_AUTHORIZED
        v.evidence = (
            f"read {len(v.checked)} page(s) on {base}; none of them says they are hiring "
            "and none lists an open role"
        )
    return v


# ------------------------------------------------------------------ public API

def verify(name: str, *, website: str | None = None, ats: str | None = None,
           slug: str | None = None, claimed_role: str | None = None,
           fresh: bool = False) -> Verification:
    """Check one company. ATS first (cheapest and strongest), careers page second."""
    v = asyncio.run(_check_ats(name, website, ats, slug)) or Verification()
    if v.status != VERIFIED:
        careers = _check_careers(website, fresh=fresh)
        careers.checked = v.checked + careers.checked
        v = careers

    if v.status == VERIFIED and claimed_role:
        # "they are hiring" is not "they are hiring THIS". Only the roles we can see on
        # their own page count, and an ATS board we did not enumerate cannot answer it.
        if v.roles and not _role_matches(claimed_role, v.roles):
            v.status = ROLE_MISSING
            v.evidence = (
                f"they are hiring ({', '.join(v.roles[:4])}) but nothing matching "
                f"'{claimed_role}' is listed"
            )
    return v


_WORD = re.compile(r"[a-z0-9+#]+")


# Words that appear in half of all engineering titles: on their own they prove nothing,
# so a match needs either two of them together or one word more specific than these.
_GENERIC = {"engineer", "engineering", "developer", "software", "senior", "staff", "lead",
            "sr", "jr", "junior", "principal", "and", "the", "of", "for", "at", "in", "i",
            "ii", "iii", "iv", "remote", "team"}


def _role_matches(claimed: str, roles: list[str]) -> bool:
    """Does any listed role plausibly answer the claim?

    Deliberately generous about wording and strict about substance: "Senior Backend
    Engineer" is answered by "Senior Software Engineer, Platform" (two shared words),
    but "Mobile iOS Engineer" is not answered by "Data Engineer" (only the generic
    word "engineer" is shared).
    """
    want = {w for w in _WORD.findall(claimed.lower()) if len(w) > 1}
    if not want:
        return True
    for role in roles:
        have = {w for w in _WORD.findall(role.lower()) if len(w) > 1}
        shared = want & have
        if len(shared) >= 2 or (shared - _GENERIC):
            return True
    return False


def verify_company(company_id: int, *, claimed_role: str | None = None, fresh: bool = False) -> dict:
    """Verify one company row and write the verdict back."""
    init_db()
    with get_session() as session:
        company = session.get(Company, company_id)
        if company is None:
            return {"error": f"no company {company_id}"}
        name, website = company.name, company.website
        ats, slug = company.ats, company.ats_slug
        claim = claimed_role or company.hiring_claim

    v = verify(name, website=website, ats=ats, slug=slug, claimed_role=claim, fresh=fresh)

    with get_session() as session:
        company = session.get(Company, company_id)
        company.hiring_status = v.status
        company.hiring_evidence = v.evidence
        company.hiring_roles = json.dumps(v.roles) if v.roles else None
        company.careers_url = v.careers_url or company.careers_url
        company.hiring_checked_at = utcnow()
        if claimed_role:
            company.hiring_claim = claimed_role
        session.add(company)
        session.commit()
    log.info("hiring: %s -> %s", name, v.status)
    return {"company": name, **v.as_dict()}


def verify_pending(limit: int = 10, *, tiers: tuple[str, ...] = ("tier1", "tier2", "unknown", "prospect"),
                   recheck_days: int = MAX_AGE_DAYS, fresh: bool = False) -> list[dict]:
    """Verify the companies worth verifying: graded targets, oldest check first.

    A `reject` is never checked — we are not going to write to them either way — and a
    check younger than `recheck_days` is left alone.
    """
    init_db()
    from jobhunter.targeting import TIER_ORDER

    cutoff = utcnow() - timedelta(days=recheck_days)
    with get_session() as session:
        rows = session.exec(select(Company)).all()
        pending = [
            c for c in rows
            if (c.tier or "unknown") in tiers
            and (c.hiring_checked_at is None or c.hiring_checked_at < cutoff)
            and (c.website or c.ats_slug)
        ]
        pending.sort(key=lambda c: (TIER_ORDER.get(c.tier or "unknown", 9), c.name))
        ids = [c.id for c in pending[:limit]]
    return [verify_company(cid, fresh=fresh) for cid in ids]


def claim_from_job(job_id: int) -> dict:
    """Verify the company behind one scraped posting, testing that posting's own title.

    This is the aggregator case: RemoteOK or an HN thread says a role exists. Before we
    write to anyone about it, the company's own board has to agree.
    """
    init_db()
    with get_session() as session:
        job = session.get(Job, job_id)
        if job is None or not job.company_id:
            return {"error": f"no job {job_id} with a company"}
        title, company_id, url = job.title, job.company_id, job.url
    result = verify_company(company_id, claimed_role=title)
    result["claim"] = {"role": title, "source_url": url}
    with get_session() as session:
        company = session.get(Company, company_id)
        if company:
            company.hiring_claim_url = url
            session.add(company)
            session.commit()
    return result


__all__ = ["Verification", "verify", "verify_company", "verify_pending", "claim_from_job",
           "VERIFIED", "ROLE_MISSING", "NOT_AUTHORIZED", "UNREACHABLE", "UNCHECKED"]
