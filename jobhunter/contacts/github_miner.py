"""GitHub org mining — the best free source of *verified* engineer emails.

Public commit metadata carries the author's real email address. We read it from
the repo commits endpoint rather than per-user activity: GitHub's public events
API no longer includes commit details in PushEvent payloads, and one commits call
returns up to 100 commits covering dozens of engineers at once. That keeps a
whole company inside ~10 API calls, which matters a lot on the unauthenticated
60/hour budget.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import httpx

from jobhunter import CONFIG, secret
from jobhunter.scrapers.base import get_json

log = logging.getLogger(__name__)

API = "https://api.github.com"
# The environment (.env, gitignored) is the one door for credentials; the config
# key is kept only so an old config.yaml keeps working, and it should stay empty.
TOKEN = secret("GITHUB_TOKEN") or (CONFIG.get("contacts") or {}).get("github_token") or ""

# GitHub's privacy relay and bot accounts — never worth contacting
_NOREPLY = re.compile(r"(noreply|no-reply|users\.noreply\.github\.com|example\.com|localhost)", re.I)
_BOT = re.compile(r"(\[bot\]|-bot$|^bot-|dependabot|renovate|github-actions|semantic-release)", re.I)

REPOS_PER_ORG = 6
COMMITS_PER_REPO = 100
PROFILES_TO_ENRICH = 12   # bios cost one call each; only fetch them for people we'd actually write to


def headers() -> dict[str, str]:
    h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if TOKEN:
        h["Authorization"] = f"Bearer {TOKEN}"
    return h


@dataclass
class GHPerson:
    login: str
    name: str | None = None
    email: str | None = None
    bio: str | None = None
    company: str | None = None
    blog: str | None = None
    source: str = "github"
    confidence: str = "scraped"


async def find_org(http: httpx.AsyncClient, company_name: str, website: str | None = None) -> str | None:
    """Resolve a company name to its GitHub org login."""
    q = company_name.replace(" ", "+")
    data = await get_json(http, f"{API}/search/users?q={q}+type:org&per_page=5", headers=headers())
    items = (data or {}).get("items") or []
    if not items:
        return None

    host = (website or "").replace("https://", "").replace("http://", "").split("/")[0].removeprefix("www.")
    simple = re.sub(r"[^a-z0-9]", "", company_name.lower())

    for it in items:
        if re.sub(r"[^a-z0-9]", "", it.get("login", "").lower()) == simple:
            return it["login"]
    for it in items[:3]:
        org = await get_json(http, f"{API}/orgs/{it['login']}", headers=headers())
        if host and host in ((org or {}).get("blog") or ""):
            return it["login"]
    return items[0]["login"]


async def top_repos(http: httpx.AsyncClient, org: str, limit: int = REPOS_PER_ORG) -> list[str]:
    repos = await get_json(
        http, f"{API}/orgs/{org}/repos?sort=pushed&per_page={limit}&type=public", headers=headers()
    )
    return [r["name"] for r in (repos or []) if r.get("name") and not r.get("archived")]


async def emails_from_repo(http: httpx.AsyncClient, org: str, repo: str) -> dict[str, tuple[str, str | None]]:
    """{login: (email, name)} harvested from one repo's recent commits."""
    commits = await get_json(
        http, f"{API}/repos/{org}/{repo}/commits?per_page={COMMITS_PER_REPO}", headers=headers()
    )
    found: dict[str, tuple[str, str | None]] = {}
    for c in commits or []:
        author = (c.get("author") or {})
        login = author.get("login")
        meta = ((c.get("commit") or {}).get("author") or {})
        email, name = meta.get("email"), meta.get("name")
        if not login or not email:
            continue
        if _NOREPLY.search(email) or _BOT.search(login):
            continue
        found.setdefault(login, (email, name))
    return found


async def org_members(http: httpx.AsyncClient, org: str, limit: int = 30) -> list[str]:
    members = await get_json(http, f"{API}/orgs/{org}/members?per_page={limit}", headers=headers())
    return [m["login"] for m in (members or []) if m.get("login") and not _BOT.search(m["login"])]


async def user_profile(http: httpx.AsyncClient, login: str) -> GHPerson | None:
    u = await get_json(http, f"{API}/users/{login}", headers=headers())
    if not u or u.get("type") == "Organization":
        return None
    email = u.get("email")
    if email and _NOREPLY.search(email):
        email = None
    return GHPerson(
        login=login,
        name=u.get("name"),
        email=email,
        bio=u.get("bio"),
        company=u.get("company"),
        blog=u.get("blog") or None,
        confidence="verified" if email else "scraped",
    )


async def mine(
    http: httpx.AsyncClient,
    company_name: str,
    website: str | None = None,
    org: str | None = None,
    limit: int = 20,
) -> tuple[str | None, list[GHPerson]]:
    """Return (org_login, people). People with a verified email come first."""
    org = org or await find_org(http, company_name, website)
    if not org:
        log.info("github: no org found for %s", company_name)
        return None, []

    # 1. harvest emails in bulk from recent commits across the org's active repos
    harvested: dict[str, tuple[str, str | None]] = {}
    for repo in await top_repos(http, org):
        harvested.update(await emails_from_repo(http, org, repo))
        if len(harvested) >= limit * 2:
            break

    # 2. org members fill in people who haven't committed recently
    members = await org_members(http, org, limit=limit)

    people: dict[str, GHPerson] = {}
    for login, (email, name) in harvested.items():
        people[login] = GHPerson(login=login, name=name, email=email, confidence="verified")
    for login in members:
        people.setdefault(login, GHPerson(login=login))

    ordered = sorted(people.values(), key=lambda p: p.email is None)[:limit]

    # 3. enrich the ones we're most likely to write to — bio gives us their role
    for person in ordered[:PROFILES_TO_ENRICH]:
        profile = await user_profile(http, person.login)
        if profile is None:
            continue
        person.name = person.name or profile.name
        person.bio = profile.bio
        person.company = profile.company
        person.blog = profile.blog
        if not person.email and profile.email:
            person.email = profile.email
            person.confidence = "verified"

    found = sum(1 for p in ordered if p.email)
    log.info("github: %s (org=%s) -> %d people, %d with emails", company_name, org, len(ordered), found)
    return org, ordered


async def rate_limit(http: httpx.AsyncClient) -> dict:
    data = await get_json(http, f"{API}/rate_limit", headers=headers())
    core = ((data or {}).get("resources") or {}).get("core") or {}
    return {"limit": core.get("limit"), "remaining": core.get("remaining"), "authenticated": bool(TOKEN)}
