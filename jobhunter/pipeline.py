"""Ingestion pipeline: run the scraper fleet -> filter -> dedup -> persist.

Filtering happens before the database, not after: the seed boards expose ~10k
postings and only a few hundred are jobs this candidate should ever see.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

import yaml
from sqlmodel import Session, select

from jobhunter import CONFIG, ROOT
from jobhunter import normalize as norm
from jobhunter.db import Company, Job, get_session, init_db
from jobhunter.scrapers import get_fetcher
from jobhunter.scrapers.base import RawJob, make_client

log = logging.getLogger(__name__)

COMPANIES_PATH = ROOT / "companies.yaml"


@dataclass
class ScrapeStats:
    scraped: int = 0
    kept: int = 0
    inserted: int = 0
    updated: int = 0
    per_source: dict[str, int] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "scraped": self.scraped,
            "kept": self.kept,
            "inserted": self.inserted,
            "updated": self.updated,
            "per_source": self.per_source,
            "errors": self.errors,
        }


def load_companies() -> list[dict]:
    if not COMPANIES_PATH.exists():
        log.warning("companies.yaml missing — ATS scrapers will be idle")
        return []
    data = yaml.safe_load(COMPANIES_PATH.read_text(encoding="utf-8")) or {}
    return [c for c in (data.get("companies") or []) if c.get("name")]


def sync_companies() -> int:
    """Seed Company rows from companies.yaml.

    The ATS scrapers only know a board slug, not a website — but contact discovery
    needs the domain to scrape the team page and to guess email patterns. The seed
    file has both, so it gets written into the DB before any scraping happens.
    """
    init_db()
    seeded = 0
    with get_session() as session:
        for entry in load_companies():
            name = str(entry["name"]).strip()
            company = session.exec(select(Company).where(Company.name == name)).first()
            if company is None:
                company = Company(name=name)
                seeded += 1
            company.website = company.website or entry.get("website")
            company.domain = company.domain or entry.get("domain") or _domain_of(entry.get("website"))
            company.ats = company.ats or entry.get("ats")
            company.ats_slug = company.ats_slug or entry.get("ats_slug")
            company.github_org = company.github_org or entry.get("github_org")
            session.add(company)
        session.commit()
    log.info("companies.yaml synced: %d new company rows", seeded)
    return seeded


def enabled_sources() -> list[str]:
    src = CONFIG.get("sources") or {}
    from jobhunter.scrapers import REGISTRY

    return [name for name in REGISTRY if src.get(name) is True]


# ---------------------------------------------------------------- scrape

async def scrape_async(sources: list[str] | None = None) -> tuple[list[RawJob], ScrapeStats]:
    companies = load_companies()
    names = sources or enabled_sources()
    stats = ScrapeStats()
    jobs: list[RawJob] = []

    async with make_client() as http:
        async def run(name: str) -> tuple[str, list[RawJob] | Exception]:
            fetch = get_fetcher(name)
            if fetch is None:
                return name, RuntimeError("scraper unavailable")
            try:
                return name, await fetch(http, companies)
            except Exception as e:  # noqa: BLE001 — a broken source must not sink the run
                log.exception("scraper %s failed", name)
                return name, e

        for name, result in await asyncio.gather(*(run(n) for n in names)):
            if isinstance(result, Exception):
                stats.errors[name] = str(result)[:200]
                continue
            stats.per_source[name] = len(result)
            jobs.extend(result)

    stats.scraped = len(jobs)
    log.info("scraped %d raw postings from %s", stats.scraped, stats.per_source)
    return jobs, stats


def keep(job: RawJob) -> bool:
    """Pre-store relevance gate. Also repairs text encoding before anything reads it."""
    if not job.title or not job.url or not job.company_name:
        return False
    job.company_name = norm.fix_mojibake(job.company_name) or job.company_name
    job.title = norm.fix_mojibake(job.title) or job.title
    job.location = norm.fix_mojibake(job.location)
    job.description = norm.fix_mojibake(job.description)
    if not norm.title_relevant(job.title):
        return False
    if not norm.location_ok(job.location, job.remote):
        return False
    return True


def upsert_company(session: Session, job: RawJob) -> Company:
    name = job.company_name.strip()
    company = session.exec(select(Company).where(Company.name == name)).first()
    if company is None:
        company = Company(name=name)
        session.add(company)

    if job.company_website and not company.website:
        company.website = job.company_website
        company.domain = _domain_of(job.company_website)
    if job.ats and not company.ats:
        company.ats = job.ats
        company.ats_slug = job.ats_slug
    if job.github_org and not company.github_org:
        company.github_org = job.github_org
    if not company.hiring_claim_url:
        # the first posting we saw is the hiring post on record: a person on HN, or
        # the company's own board. Kept until something better (a named X post) lands.
        source = job.extra.get("post_source") or ("ats" if job.ats else job.source)
        company.hiring_claim = job.title
        company.hiring_claim_url = job.url
        company.hiring_claim_by = job.extra.get("posted_by")
        company.hiring_claim_source = source
        company.hiring_claim_at = job.posted_at
    session.flush()
    return company


def _domain_of(url: str | None) -> str | None:
    if not url:
        return None
    host = url.split("//")[-1].split("/")[0].split("?")[0].lower()
    host = host.removeprefix("www.")
    return host or None


def persist(jobs: list[RawJob], stats: ScrapeStats) -> ScrapeStats:
    """Filter, dedup, and write to the DB. Existing rows are refreshed, never duplicated."""
    init_db()
    kept = [j for j in jobs if keep(j)]
    stats.kept = len(kept)

    # in-batch dedup: same role often appears on both its ATS and an aggregator
    by_fp: dict[str, RawJob] = {}
    for j in kept:
        fp = norm.fingerprint(j.company_name, j.title)
        prior = by_fp.get(fp)
        # prefer the richest record (ATS descriptions beat aggregator blurbs)
        if prior is None or len(j.description or "") > len(prior.description or ""):
            by_fp[fp] = j

    with get_session() as session:
        seen_companies: dict[str, Company] = {}
        for fp, j in by_fp.items():
            cname = j.company_name.strip()
            if cname not in seen_companies:
                seen_companies[cname] = upsert_company(session, j)
            company = seen_companies[cname]

            existing = session.exec(select(Job).where(Job.url == j.url)).first()
            if existing is None:
                existing = session.exec(
                    select(Job).where(Job.fingerprint == fp, Job.company_name == cname)
                ).first()

            sal = norm.parse_salary(j.salary_raw, j.description)
            # internship -> PPO is the route most target companies hire freshers by, so
            # the stipend and the conversion package are read at scrape time and become
            # the evidence company grading runs on (jobhunter/targeting.py).
            pay = norm.parse_pay(j.salary_raw, j.description)
            intern = norm.is_internship(j.title, j.description)

            if existing:
                # refresh volatile fields; never clobber scoring work
                existing.title = j.title
                existing.location = j.location or existing.location
                existing.remote = j.remote or existing.remote
                if len(j.description or "") > len(existing.description or ""):
                    existing.description = j.description
                if sal.ok() and existing.salary_min_lpa is None:
                    existing.salary_min_lpa = sal.min_lpa
                    existing.salary_max_lpa = sal.max_lpa
                    existing.currency = sal.currency
                    existing.salary_raw = sal.raw
                    existing.salary_extracted = True
                existing.company_id = existing.company_id or company.id
                existing.is_internship = intern
                existing.is_senior = norm.is_senior(j.title)
                existing.remote_anywhere = existing.remote_anywhere or norm.remote_anywhere(j.location, j.description)
                existing.stipend_inr_month = existing.stipend_inr_month or pay.stipend_inr_month
                existing.ppo_lpa = existing.ppo_lpa or pay.ppo_lpa
                session.add(existing)
                stats.updated += 1
                continue

            session.add(
                Job(
                    company_id=company.id,
                    company_name=cname,
                    title=j.title,
                    location=j.location,
                    remote=j.remote,
                    url=j.url,
                    fingerprint=fp,
                    source=j.source,
                    description=j.description,
                    posted_at=j.posted_at,
                    salary_min_lpa=sal.min_lpa,
                    salary_max_lpa=sal.max_lpa,
                    salary_raw=sal.raw or j.salary_raw,
                    currency=sal.currency,
                    salary_extracted=sal.ok(),
                    is_internship=intern,
                    is_senior=norm.is_senior(j.title),
                    remote_anywhere=norm.remote_anywhere(j.location, j.description),
                    stipend_inr_month=pay.stipend_inr_month,
                    ppo_lpa=pay.ppo_lpa,
                )
            )
            stats.inserted += 1
        session.commit()

    log.info(
        "persist: %d kept of %d scraped -> %d new, %d refreshed",
        stats.kept, stats.scraped, stats.inserted, stats.updated,
    )
    return stats


def scrape(sources: list[str] | None = None) -> ScrapeStats:
    sync_companies()
    jobs, stats = asyncio.run(scrape_async(sources))
    return persist(jobs, stats)


# ---------------------------------------------------------------- LLM salary backfill

def extract_salaries(limit: int = 60) -> int:
    """LLM pass over jobs whose salary regex came up empty but that mention pay."""
    init_db()
    filled = 0
    with get_session() as session:
        rows = session.exec(
            select(Job)
            .where(Job.salary_extracted == False)  # noqa: E712 — SQLModel needs the comparison
            .order_by(Job.scraped_at.desc())
            .limit(limit)
        ).all()

        for job in rows:
            sal = norm.llm_salary(job.title, job.company_name, job.location, job.description)
            job.salary_extracted = True  # don't retry a posting that genuinely states no pay
            if sal.ok():
                job.salary_min_lpa = sal.min_lpa
                job.salary_max_lpa = sal.max_lpa
                job.currency = sal.currency
                job.salary_raw = sal.raw
                filled += 1
            session.add(job)
        session.commit()
    log.info("salary backfill: %d of %d postings resolved", filled, len(rows))
    return filled
