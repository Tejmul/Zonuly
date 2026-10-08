"""Company website mining — team/about/careers pages for names and public emails."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import httpx

from jobhunter.scrapers.base import get_text

log = logging.getLogger(__name__)

CANDIDATE_PATHS = [
    "", "/about", "/about-us", "/team", "/our-team", "/people", "/company",
    "/careers", "/jobs", "/contact", "/contact-us", "/leadership",
]

_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
# addresses that exist on every marketing site and reach no human
_GENERIC = re.compile(
    r"^(info|hello|hi|contact|support|sales|admin|team|press|media|legal|privacy|security|"
    r"noreply|no-reply|donotreply|help|billing|abuse|webmaster|postmaster|marketing)@",
    re.I,
)
_ASSET = re.compile(r"\.(png|jpe?g|gif|svg|webp|css|js|woff2?|ico)$", re.I)
_RECRUIT = re.compile(r"^(careers?|jobs|recruit(ing|ment)?|hiring|talent|hr|people)@", re.I)


@dataclass
class SiteContact:
    email: str
    name: str | None = None
    role: str | None = None
    source: str = "site"
    confidence: str = "scraped"
    is_recruiter: bool = False


def _same_domain(email: str, domain: str) -> bool:
    return email.split("@")[-1].lower().endswith(domain.lower())


async def scrape(http: httpx.AsyncClient, website: str, domain: str | None = None) -> list[SiteContact]:
    """Walk a handful of likely pages and pull same-domain email addresses."""
    if not website:
        return []
    base = website.rstrip("/")
    if not base.startswith("http"):
        base = f"https://{base}"
    domain = domain or base.split("//")[-1].split("/")[0].removeprefix("www.")

    found: dict[str, SiteContact] = {}
    for path in CANDIDATE_PATHS:
        html = await get_text(http, base + path)
        if not html:
            continue
        for raw in set(_EMAIL_RE.findall(html)):
            email = raw.lower().strip(".")
            if _ASSET.search(email) or not _same_domain(email, domain):
                continue
            if email in found:
                continue
            recruiter = bool(_RECRUIT.match(email))
            if _GENERIC.match(email) and not recruiter:
                continue  # generic inbox that isn't even a careers alias
            found[email] = SiteContact(
                email=email,
                name=_name_near(html, raw),
                role="Recruiting" if recruiter else None,
                is_recruiter=recruiter,
            )
        if len(found) >= 12:
            break

    log.info("site: %s -> %d contacts", domain, len(found))
    return list(found.values())


_NAME_RE = re.compile(r"\b([A-Z][a-z]{1,15})\s+([A-Z][a-z]{1,20})\b")


def _name_near(html: str, email: str, window: int = 300) -> str | None:
    """Best-effort: a Firstname Lastname appearing just before the address."""
    idx = html.find(email)
    if idx == -1:
        return None
    chunk = re.sub(r"<[^>]+>", " ", html[max(0, idx - window) : idx])
    matches = _NAME_RE.findall(chunk)
    if not matches:
        return None
    first, last = matches[-1]
    if first.lower() in {"the", "our", "we", "contact", "email", "get", "all", "read", "learn"}:
        return None
    return f"{first} {last}"


TEAM_PATHS = ["/team", "/about", "/about-us", "/people", "/company"]


def team_from_jina(website: str, domain: str | None = None, *, max_pages: int = 3) -> list[tuple[SiteContact, str]]:
    """The team / about pages read through Jina Reader (the research layer's page read —
    a JS-rendered page the direct fetch sees empty). (contact, page url) for every named
    address on the company's own domain. A shared inbox is dropped; a name is only what
    sits next to the address."""
    from jobhunter.research import web

    if not website:
        return []
    base = website.rstrip("/")
    if not base.startswith("http"):
        base = f"https://{base}"
    domain = domain or base.split("//")[-1].split("/")[0].removeprefix("www.")
    found: dict[str, tuple[SiteContact, str]] = {}
    read = 0
    for path in TEAM_PATHS:
        if read >= max_pages:
            break
        url = base + path
        page = web.read(url, prefer="jina", timeout=40)
        text = page.get("text") or ""
        if not text or page.get("error"):
            continue
        read += 1
        for raw in set(_EMAIL_RE.findall(text)):
            email = raw.lower().strip(".")
            if _ASSET.search(email) or not _same_domain(email, domain) or email in found:
                continue
            if _GENERIC.match(email) or _RECRUIT.match(email):
                continue      # an inbox reaches the company, not a referrer
            name = _name_near(text, raw)
            if not name:
                continue
            found[email] = (SiteContact(email=email, name=name, source="site"), url)
    return list(found.values())
