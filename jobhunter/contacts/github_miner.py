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
from datetime import datetime

import httpx

from jobhunter import CONFIG, channels, secret

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


_resolved: str | None = None


def _gh_cli_token() -> str:
    """The token the authenticated GitHub CLI holds (`gh auth token`), or ""."""
    import subprocess

    from jobhunter.research import backends

    exe = backends.resolve("gh")
    if not exe:
        return ""
    import os

    # gh prefers GITHUB_TOKEN / GH_TOKEN from the environment over its own login — and the
    # environment's is the token being replaced, so it is hidden from this one call
    env = {k: v for k, v in os.environ.items() if k not in ("GITHUB_TOKEN", "GH_TOKEN")}
    try:
        r = subprocess.run([exe, "auth", "token"], capture_output=True, text=True, timeout=15, env=env)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:  # noqa: BLE001
        return ""


def token() -> str:
    """A token GitHub actually accepts: GITHUB_TOKEN from .env, else the GitHub CLI's.

    2026-10-07: the .env token answered 401 "Bad credentials" on every call, so every
    people hunt had been running unauthenticated-and-refused ("no org found") while `gh`
    sat logged in with 5,000 calls an hour. Checked once per process, against /rate_limit
    (which costs nothing); a refused token is recorded on the github channel."""
    global _resolved
    if _resolved is not None:
        return _resolved
    # the personal `gh` login is never borrowed for mining unless the operator says so:
    # bulk collection through someone's own account is how an account gets flagged
    personal = bool((CONFIG.get("research") or {}).get("use_personal_github_login", False))
    creds = [("GITHUB_TOKEN", TOKEN)] + ([("gh auth token", _gh_cli_token())] if personal else [])
    for name, cand in creds:
        if not cand:
            continue
        try:
            r = httpx.get(f"{API}/rate_limit", timeout=15,
                          headers={"Accept": "application/vnd.github+json", "Authorization": f"Bearer {cand}"})
        except httpx.HTTPError:
            _resolved = cand        # offline: keep the first one, the calls will say so
            return cand
        if r.status_code == 200:
            _resolved = cand
            return cand
        channels.record("github", False, f"{name} refused: HTTP {r.status_code}")
        log.warning("github: %s refused (HTTP %s) — trying the next credential", name, r.status_code)
    _resolved = ""
    return ""


def headers() -> dict[str, str]:
    h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    tok = token()
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return h


@dataclass
class GHPerson:
    login: str
    name: str | None = None
    email: str | None = None
    bio: str | None = None
    company: str | None = None
    blog: str | None = None
    twitter: str | None = None     # the X handle on their profile — a DM route, not an address
    source: str = "github"
    confidence: str = "scraped"
    # affiliation evidence (guardrails.affiliation): a commit is not employment, these are
    member: bool = False                     # listed in the org's (public) members
    last_commit_at: datetime | None = None   # their latest commit to the org's own non-fork repos
    commit_repo: str | None = None
    profile_email: bool = False              # the address is the public one on their profile


async def get_json(http: httpx.AsyncClient, url: str, **kw) -> object | None:
    """GET → JSON or None, recording the GitHub channel's health (a 404 is an answer;
    403/429/5xx and transport errors are failures — guardrails' hourly audit reads them)."""
    try:
        r = await http.get(url, **kw)
    except Exception as e:  # noqa: BLE001 — one dead call must not end a hunt
        channels.record("github", False, f"{type(e).__name__}: {str(e)[:120]}")
        return None
    if r.status_code in (401, 403, 429) or r.status_code >= 500:
        channels.record("github", False, f"HTTP {r.status_code}: {r.text[:120]}")
        return None
    channels.record("github", True)
    if r.status_code >= 400:
        return None
    try:
        return r.json()
    except ValueError:
        return None


def _when(raw: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat((raw or "").replace("Z", "+00:00")).replace(tzinfo=None) if raw else None
    except ValueError:
        return None


_DECOR = re.compile(r"(ai|labs?|hq|inc|tech|technologies|app|org|engg|eng|git|github|oss|dev)$")


def _norm(text: str | None) -> str:
    """A name with its decoration stripped: "Edubuk-org", "Swish HQ" and "Sarvam AI"
    become edubuk, swish, sarvam — the part that has to agree."""
    stem = re.sub(r"[^a-z0-9]", "", (text or "").lower())
    bare = _DECOR.sub("", stem)
    return bare if len(bare) >= 3 else stem


def _host(url: str | None) -> str:
    return (url or "").replace("https://", "").replace("http://", "").split("/")[0].removeprefix("www.")


def _sld(host: str) -> str:
    """The registered label: new.turbohire.co → turbohire, vahan.ai → vahan."""
    parts = host.split(".")
    return parts[-2] if len(parts) >= 2 else host


def _same(company_name: str, website: str | None, *, login: str | None = None,
          org_name: str | None = None, blog: str | None = None) -> str | None:
    """Why this org is the company's — or None. Exact on the normalized name, or the
    same registered domain; never "contains", which is how Equal AI became moeru-ai
    and then equalcollective."""
    want = _norm(company_name)
    if login and _norm(login) == want:
        return "login is the company name"
    if org_name and _norm(org_name) == want:
        return f"org name is {org_name!r}"
    host, site = _host(website), _host(blog)
    if host and site and (site == host or (len(_sld(host)) >= 4 and _sld(site) == _sld(host))):
        return f"org website is {site}"
    return None


async def find_org(http: httpx.AsyncClient, company_name: str, website: str | None = None) -> str | None:
    """Resolve a company name to its GitHub org login — or to nothing.

    An org is theirs when its login or display name is the company's name (decoration
    aside), or its website is the company's domain. A search hit that passes neither
    is a stranger: "Equal AI" used to come back as moeru-ai, and twenty of its
    committers were filed as Equal's engineers. No match means no org, never the
    first result.
    """
    q = company_name.replace(" ", "+")
    data = await get_json(http, f"{API}/search/users?q={q}+type:org&per_page=5", headers=headers())
    items = (data or {}).get("items") or []
    for it in items:
        if _same(company_name, website, login=it.get("login")):
            return it["login"]
    for it in items:
        org = await get_json(http, f"{API}/orgs/{it['login']}", headers=headers())
        if org and _same(company_name, website, org_name=org.get("name"), blog=org.get("blog")):
            return it["login"]
    return None


async def org_is_theirs(http: httpx.AsyncClient, company_name: str, website: str | None, org: str) -> tuple[bool, str]:
    """The same test find_org applies, for an org already on record — the audit path."""
    why = _same(company_name, website, login=org)
    if why:
        return True, why
    data = await get_json(http, f"{API}/orgs/{org}", headers=headers())
    if not data:
        return False, "org not found on GitHub"
    why = _same(company_name, website, org_name=data.get("name"), blog=data.get("blog"))
    if why:
        return True, why
    return False, f"org is {data.get('name')!r} ({_host(data.get('blog')) or 'no website'}) — not {company_name}"


async def top_repos(http: httpx.AsyncClient, org: str, limit: int = REPOS_PER_ORG) -> list[str]:
    repos = await get_json(
        http, f"{API}/orgs/{org}/repos?sort=pushed&per_page={limit}&type=public", headers=headers()
    )
    # a fork's history is upstream's committers, not the company's (Affirm's Flink fork)
    return [r["name"] for r in (repos or []) if r.get("name") and not r.get("archived") and not r.get("fork")]


async def emails_from_repo(http: httpx.AsyncClient, org: str, repo: str) -> dict[str, tuple[str, str | None, datetime | None]]:
    """{login: (email, name, when)} harvested from one repo's recent commits."""
    commits = await get_json(
        http, f"{API}/repos/{org}/{repo}/commits?per_page={COMMITS_PER_REPO}", headers=headers()
    )
    found: dict[str, tuple[str, str | None, datetime | None]] = {}
    for c in commits or []:
        author = (c.get("author") or {})
        login = author.get("login")
        meta = ((c.get("commit") or {}).get("author") or {})
        email, name = meta.get("email"), meta.get("name")
        if not login or not email:
            continue
        if _NOREPLY.search(email) or _BOT.search(login):
            continue
        found.setdefault(login, (email, name, _when(meta.get("date"))))   # newest first
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
        twitter=(u.get("twitter_username") or "").lstrip("@") or None,
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

    # 1. harvest emails in bulk from recent commits across the org's own active repos
    harvested: dict[str, tuple[str, str | None, datetime | None]] = {}
    repo_of: dict[str, str] = {}
    for repo in await top_repos(http, org):
        for login, row in (await emails_from_repo(http, org, repo)).items():
            old = harvested.get(login)
            if old is None or (row[2] and (not old[2] or row[2] > old[2])):
                harvested[login] = row
                repo_of[login] = f"{org}/{repo}"
        if len(harvested) >= limit * 2:
            break

    # 2. org members fill in people who haven't committed recently
    members = await org_members(http, org, limit=limit)

    people: dict[str, GHPerson] = {}
    for login, (email, name, when) in harvested.items():
        people[login] = GHPerson(login=login, name=name, email=email, last_commit_at=when,
                                 commit_repo=repo_of.get(login))
    for login in members:
        people.setdefault(login, GHPerson(login=login)).member = True

    ordered = sorted(people.values(), key=lambda p: p.email is None)[:limit]

    # 3. enrich the ones we're most likely to write to — bio gives us their role. Without a
    # token (60 calls an hour) only the first few: the budget is for addresses, not bios
    for person in ordered[:PROFILES_TO_ENRICH if token() else 4]:
        profile = await user_profile(http, person.login)
        if profile is None:
            continue
        person.bio = profile.bio
        person.company = profile.company
        person.blog = profile.blog
        person.twitter = profile.twitter
        if profile.name:
            person.name = profile.name      # their display name beats the commit's author string
        if not person.email and profile.email:
            person.email = profile.email
            person.profile_email = True
        elif person.email and profile.email and person.email.lower() == profile.email.lower():
            person.profile_email = True

    found = sum(1 for p in ordered if p.email)
    log.info("github: %s (org=%s) -> %d people, %d with emails", company_name, org, len(ordered), found)
    return org, ordered


_LEFT: dict = {"at": 0.0, "remaining": None, "limit": None, "reset": None}


def calls_left(max_age_s: int = 60) -> dict:
    """GitHub's core budget right now — {remaining, limit, reset, authenticated}. The
    /rate_limit call itself costs nothing; cached for a minute. Unauthenticated (no
    separate-account GITHUB_TOKEN) the limit is 60 an hour, so the people step mines a
    company only while enough calls are left (tick.github_min_remaining)."""
    import time as _t

    if _LEFT["at"] and _t.time() - _LEFT["at"] < max_age_s:
        return {k: _LEFT[k] for k in ("remaining", "limit", "reset")} | {"authenticated": bool(token())}
    try:
        r = httpx.get(f"{API}/rate_limit", headers=headers(), timeout=15)
        core = (r.json().get("resources") or {}).get("core") or {}
        _LEFT.update(at=_t.time(), remaining=core.get("remaining"), limit=core.get("limit"), reset=core.get("reset"))
    except Exception as e:  # noqa: BLE001 — unknown budget: let the call path find out
        log.debug("github rate_limit unreadable: %s", e)
        _LEFT.update(at=_t.time(), remaining=None, limit=None, reset=None)
    return {k: _LEFT[k] for k in ("remaining", "limit", "reset")} | {"authenticated": bool(token())}


async def rate_limit(http: httpx.AsyncClient) -> dict:
    data = await get_json(http, f"{API}/rate_limit", headers=headers())
    core = ((data or {}).get("resources") or {}).get("core") or {}
    return {"limit": core.get("limit"), "remaining": core.get("remaining"), "authenticated": bool(token())}
