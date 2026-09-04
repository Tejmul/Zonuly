"""Adding one company by hand.

`companies.yaml` is not a display list — it is the set of company job boards the
scrapers crawl directly, and a company in it gets its whole Greenhouse/Lever/Ashby
board pulled on every cycle. Everything else only appears if it happens to post
somewhere public. That makes it the highest-leverage file in the project, and until
now the only way into it was a text editor and a restart.

What this does, given nothing but a name:

  1. finds the company's public job board by probing every plausible slug,
  2. writes the row, so the dashboard has it immediately,
  3. appends it to companies.yaml, so every future cycle crawls it too,
  4. pulls that one board straight away, so the roles are there before you look.

Step 3 appends text rather than round-tripping the YAML. `yaml.safe_dump` would
rewrite all 571 lines and throw away every comment in the file, including the
per-entry notes recording how many roles each board had when it was seeded. An
append is uglier code and a much better neighbour.
"""

from __future__ import annotations

import asyncio
import logging
import re

from sqlmodel import col, func, select

from jobhunter import ROOT
from jobhunter.db import Company, Job, get_session, init_db

log = logging.getLogger(__name__)

COMPANIES_PATH = ROOT / "companies.yaml"


class AddError(Exception):
    """Something the caller did, phrased for the caller."""


def _clean_website(website: str | None) -> str | None:
    if not website:
        return None
    site = website.strip()
    if not site:
        return None
    if not site.startswith(("http://", "https://")):
        site = f"https://{site}"
    return site


def _domain_of(url: str | None) -> str | None:
    if not url:
        return None
    host = url.split("//")[-1].split("/")[0].split("?")[0].lower()
    return host.removeprefix("www.") or None


def _existing(session, name: str, domain: str | None) -> Company | None:
    """Match on name case-insensitively, then on domain — the same company arrives
    spelled two ways often enough to be worth both checks."""
    row = session.exec(
        select(Company).where(func.lower(Company.name) == name.lower())
    ).first()
    if row or not domain:
        return row
    return session.exec(select(Company).where(Company.domain == domain)).first()


async def _find_board(name: str, website: str | None, hint: str | None):
    from jobhunter.scrapers.base import make_client
    from jobhunter.scrapers.discover import find_ats

    async with make_client() as http:
        return await find_ats(http, name, website, hint=hint)


def _verify_board(ats: str, slug: str):
    """Confirm a board the caller named, rather than guessing at one."""
    from jobhunter.scrapers.base import make_client
    from jobhunter.scrapers.discover import PROBES, probe

    if ats not in PROBES:
        raise AddError(f"Job boards this can read: {', '.join(PROBES)}.")

    async def go():
        async with make_client() as http:
            return await probe(http, ats, slug)

    try:
        return asyncio.run(go())
    except Exception as e:  # noqa: BLE001
        log.warning("board check failed for %s/%s: %s", ats, slug, e)
        return None


async def _scrape_board(entry: dict) -> int:
    """Pull this one board now. Reuses the normal ingest path, so a role added this
    way is indistinguishable from one the daily cycle found."""
    from jobhunter.pipeline import ScrapeStats, persist
    from jobhunter.scrapers import get_fetcher
    from jobhunter.scrapers.base import make_client

    fetch = get_fetcher(entry["ats"])
    if fetch is None:
        return 0
    async with make_client() as http:
        jobs = await fetch(http, [entry])
    stats = ScrapeStats()
    stats.scraped = len(jobs)
    stats.per_source[entry["ats"]] = len(jobs)
    persist(jobs, stats)
    return stats.inserted


def _already_in_yaml(text: str, name: str, slug: str | None) -> bool:
    if re.search(rf'^\s*-\s*name:\s*["\']?{re.escape(name)}["\']?\s*$', text, re.M | re.I):
        return True
    return bool(slug and re.search(rf'^\s*ats_slug:\s*["\']?{re.escape(slug)}["\']?\s*$', text, re.M))


def _append_to_yaml(entry: dict) -> bool:
    """Append one entry, preserving every comment already in the file."""
    if not COMPANIES_PATH.exists():
        return False
    text = COMPANIES_PATH.read_text(encoding="utf-8")
    if _already_in_yaml(text, entry["name"], entry.get("ats_slug")):
        return False

    def q(v: object) -> str:
        return '"' + str(v).replace('\\', '\\\\').replace('"', '\\"') + '"'

    lines = [f'  - name: {q(entry["name"])}']
    for key in ("website", "domain", "ats", "ats_slug"):
        if entry.get(key):
            lines.append(f"    {key}: {q(entry[key])}")
    lines.append("    tags: [added-by-hand]")

    if not text.endswith("\n"):
        text += "\n"
    COMPANIES_PATH.write_text(text + "\n".join(lines) + "\n", encoding="utf-8")
    return True


def add_company(
    name: str,
    website: str | None = None,
    *,
    ats_hint: str | None = None,
    ats_slug: str | None = None,
    scrape_now: bool = True,
) -> dict:
    """Add one company end to end. Safe to call twice — the second call reports
    what already exists rather than making a duplicate."""
    init_db()
    name = (name or "").strip()
    if len(name) < 2:
        raise AddError("Give the company a name.")
    if len(name) > 120:
        raise AddError("That name is too long to be a company name.")

    website = _clean_website(website)
    domain = _domain_of(website)

    with get_session() as session:
        row = _existing(session, name, domain)
        already = row is not None
        if row is None:
            row = Company(name=name)
        row.website = row.website or website
        row.domain = row.domain or domain

        # A company can already be in the database — pulled off a job posting — and
        # still have no board of its own. That is exactly the row worth upgrading.
        needs_board = not (row.ats and row.ats_slug)
        session.add(row)
        session.commit()
        session.refresh(row)
        company_id, current_ats, current_slug = row.id, row.ats, row.ats_slug

    board = None
    if ats_hint and ats_slug:
        # Told outright which board it is. Roughly half of companies are on an ATS
        # this cannot probe, or use a slug no rule would guess, so being able to say
        # so is the difference between the feature working and half-working.
        board = _verify_board(ats_hint.strip().lower(), ats_slug.strip())
        if board is None:
            raise AddError(
                f"No {ats_hint} board answers to '{ats_slug}'. Check the slug in the "
                "careers-page URL — it is the part after the provider's domain."
            )
    elif needs_board:
        try:
            board = asyncio.run(_find_board(name, website, ats_hint))
        except Exception as e:  # noqa: BLE001 — a probe failure is not a failure to add
            log.warning("board probe failed for %s: %s", name, e)

    if board:
        ats, slug, open_roles = board
        with get_session() as session:
            row = session.get(Company, company_id)
            if row is not None:
                row.ats, row.ats_slug = ats, slug
                session.add(row)
                session.commit()
        current_ats, current_slug = ats, slug
    else:
        open_roles = None

    entry = {
        "name": name,
        "website": website,
        "domain": domain,
        "ats": current_ats,
        "ats_slug": current_slug,
    }
    # Only a company with a board belongs in the seed file — the scrapers key off
    # ats_slug, so an entry without one would be read on every cycle and skipped.
    seeded = bool(current_ats and current_slug) and _append_to_yaml(entry)

    scraped = 0
    if scrape_now and current_ats and current_slug:
        try:
            scraped = asyncio.run(_scrape_board(entry))
        except Exception as e:  # noqa: BLE001
            log.warning("first scrape of %s failed: %s", name, e)

    with get_session() as session:
        roles = session.exec(
            select(func.count()).select_from(Job).where(col(Job.company_id) == company_id)
        ).one()

    return {
        "id": company_id,
        "name": name,
        "website": website,
        "already_existed": already,
        "board": {"ats": current_ats, "slug": current_slug, "open_roles": open_roles}
        if current_ats
        else None,
        "seeded_into_yaml": seeded,
        "roles_added": scraped,
        "roles_total": roles,
        "note": _note(already, bool(current_ats), scraped),
    }


def _note(already: bool, has_board: bool, scraped: int) -> str:
    """One sentence the dashboard can show verbatim."""
    if not has_board:
        return (
            "Added, but no public job board was found under any name worth guessing. It "
            "will still be graded and searched for people — its roles just cannot be "
            "pulled automatically. If you know the board, add it again with the provider "
            "and slug from its careers-page URL."
        )
    if scraped:
        return f"Board found and {scraped} new role{'s' if scraped != 1 else ''} pulled. It will be crawled on every cycle from now on."
    if already:
        return "Already known. Its board is now recorded, so future cycles will crawl it."
    return "Board found, but it has no postings open right now. Future cycles will pick them up when it does."
