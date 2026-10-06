"""Bulk company harvest — N *different* companies that fit, then their roles, then people.

MOTIV §4 step 1 at volume. Three keyless-or-cheap sources feed one admission gate:

  yc    the open yc-oss mirror of the YC directory (~1,500 hiring companies): one-liner,
        regions, batch, team size — a description and a location for free, and a batch
        date that stands in for "recently funded"
  exa   `category: company` searches — 25 company sites a call, inside
        research.exa_daily_cap (the key is a $10 free tier with nothing behind it)
  x     posts already found by `research x` — claims for hiring_verify, never evidence

Admission is admission.admit() applied *before* a row exists: a junk or headline name is
cleaned or refused, a famous / listed / mega-funded company is refused (overrated), a
twin of an existing row is merged into it, and a region outside the configured ones is
skipped. Headcount is never a gate (2026-10-06). What gets in becomes a Company row
carrying the evidence it arrived with — nothing is stated more firmly than the source said
it. funding_stage is set to "Seed" only for a YC batch inside
targeting.funding.max_age_months; an older batch is just a note, because the company may
have raised three rounds since.

Then, per company and in cost order: ATS probe (free JSON, and the strongest possible
hiring proof — their own live board) → roles from that board through pipeline.persist,
so the same title and location gates apply as to every other source → grade. The
careers-page check for the no-ATS residue and people-finding are separate commands:
they are the slow, rate-limited part and should never block the count.

Layering: pipeline layer. scrapers, targeting, hiring_verify, research and db below;
api and the CLI above.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx
from sqlmodel import col, select

from jobhunter import CONFIG
from jobhunter import admission
from jobhunter import backoff
from jobhunter import pipeline
from jobhunter import targeting
from jobhunter.db import Company, Contact, Job, get_session, init_db, utcnow
from jobhunter.hiring_verify import VERIFIED
from jobhunter.research import web
from jobhunter.scrapers import ashby, discover, greenhouse, lever
from jobhunter.scrapers.base import RawJob, gather_limited, make_client

log = logging.getLogger(__name__)

_H = CONFIG.get("harvest") or {}
YC_DIRECTORY = "https://yc-oss.github.io/api/companies/all.json"
REGIONS_OK: set[str] = set(_H.get("regions") or ["us", "uk", "de", "nl", "eu", "india", "remote"])
FUNDING_MAX_MONTHS = int(((CONFIG.get("targeting") or {}).get("funding") or {}).get("max_age_months", 30))

_FETCHERS = {"greenhouse": greenhouse.fetch_board, "lever": lever.fetch_board, "ashby": ashby.fetch_board}

# search hits that are about a company but are not the company's own site
_NOT_A_SITE = re.compile(
    r"(linkedin|crunchbase|pitchbook|dealroom|wellfound|angel|glassdoor|indeed|techcrunch|"
    r"ycombinator|producthunt|github|medium|substack|twitter|x\.com|facebook|wikipedia|"
    r"bloomberg|reuters|forbes|cbinsights|tracxn|craft\.co|zoominfo|apollo|rocketreach|"
    r"g2\.com|capterra|trustpilot)\.",
    re.I,
)
_TITLE_NOISE = re.compile(r"\s*[\(\[]\s*YC\s+[A-Z]+\d{2}\s*[\)\]]\s*|\s*[|–—-]\s.*$|\s*:\s.*$", re.I)


@dataclass
class HarvestStats:
    source: str
    seen: int = 0
    candidates: int = 0
    admitted: int = 0
    updated: int = 0
    skipped: dict[str, int] = field(default_factory=dict)

    def skip(self, why: str) -> None:
        self.skipped[why] = self.skipped.get(why, 0) + 1

    def as_dict(self) -> dict:
        return {"source": self.source, "seen": self.seen, "candidates": self.candidates,
                "admitted": self.admitted, "updated": self.updated, "skipped": self.skipped}


# ------------------------------------------------------------------ helpers

_BATCH_MONTH = {"winter": 1, "spring": 3, "summer": 6, "fall": 9}


def batch_date(batch: str | None) -> str | None:
    """'Winter 2025' -> '2025-01-01'. The batch start is the closest thing to a round date."""
    m = re.match(r"\s*(winter|spring|summer|fall)\s+(\d{4})", batch or "", re.I)
    if not m:
        return None
    return f"{m.group(2)}-{_BATCH_MONTH[m.group(1).lower()]:02d}-01"


def _months_since(iso: str | None) -> int | None:
    if not iso:
        return None
    try:
        d = datetime.strptime(iso[:10], "%Y-%m-%d")
    except ValueError:
        return None
    now = utcnow()
    return (now.year - d.year) * 12 + (now.month - d.month)


def _clean_name(title: str) -> str | None:
    name = _TITLE_NOISE.sub("", (title or "").strip()).strip(" -–—|:")
    name = re.sub(r"\s+", " ", name)
    if not name or len(name) > 60 or len(name.split()) > 6:
        return None
    return name


def _first_sentence(text: str | None, limit: int = 300) -> str | None:
    # Exa hands back page text as Markdown: drop headings ("# Acme"), link lines and
    # bullets so the description is a sentence about the business, not its title.
    lines = [ln.strip() for ln in (text or "").splitlines()]
    lines = [ln for ln in lines if ln and not ln.startswith(("#", "*", "-", "|", "[", "!")) and len(ln.split()) >= 5]
    text = re.sub(r"\s+", " ", " ".join(lines) if lines else (text or "")).strip()
    text = re.sub(r"^#+\s*", "", text)
    if not text:
        return None
    first = re.split(r"(?<=[.!?])\s+", text)[0].strip()
    first = first if len(first) >= 25 else text
    return first[:limit] or None


def _norm_name(name: str) -> str:
    """A company's identity key — admission.name_key, kept under its old name."""
    return admission.name_key(name)


def _find_company(session, name: str) -> Company | None:
    """Exact name, then the normalized name key — the anti-duplicate gate. The index
    lives on the session (admission._index), so threads never share a stale copy."""
    return admission.find_existing(session, name)


def _skip_key(reason: str) -> str:
    return "junk name" if reason.startswith("junk name") else reason.split(":")[0].split(" — ")[0]


def _upsert(session, *, name: str, website: str | None, source: str, description: str | None,
            region: str | None, note: str, funding: dict | None = None,
            claim: dict | None = None, story: str | None = None, story_evidence: str | None = None,
            team_size: int | None = None, stats: HarvestStats, headline: bool = False,
            website_is_link: bool = False) -> Company | None:
    """Create or fill-in-the-blanks through the admission gate. None when the gate says
    no (junk name, overrated, quarantined) — the reason is counted in stats.skipped.
    Never overwrites something already known, except that a newer round replaces an
    older one."""
    fund = funding or {}
    verdict = admission.admit(admission.Candidate(
        name=name, source=source, website=website, website_is_link=website_is_link, headline=headline,
        stage=fund.get("stage"), amount_usd_m=fund.get("amount_usd_m"), total_usd_m=fund.get("total_usd_m"),
        valuation_usd_m=fund.get("valuation_usd_m"), announced=fund.get("announced"),
        evidence=fund.get("evidence"),
    ), session)
    if not verdict.ok:
        stats.skip(_skip_key(verdict.reason))
        return None
    company = verdict.existing
    new = company is None
    if new:
        company = Company(name=verdict.name)
    admission.stamp(company, verdict if new else None)
    company.website = company.website or verdict.website
    company.domain = company.domain or verdict.domain
    company.description = company.description or description
    company.hq_region = company.hq_region or region
    if story and not company.story:
        company.story = story[:1200]
        company.story_evidence = story_evidence
    if team_size and not company.team_size:
        company.team_size = int(team_size)
    if funding:
        _apply_funding(company, funding)
    if claim and not company.hiring_claim_url:
        # the hiring post on record: who said they are hiring, and where
        company.hiring_claim = claim.get("text")
        company.hiring_claim_url = claim.get("url")
        company.hiring_claim_by = claim.get("by")
        company.hiring_claim_source = source
    tag = f"[{source}] {note}".strip()
    if tag not in (company.notes or ""):
        company.notes = f"{company.notes}\n{tag}".strip() if company.notes else tag
    session.add(company)
    session.flush()
    admission.remember(session, company)
    if new:
        stats.admitted += 1
    else:
        stats.updated += 1
    return company


def _apply_funding(company: Company, funding: dict) -> None:
    """Write a round onto a row: the stage in the one vocabulary, the amount (feeds carry
    it — it used to be dropped here), the date, the quote. Blanks are filled; a round
    announced later than the one on record replaces it."""
    stage = admission.normalize_stage(funding.get("stage"))
    announced = funding.get("announced")
    newer = bool(announced and company.funding_announced and str(announced) > str(company.funding_announced))
    if newer or not (company.funding_stage or company.funding_amount_usd_m):
        company.funding_stage = stage or (None if newer else company.funding_stage)
        amt = funding.get("amount_usd_m")
        company.funding_amount_usd_m = float(amt) if amt else (None if newer else company.funding_amount_usd_m)
        company.funding_announced = announced or company.funding_announced
        company.funding_evidence = funding.get("evidence") or company.funding_evidence
    else:
        company.funding_stage = company.funding_stage or stage
        company.funding_announced = company.funding_announced or announced
        company.funding_evidence = company.funding_evidence or funding.get("evidence")
    if funding.get("total_usd_m") and not company.funding_total_usd_m:
        company.funding_total_usd_m = float(funding["total_usd_m"])
    if funding.get("valuation_usd_m") and not company.valuation_usd_m:
        company.valuation_usd_m = float(funding["valuation_usd_m"])
        company.valuation_evidence = company.valuation_evidence or funding.get("evidence")


# ------------------------------------------------------------------ source: YC directory

def _yc_fits(c: dict, stats: HarvestStats) -> tuple[str | None, str | None]:
    """(region, reason-to-skip). Region is the configured key or None."""
    if not c.get("isHiring") or (c.get("status") or "").lower() != "active":
        return None, "not hiring / inactive"
    if (c.get("stage") or "").lower() in ("public", "acquired"):
        return None, "public or acquired"
    # headcount is not a gate any more (2026-10-06); fame is, and admit() checks it
    region = targeting.region_of(" ".join(c.get("regions") or []), " ".join(c.get("all_locations") or []))
    if region not in REGIONS_OK:
        return None, "region outside targets"
    return region, None


def admit_yc(limit: int | None = None, *, timeout: int = 90) -> HarvestStats:
    """Every YC company that fits, newest batch first, into the registry."""
    init_db()
    stats = HarvestStats("yc")
    try:
        data = httpx.get(YC_DIRECTORY, timeout=timeout).json()
    except Exception as e:  # noqa: BLE001
        log.error("yc directory unavailable: %s", e)
        stats.skip(f"directory unavailable: {type(e).__name__}")
        return stats
    if not isinstance(data, list):
        stats.skip("directory malformed")
        return stats
    stats.seen = len(data)

    fitting: list[tuple[str, dict]] = []
    for c in data:
        if not isinstance(c, dict) or not c.get("name"):
            continue
        region, why = _yc_fits(c, stats)
        if why:
            stats.skip(why)
            continue
        fitting.append((region, c))
    fitting.sort(key=lambda rc: batch_date(rc[1].get("batch")) or "0000", reverse=True)
    if limit:
        fitting = fitting[:limit]
    stats.candidates = len(fitting)

    with get_session() as session:
        for region, c in fitting:
            announced = batch_date(c.get("batch"))
            age = _months_since(announced)
            funding = None
            if age is not None and age <= FUNDING_MAX_MONTHS:
                funding = {"stage": "Seed", "announced": announced,
                           "evidence": f"YC {c.get('batch')} (yc-oss directory) — batch investment, seed-stage"}
            note = (f"YC {c.get('batch')}; team {c.get('team_size') or '?'}; "
                    f"{', '.join((c.get('industries') or [])[:3])}; regions {', '.join((c.get('regions') or [])[:3])}")
            claim = {"text": f"listed as hiring in the YC directory ({c.get('batch')})",
                     "url": c.get("url") or f"https://www.ycombinator.com/companies/{c.get('slug')}",
                     "by": "Y Combinator"}
            long = re.sub(r"\s+", " ", (c.get("long_description") or "")).strip()
            _upsert(session, name=str(c["name"]).strip(), website=c.get("website"), source="yc",
                    description=_first_sentence(c.get("one_liner") or c.get("long_description")),
                    region=region, note=note, funding=funding, claim=claim, stats=stats,
                    # their own words in the YC directory: the story, free, no model
                    # a product blurb, not the origin: kept as a fallback, replaced once the
                    # About page has been read (enrich.py, missing="story")
                    story=long if len(long) >= 80 else None,
                    story_evidence="YC directory (what they say they do)" if len(long) >= 80 else None,
                    team_size=c.get("team_size") or None)
        session.commit()
    log.info("harvest yc: %s", stats.as_dict())
    return stats


# ------------------------------------------------------------------ source: Exa company search

DEFAULT_EXA_QUERIES = _H.get("exa_queries") or [
    "seed-stage AI startup in the United States hiring remote software engineers",
    "Series A startup in San Francisco hiring machine learning engineers, remote friendly",
    "London startup that recently raised a seed round, hiring backend engineers, remote",
    "Berlin AI startup, seed funded, hiring a founding engineer",
    "Bangalore AI startup, funded, hiring software engineers",
    "developer tools startup hiring a founding engineer, remote",
    "LLM infrastructure startup hiring applied AI engineers, remote in the US",
    "New York seed-stage startup hiring full-stack engineers, remote",
    "AI agents startup that raised a seed round in 2026 and is hiring engineers",
    "Indian startup, Series A, hiring ML engineers in Bengaluru or Hyderabad",
    "remote-first European startup, seed funded, hiring backend engineers",
    "YC-backed startup hiring AI engineers, remote",
    # the ideal company: hires engineers from any country, no visa, pays in dollars
    "remote-first startup that hires software engineers from anywhere in the world, no visa sponsorship needed",
    "US startup hiring remote engineers globally, work from any country, async team",
    "fully remote AI company, globally distributed engineering team, hiring worldwide",
    "UK startup hiring remote developers internationally, contractors from India welcome",
    "startup careers page: we hire from anywhere, timezone agnostic, remote engineers",
    "seed-stage company hiring remote software engineers in India for a US team",
]


def admit_exa(queries: list[str] | None = None, *, per_query: int = 25, fresh: bool = False) -> HarvestStats:
    """Company sites from Exa's company index. One search per query, capped by exa_daily_cap."""
    init_db()
    stats = HarvestStats("exa")
    queries = queries or DEFAULT_EXA_QUERIES
    with get_session() as session:
        for q in queries:
            hit = web.search(q, limit=per_query, category="company", fresh=fresh)
            if hit.get("error"):
                stats.skip(f"search error: {hit['error'][:80]}")
                if "cap" in hit["error"]:
                    break
                continue
            for r in hit.get("results") or []:
                stats.seen += 1
                url = r.get("url") or ""
                host = (urlparse(url).hostname or "").lower()
                if not host or _NOT_A_SITE.search(host) or admission.bogus_host(host.removeprefix("www.")):
                    stats.skip("not the company's own site")
                    continue
                name = _clean_name(r.get("title") or "")
                if not name:
                    stats.skip("no usable name in title")
                    continue
                snippet = r.get("snippet") or ""
                region = targeting.region_of(snippet, q)
                if region not in REGIONS_OK:
                    stats.skip("region outside targets")
                    continue
                stats.candidates += 1
                site = f"{urlparse(url).scheme or 'https'}://{host}"
                _upsert(session, name=name, website=site, source="exa",
                        description=_first_sentence(snippet), region=region,
                        note=f"found by: {q[:80]}", stats=stats)
            session.commit()
    log.info("harvest exa: %s", stats.as_dict())
    return stats


# ------------------------------------------------------------------ source: X posts

DEFAULT_X_QUERIES = _H.get("x_queries") or [
    "we're hiring AI engineer remote",
    "we're hiring software engineer remote startup",
    "hiring founding engineer",
    "we are hiring ML engineer",
    "hiring backend engineer remote",
    "hiring software engineers Bangalore startup",
]

_X_SYSTEM = """You read one social-media post and say whether it announces that a specific
company is hiring, using only the words in the post. You never invent a company, a role or
a location. If the post does not say, the field is null. JSON only."""

_X_PROMPT = """Post by @{handle} ({name}):
---
{text}
---

Reply with exactly:
{{"is_hiring_post": true|false,
  "company": "<the hiring company's name, or null>",
  "roles": ["<role titles named in the post>"],
  "location": "<city / country text from the post, or null>",
  "remote": true|false|null,
  "evidence": "<the words you read the company from, quoted>"}}

The company is the organisation the post says is hiring — the poster's own organisation
when the post says "we're hiring" and names none other. A recruiter reposting for a client
names the client. If you cannot tell which company, company is null."""


def _x_extract(post: dict) -> dict | None:
    """The `cheap` alias reads the post; None on any failure or a non-hiring post."""
    from jobhunter import llm

    text = (post.get("text") or "").strip()
    if len(text) < 15:
        return None
    try:
        data = llm.chat_json(
            _X_PROMPT.format(handle=post.get("handle"), name=post.get("name") or "", text=text[:1500]),
            _X_SYSTEM, temperature=0.0, alias="cheap", purpose="x-hiring-post", default=None,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("x extract failed (model error, not an empty post): %s", e)
        return None
    if not isinstance(data, dict) or not data.get("is_hiring_post"):
        return None
    company = (data.get("company") or "").strip()
    if not company or company.lower() in ("null", "none", "unknown"):
        return None
    return data


def _x_published(raw: str | None):
    if not raw:
        return None
    try:
        from dateutil import parser as dp

        return dp.parse(raw).astimezone(timezone.utc).replace(tzinfo=None)
    except Exception:  # noqa: BLE001
        return None


def admit_x(queries: list[str] | None = None, *, per_query: int = 20, fresh: bool = False) -> HarvestStats:
    """Hiring posts on X → companies, with the post itself as the hiring record.

    Search runs through the throwaway session (research/x_search.py, capped per day);
    the free model reads each post and names the company only if the post does. The
    post URL, the poster's handle and the post date become hiring_claim_* — so the
    company page can always answer "where did you get this?". hiring_verify then
    decides against the company's own site; a post alone is never `verified`.
    """
    from jobhunter.research import x_search

    init_db()
    stats = HarvestStats("x")
    if not x_search.session_present():
        stats.skip("no X session in .env")
        return stats
    with get_session() as session:
        for q in queries or DEFAULT_X_QUERIES:
            hit = x_search.search(q, limit=per_query, fresh=fresh)
            if hit.get("error"):
                stats.skip(f"search: {hit['error'][:80]}")
                if "cap" in hit["error"] or "restricted" in hit["error"]:
                    break
                continue
            for post in hit.get("posts") or []:
                stats.seen += 1
                data = _x_extract(post)
                if not data:
                    stats.skip("not a hiring post / no company named")
                    continue
                name = _clean_name(data["company"]) or data["company"][:60]
                # a handle is a poster, not an employer: a recruiter reposting "we're hiring"
                # under their own name is not a company we can verify or write to
                if name.startswith("@") or name.lower().lstrip("@") == (post.get("handle") or "").lower():
                    stats.skip("company is the poster's own handle")
                    continue
                region = "remote" if data.get("remote") else targeting.region_of(
                    data.get("location"), post.get("text"))
                if region not in REGIONS_OK:
                    stats.skip("no location in the post, or outside targets")
                    continue
                roles = [r for r in (data.get("roles") or []) if isinstance(r, str)][:6]
                stats.candidates += 1
                claim = {"text": (post.get("text") or "")[:300], "url": post["url"], "by": f"@{post['handle']}"}
                company = _upsert(
                    session, name=name, website=None, source="x", description=None, region=region,
                    note=f"post by @{post['handle']} — roles: {', '.join(roles) or '?'}; "
                         f"evidence: {str(data.get('evidence') or '')[:120]}",
                    claim=claim, stats=stats,
                )
                if company is None:
                    continue
                if company.hiring_claim_source == "x" and not company.hiring_claim_at:
                    company.hiring_claim_at = _x_published(post.get("published"))
                if roles and not company.hiring_claim:
                    company.hiring_claim = ", ".join(roles)
                # commit per post: the model call before the next post takes up to a
                # minute, and an open write transaction that long locks out every other
                # stage (the people hunt was losing companies to "database is locked")
                session.commit()
            session.commit()
    log.info("harvest x: %s", stats.as_dict())
    return stats


# ------------------------------------------------------------------ ATS probe + roles

_PROBED_NONE = "[ats] probed: no public board"
_ROLES_READ = "[roles] board read"


async def _probe_one(http: httpx.AsyncClient, cid: int, name: str, website: str | None) -> tuple[int, tuple | None, int]:
    hit, errors = await discover.find_ats_status(http, name, website)
    return cid, hit, errors


def probe_ats(limit: int | None = None, *, concurrency: int = 6,
              company_ids: list[int] | None = None) -> dict:
    """Which public board (Greenhouse / Lever / Ashby) each new company uses.

    A live board with postings is the company saying "we are hiring" in its own
    machine-readable words, so a hit also sets hiring_status=verified — the same
    verdict hiring_verify would reach, without the careers-page round trip.
    `company_ids` narrows the probe to those rows (a hand-picked list) and re-probes
    them even if an earlier pass found nothing.
    """
    init_db()
    with get_session() as session:
        stmt = select(Company).where(col(Company.ats).is_(None), col(Company.status).is_(None))
        if company_ids:
            stmt = stmt.where(col(Company.id).in_(company_ids))
        else:
            stmt = stmt.where(col(Company.overrated).is_not(True))
        rows = session.exec(stmt).all()
        rows.sort(key=lambda c: -(c.priority_score or 0))
        pending = [(c.id, c.name, c.website) for c in rows
                   if (c.website or c.name) and (company_ids or (
                       _PROBED_NONE not in (c.notes or "") and backoff.due(c, "ats-probe")))]
        if limit:
            pending = pending[:limit]

    async def run() -> list:
        async with make_client() as http:
            return await gather_limited([_probe_one(http, *p) for p in pending], limit=concurrency)

    results = asyncio.run(run()) if pending else []
    found = retry = 0
    with get_session() as session:
        for item in results:
            if not item:
                continue
            cid, hit, errors = item
            company = session.get(Company, cid)
            if company is None:
                continue
            if hit:
                ats, slug, count = hit
                company.ats, company.ats_slug = ats, slug
                company.careers_url = company.careers_url or discover.PROBES[ats].format(slug=slug)
                company.hiring_status = VERIFIED
                company.hiring_evidence = f"{ats} board '{slug}' is live with {count} open posting(s)"
                company.hiring_checked_at = utcnow()
                # their own board beats a directory flag as the post on record
                if not company.hiring_claim_url or company.hiring_claim_source in ("yc", None):
                    company.hiring_claim = f"{count} open posting(s) on their {ats} board"
                    company.hiring_claim_url = discover.PROBES[ats].format(slug=slug)
                    company.hiring_claim_by = None
                    company.hiring_claim_source = "ats"
                    company.hiring_claim_at = utcnow()
                found += 1
                backoff.ok(company, "ats-probe")
            elif errors:
                # timeouts / 429 / 5xx: we could not tell, so it is not "no board" — retry later
                backoff.fail(company, "ats-probe", f"{errors} probe(s) errored")
                retry += 1
            else:
                company.notes = f"{company.notes}\n{_PROBED_NONE}".strip() if company.notes else _PROBED_NONE
            session.add(company)
        session.commit()
    out = {"probed": len(pending), "boards_found": found, "no_board": len(pending) - found - retry,
           "retry_later": retry}
    log.info("harvest probe: %s", out)
    return out


def scrape_roles(limit: int | None = None, *, only_without_jobs: bool = True, concurrency: int = 6,
                 company_ids: list[int] | None = None) -> dict:
    """Every open role from each discovered board, through the normal persist gate."""
    init_db()
    with get_session() as session:
        stmt = select(Company).where(col(Company.ats).is_not(None), col(Company.ats_slug).is_not(None))
        if company_ids:
            stmt = stmt.where(col(Company.id).in_(company_ids))
        rows = session.exec(stmt).all()
        if only_without_jobs:
            # "no jobs" is also what a board full of senior/non-engineering roles looks
            # like after the gates — so a board we already read is marked, not re-read
            with_jobs = set(session.exec(select(Job.company_id).distinct()).all())
            rows = [c for c in rows if c.id not in with_jobs and _ROLES_READ not in (c.notes or "")]
        targets = [(c.name, c.website, c.ats, c.ats_slug) for c in rows if c.ats in _FETCHERS]
        if limit:
            targets = targets[:limit]

    async def run() -> list:
        async with make_client() as http:
            return await gather_limited(
                [_FETCHERS[ats](http, slug, name) for name, _, ats, slug in targets], limit=concurrency
            )

    results = asyncio.run(run()) if targets else []
    jobs: list[RawJob] = []
    for (name, website, ats, _), r in zip(targets, results):
        for j in r or []:
            j.company_website = j.company_website or website
            j.company_name = j.company_name or name
            jobs.append(j)
    stats = pipeline.ScrapeStats(scraped=len(jobs), per_source={"harvest": len(jobs)})
    stats = pipeline.persist(jobs, stats)
    # remember which boards were read, so the next run only reads new ones
    read_names = {name for name, *_ in targets}
    with get_session() as session:
        for c in session.exec(select(Company).where(col(Company.name).in_(read_names))).all():
            if _ROLES_READ not in (c.notes or ""):
                c.notes = f"{c.notes}\n{_ROLES_READ}".strip() if c.notes else _ROLES_READ
                session.add(c)
        session.commit()
    out = {"boards": len(targets), **stats.as_dict()}
    log.info("harvest roles: %s", out)
    return out


# ------------------------------------------------------------------ people

def find_people(limit: int = 200, *, tiers: tuple[str, ...] = ("tier1", "tier2", "prospect", "unknown"),
                refresh_days: int = 30, require_roles: bool = True) -> dict:
    """Who could refer us, company by company, best tier first.

    The free waterfall in contacts/ (GitHub commits → their site → pattern → Hunter),
    run over the fitting companies that have not been searched in `refresh_days`.
    Sequential on purpose: GitHub's budget is per hour and the site scraper is polite,
    and a kill between companies loses nothing — every company commits on its own.
    """
    from datetime import timedelta

    from jobhunter.contacts import discover_for_company

    init_db()
    cutoff = utcnow() - timedelta(days=refresh_days)
    with get_session() as session:
        rows = session.exec(select(Company).where(col(Company.status).is_(None))).all()
        with_jobs = set(session.exec(select(Job.company_id).distinct()).all())
        pending = [
            c for c in rows
            if (c.tier or "unknown") in tiers
            and c.underrated is not False
            and not c.overrated
            # an unknown region is not a no: funding-feed rows often carry none until facts run
            and (c.hq_region in REGIONS_OK or c.hq_region is None)
            and (c.website or c.domain or c.github_org)
            and (not require_roles or c.id in with_jobs)
            and (c.contacts_found_at is None or c.contacts_found_at < cutoff)
        ]
        # best priority first (recently funded, hiring freshers, can pay); then fresher
        # roles, verified hiring and tier as before
        with_fresher = set(session.exec(
            select(Job.company_id).where(Job.is_senior == False).distinct()  # noqa: E712
        ).all())
        pending.sort(key=lambda c: (0 if c.scrape_first else 1, -(c.priority_score or 0),
                                    targeting.TIER_ORDER.get(c.tier or "unknown", 9),
                                    0 if c.id in with_fresher else 1,
                                    0 if c.hiring_status == VERIFIED else 1, c.name))
        ids = [c.id for c in pending[:limit]]

    out = {"companies": len(ids), "with_people": 0, "added": 0, "verified": 0, "by_source": {}}
    for cid in ids:
        try:
            s = asyncio.run(discover_for_company(cid))
        except Exception as e:  # noqa: BLE001 — one company must not end the hunt
            log.warning("people: company %s failed: %s", cid, e)
            continue
        if s.get("added"):
            out["with_people"] += 1
        out["added"] += s.get("added", 0)
        out["verified"] += s.get("verified", 0)
        for k in ("github", "site", "pattern", "hunter"):
            out["by_source"][k] = out["by_source"].get(k, 0) + s.get(k, 0)
    log.info("harvest people: %s", out)
    return out


# ------------------------------------------------------------------ the run

def status() -> dict:
    """How many different companies fit, and how far each has got."""
    init_db()
    with get_session() as session:
        rows = session.exec(select(Company)).all()
        with_jobs = set(session.exec(select(Job.company_id).distinct()).all())
        with_fresher = set(session.exec(
            select(Job.company_id).where(Job.is_senior == False).distinct()  # noqa: E712
        ).all())
        with_anywhere = set(session.exec(
            select(Job.company_id).where(Job.remote_anywhere == True).distinct()  # noqa: E712
        ).all())
        fit = [c for c in rows if c.underrated is not False and c.tier != "reject"
               and not c.overrated and c.status is None
               and (c.hq_region in REGIONS_OK or c.hq_region is None)]
        tiers: dict[str, int] = {}
        for c in rows:
            tiers[c.tier or "ungraded"] = tiers.get(c.tier or "ungraded", 0) + 1
        with_leads = set(session.exec(select(Contact.company_id).distinct()).all())
        return {
            "companies": len(rows),
            "fit": len(fit),
            # the three things a company must carry before it counts as scraped
            "1_described": sum(1 for c in fit if c.description and len(c.description) >= 25),
            "1_with_story": sum(1 for c in fit if c.story),
            "2_pay_stated": sum(1 for c in fit if c.pay_power_band == "pays"),
            "2_pay_power_65plus": sum(1 for c in fit if (c.pay_power or 0) >= 65),
            "3_with_leads": sum(1 for c in fit if c.id in with_leads),
            "fit_with_board": sum(1 for c in fit if c.ats),
            "fit_hiring_verified": sum(1 for c in fit if c.hiring_status == VERIFIED),
            "fit_with_roles": sum(1 for c in fit if c.id in with_jobs),
            "fit_with_fresher_roles": sum(1 for c in fit if c.id in with_fresher),
            "fit_hiring_from_anywhere": sum(1 for c in fit if c.id in with_anywhere),
            "fit_with_people": sum(1 for c in fit if c.contacts_found_at),
            "tiers": tiers,
            "overrated": sum(1 for c in rows if c.overrated),
            "quarantined": sum(1 for c in rows if c.status == "quarantined"),
            "fit_with_website": sum(1 for c in fit if c.website or c.domain),
            "fit_priority_50plus": sum(1 for c in fit if (c.priority_score or 0) >= 50),
            "by_region": _count(c.hq_region for c in fit),
        }


def _count(values) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in values:
        out[v or "?"] = out.get(v or "?", 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def run(target: int = 500, *, yc_limit: int | None = None, exa: bool = True,
        exa_queries: list[str] | None = None, x: bool = True,
        enrich_limit: int = 300, verify_limit: int = 300, people_limit: int = 200,
        facts_limit: int = 60) -> dict:
    """The whole scrape, for every company it admits. Tejmul, 2026-09-03: a company is
    not "scraped" until it carries all three —

      1. who they are     description + story (own site, About page, YC's words), team, location
      2. what they pay    stated stipend / PPO / salary, else funding, valuation → Pay Power
      3. who can refer    several people per company, ranked, with usable emails

    — plus its roles (fresher first, senior kept) and hiring proof from its own page.
    Order is cost order: free sources first, the model second, Exa last and capped.
    Every stage commits per item, so a kill loses nothing and a rerun resumes.
    """
    from jobhunter import enrich, hiring_verify

    summary: dict = {"target": target}
    # ---- find companies
    summary["yc"] = admit_yc(limit=yc_limit).as_dict()
    if exa and status()["fit"] < target:
        summary["exa"] = admit_exa(exa_queries).as_dict()
    if x:
        summary["x"] = admit_x().as_dict()
    # ---- their roles and hiring proof
    summary["probe"] = probe_ats()
    summary["roles"] = scrape_roles()
    summary["verify"] = {"checked": len(hiring_verify.verify_pending(limit=verify_limit))}
    # ---- who they are, what they pay
    res = enrich.enrich_pending(limit=enrich_limit, use_search=False)
    summary["enrich"] = {"companies": len(res), "described": sum(1 for r in res if r.get("description")),
                         "story": sum(1 for r in res if r.get("story"))}
    facts = enrich.facts_pending(limit=facts_limit)
    summary["facts"] = {"companies": len(facts), "team": sum(1 for r in facts if r.get("team_size")),
                        "funding": sum(1 for r in facts if r.get("funding"))}
    summary["pay"] = targeting.extract_job_pay(only_missing=True)
    summary["grade"] = targeting.grade_companies()
    # ---- who can refer
    summary["people"] = find_people(limit=people_limit)
    summary["status"] = status()
    return summary


# ------------------------------------------------------------------ discovery from the funding press

# Regions worded for a news search, mapped to our region keys.
_NEWS_REGIONS = [
    ("the United States", "us"), ("the United Kingdom", "uk"), ("Germany", "de"),
    ("the Netherlands", "nl"), ("Europe", "eu"), ("India", "india"),
]
_NEWS_TOPICS = ["AI", "developer tools", "fintech", "infrastructure", "data", "healthtech", "cybersecurity", "SaaS"]


# Directory / archive pages that list funded startups but block a plain datacenter fetch —
# scrape.do gets through. More entries than the RSS feeds, and rotated so a run sees fresh ones.
_SCRAPEDO_DIRECTORIES = [
    ("https://www.finsmes.com/category/usa", "us"),
    ("https://www.finsmes.com/category/uk", "uk"),
    ("https://www.finsmes.com/category/europe", "eu"),
    ("https://tech.eu/category/deals/", "eu"),
    ("https://www.eu-startups.com/category/deals/", "eu"),
    ("https://inc42.com/tag/funding/", "india"),
    ("https://yourstory.com/tag/funding", "india"),
    ("https://www.finsmes.com/category/india", "india"),
]


def admit_scrapedo_directories(*, pages: int = 3) -> HarvestStats:
    """New funded startups from directory/archive pages read through scrape.do (the pages
    block a plain fetch). Same headline parser as the RSS feeds; each round is admitted
    with its amount and date. A separate discovery branch from Exa and RSS."""
    import re

    from jobhunter.research import web
    from jobhunter.scrapers import funding_rss

    init_db()
    stats = HarvestStats("scrapedo-dir")
    day = datetime.now().timetuple().tm_yday
    picks = [_SCRAPEDO_DIRECTORIES[(day + i) % len(_SCRAPEDO_DIRECTORIES)] for i in range(pages)]
    _A = re.compile(r"<a[^>]+href=\"([^\"]+)\"[^>]*>(.*?)</a>", re.I | re.S)
    with get_session() as session:
        for url, region in picks:
            if web.scrapedo_budget()["daily_left"] <= 0:
                stats.skip("scrape.do budget reached")
                break
            html = web.fetch_html_scrapedo(url, timeout=45)
            if not html:
                stats.skip(f"no html: {url}")
                continue
            # headlines are <a> link texts; reuse the RSS name/amount/stage parsers
            seen_here: set[str] = set()
            for _, inner in _A.findall(html):
                title = re.sub(r"<[^>]+>", " ", inner)
                title = re.sub(r"\s+", " ", title).strip()
                name = funding_rss._name(title)
                if not name:
                    continue
                stats.seen += 1
                key = admission.name_key(name)
                if not key or key in seen_here:
                    continue
                seen_here.add(key)
                amt = funding_rss._amount_usd_m(title)
                sm = funding_rss._STAGE.search(title)
                fund = {"stage": sm.group(1) if sm else None, "amount_usd_m": amt,
                        "announced": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "evidence": title[:200]}
                stats.candidates += 1
                _upsert(session, name=name, website=None, source="scrapedo-dir",
                        description=None, region=region, note=f"funding directory: {title[:100]}",
                        funding=fund, claim={"text": title[:200], "url": url, "by": None}, stats=stats,
                        headline=True)
            session.commit()
    log.info("admit_scrapedo_directories: %s", stats.as_dict())
    return stats


def admit_funding_rss() -> HarvestStats:
    """Funded startups from the funding-news RSS feeds (TechCrunch, EU-Startups, Sifted,
    YourStory, Finsmes…) — the amount and the date, free, no Exa. Each fitting round is
    admitted with its funding, then flows into probe → roles → people like any other source.
    """
    from jobhunter.scrapers import funding_rss

    init_db()
    stats = HarvestStats("funding-rss")
    rows = funding_rss.fetch()
    stats.seen = len(rows)
    with get_session() as session:
        for r in rows:
            name = r.get("name") or ""
            if len(name.strip()) < 2:
                stats.skip("no usable name")
                continue
            region = r.get("region") or targeting.region_of(r.get("title"))
            if region and region not in REGIONS_OK:
                stats.skip("region outside targets")
                continue
            fund = {"stage": r.get("stage"), "amount_usd_m": r.get("amount_usd_m"),
                    "announced": r.get("announced"), "evidence": r.get("title")}
            stats.candidates += 1
            # an unknown region stays unknown (it used to be written "remote", which put
            # every US/EU round in the remote-anywhere bucket); facts fill it in later
            _upsert(session, name=name, website=None, source="funding-rss",
                    description=None, region=region,
                    note=f"funding press (RSS): {(r.get('title') or '')[:100]}", funding=fund,
                    claim={"text": r.get("title", "")[:200], "url": r.get("url"), "by": None}, stats=stats,
                    headline=True)
        session.commit()
    log.info("admit_funding_rss: %s", stats.as_dict())
    return stats


def discover_funded(*, topics: list[str] | None = None, per_topic: int = 8, max_searches: int = 30) -> HarvestStats:
    """Admit new companies that the existing funding-press scraper (research.find_startups)
    turns up. That scraper searches TechCrunch / EU-Startups / Sifted / YourStory etc. via
    Exa and returns records with a name, region and round; here we run it across our target
    regions and topics and put the ones that fit into the registry — the admission step it
    was missing. Each admitted company then flows into probe → roles → people like any other.
    """
    from jobhunter import research
    from jobhunter.research import web

    init_db()
    stats = HarvestStats("funding-news")
    day = datetime.now().timetuple().tm_yday
    topics = topics or _NEWS_TOPICS
    regions = [w for w, _ in _NEWS_REGIONS]
    key_of = dict(_NEWS_REGIONS)
    stage = ["seed", "Series A", "pre-seed", "Series B"][day % 4]
    budget = max(1, max_searches // max(1, len(topics)))
    with get_session() as session:
        for topic in topics:
            if web.exa_budget()["left"] <= 3:
                stats.skip("exa budget reached")
                break
            res = research.find_startups(topic=topic, regions=regions, stages=[stage],
                                         limit=budget * 4, per_query=per_topic, enrich=0)
            for c in res.get("companies") or []:
                stats.seen += 1
                name = c.get("name") or ""
                if len(name.strip()) < 2:
                    stats.skip("no usable name")
                    continue
                region = key_of.get(c.get("region"), targeting.region_of(c.get("region")))
                if region not in REGIONS_OK:
                    stats.skip("region outside targets")
                    continue
                fd = c.get("funding") or {}
                amt = fd.get("amount_usd_m")
                fund = {"stage": fd.get("stage"), "announced": fd.get("announced"),
                        "amount_usd_m": amt if (amt and amt <= 2000) else None,
                        "valuation_usd_m": fd.get("valuation_usd_m"),
                        "evidence": (fd.get("evidence_quote") or c.get("announcement_title") or "")[:300]} if fd else None
                stats.candidates += 1
                _upsert(session, name=name, website=None, source="funding-news",
                        description=_first_sentence(c.get("announcement_title")), region=region,
                        note=f"funding press: {(c.get('announcement_title') or '')[:100]}", funding=fund,
                        claim={"text": c.get("announcement_title", "")[:200], "url": c.get("announcement_url"), "by": None},
                        stats=stats, headline=True)
            session.commit()
    log.info("discover_funded: %s", stats.as_dict())
    return stats


# ------------------------------------------------------------------ three discovery branches in parallel

def discover_parallel(minutes: int = 20) -> dict:
    """Three independent discovery branches at once, each finding NEW companies from a
    different source (Tejmul, 2026-09-04 — "run all three branches to 3x the outcome"):

      exa       funding-press + company search via Exa (+ model extraction)   (Exa cap)
      agentreach  funding-news RSS feeds — TechCrunch, EU-Startups, Sifted…    (free)
      scrapedo  funding directory/archive pages fetched through scrape.do       (scrape.do cap)

    They admit to the same registry; the admission gate and _upsert dedupe by name, so a
    company found by two branches is one row. After discovery, the new companies are probed
    for boards, their roles scraped, graded, and their people found — all shared.
    """
    import concurrent.futures as cf

    from jobhunter.research import web

    before = status()["companies"]
    out: dict = {"minutes": minutes, "before": before, "branches": {}}

    def exa_branch() -> dict:
        if web.exa_budget()["left"] <= 3:
            return {"skip": "exa budget spent for today"}
        return discover_funded(max_searches=20).as_dict()

    def agentreach_branch() -> dict:
        return admit_funding_rss().as_dict()

    def scrapedo_branch() -> dict:
        if web.scrapedo_budget()["daily_left"] <= 0:
            return {"skip": "scrape.do budget spent for today"}
        return admit_scrapedo_directories(pages=4).as_dict()

    with cf.ThreadPoolExecutor(max_workers=3) as ex:
        futs = {ex.submit(fn): n for n, fn in
                {"exa": exa_branch, "agentreach": agentreach_branch, "scrapedo": scrapedo_branch}.items()}
        for fut in cf.as_completed(futs):
            n = futs[fut]
            try:
                out["branches"][n] = fut.result()
            except Exception as e:  # noqa: BLE001
                log.exception("discovery branch %s failed", n)
                out["branches"][n] = {"error": str(e)[:200]}

    # shared completion for every newly admitted company
    out["probe"] = probe_ats()
    out["roles"] = scrape_roles()
    from jobhunter import targeting

    out["grade"] = targeting.grade_companies(regrade=False)
    out["people"] = find_people(limit=80, require_roles=False)
    after = status()
    out["after"] = after["companies"]
    out["new_companies"] = after["companies"] - before
    out["status"] = after
    return out


# ------------------------------------------------------------------ four channels in parallel

def parallel_run(minutes: int = 30) -> dict:
    """Run the four data channels at once, each on a different STAGE so they never
    duplicate each other's work (Tejmul, 2026-09-04):

      keyless   people hunt + board discovery + roles + hiring verification   (free)
      exa       company facts — headcount, HQ, round, description             (Exa cap)
      model     origin stories from About pages                               (OpenRouter)
      scrapedo  levels.fyi pay + reading sites the free fetchers can't        (scrape.do cap)

    Each channel's queue filters on its own done-marker ([facts]/[story]/[levels],
    description IS NULL, contacts_found_at, hiring_checked_at) and commits per item, so
    two channels touching the same company write different fields and never collide.
    """
    import concurrent.futures as cf
    import time as _t

    from jobhunter import enrich, hiring_verify, levels, targeting
    from jobhunter.research import web

    deadline = _t.time() + minutes * 60
    out: dict = {"minutes": minutes, "channels": {}}

    def keyless() -> dict:
        s = {"people": 0, "verified": 0, "roles": 0, "boards": 0}
        # free discovery first: the funding-news RSS feeds (no Exa, amounts + dates)
        try:
            s["rss_admitted"] = admit_funding_rss().admitted
            probe_ats(limit=40)
            scrape_roles(limit=40)
        except Exception as e:  # noqa: BLE001
            s["rss_error"] = str(e)[:120]
        # public-LinkedIn leads for companies with no GitHub — free, gapped, no login
        try:
            from jobhunter.contacts import linkedin_public

            li = linkedin_public.find_pending(limit=20)
            s["linkedin_leads"] = li.get("found_people", 0)
        except Exception as e:  # noqa: BLE001
            s["linkedin_error"] = str(e)[:120]
        while _t.time() < deadline:
            p = find_people(limit=20)
            s["people"] += p.get("with_people", 0)
            s["verified"] += p.get("verified", 0)
            if _t.time() > deadline:
                break
            v = hiring_verify.verify_pending(limit=20, tiers=("tier1", "tier2", "prospect", "unknown"))
            s["verified_hiring"] = s.get("verified_hiring", 0) + len(v)
            probe_ats(limit=40)
            r = scrape_roles(limit=40)
            s["roles"] += r.get("inserted", 0)
            if not p.get("companies") and not v:
                p2 = find_people(limit=20, require_roles=False)
                s["people"] += p2.get("with_people", 0)
                if not p2.get("companies"):
                    break
        return s

    def exa() -> dict:
        s = {"admitted": 0, "facts": 0}
        # discovery from the funding press + company search, then facts, until the cap
        news = discover_funded(max_searches=12)
        s["admitted"] += news.admitted
        if web.exa_budget()["left"] > 5:
            day = datetime.now().timetuple().tm_yday
            q = [DEFAULT_EXA_QUERIES[(day + i) % len(DEFAULT_EXA_QUERIES)] for i in range(3)]
            s["admitted"] += admit_exa(q).admitted
        # a discovery burst leaves new companies with no board/roles — probe and scrape them
        probe_ats(limit=60)
        scrape_roles(limit=60)
        while _t.time() < deadline and web.exa_budget()["left"] > 5:
            r = enrich.facts_pending(limit=20)
            if not r:
                break
            s["facts"] += len(r)
        s["exa_budget"] = web.exa_budget()
        return s

    def model() -> dict:
        s = {"story": 0, "described": 0}
        while _t.time() < deadline:
            r = enrich.enrich_pending(limit=20, use_search=False, missing="story")
            s["story"] += sum(1 for x in r if x.get("story"))
            if not r:
                r = enrich.enrich_pending(limit=20, use_search=False, missing="description")
                s["described"] += sum(1 for x in r if x.get("description"))
                if not r:
                    break
        return s

    def scrapedo() -> dict:
        s = {"looked": 0, "found": 0}
        while _t.time() < deadline and web.scrapedo_budget()["daily_left"] > 0:
            r = levels.lookup_pending(limit=15)
            s["looked"] += r.get("looked", 0)
            s["found"] += r.get("found", 0)
            if r.get("looked", 0) == 0:
                break
        s["scrapedo_budget"] = web.scrapedo_budget()
        return s

    jobs = {"keyless": keyless, "exa": exa, "model": model, "scrapedo": scrapedo}
    with cf.ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(fn): name for name, fn in jobs.items()}
        for fut in cf.as_completed(futs):
            name = futs[fut]
            try:
                out["channels"][name] = fut.result()
            except Exception as e:  # noqa: BLE001 — one channel failing must not sink the run
                log.exception("channel %s failed", name)
                out["channels"][name] = {"error": str(e)[:200]}
    targeting.grade_companies(regrade=True)
    out["status"] = status()
    return out


__all__ = ["admit_yc", "admit_exa", "admit_x", "probe_ats", "scrape_roles", "find_people", "parallel_run",
           "status", "run", "batch_date"]
