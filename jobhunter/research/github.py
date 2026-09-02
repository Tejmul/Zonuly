"""GitHub reading — repos, orgs and people.

Two backends, in Agent Reach's preferred order: the `gh` CLI (carries the user's
own auth, so 5000 requests/hour and no token of ours in play), falling back to the
public REST API through httpx.

This is the *research* view of GitHub — what a company builds, in what language,
how recently. Finding e-mail addresses in commit metadata stays where it already
lives, in `contacts.github_miner`; nothing here duplicates it.
"""

from __future__ import annotations

import json
import logging

import httpx

from jobhunter.research import backends, cache
from jobhunter.research.models import RepoHit

log = logging.getLogger(__name__)

API = "https://api.github.com"

_GH_JSON_FIELDS = "fullName,description,language,stargazersCount,url,updatedAt,owner"


def _api_headers() -> dict[str, str]:
    h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    token = backends.github_token()
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _repo_from_gh(item: dict) -> RepoHit:
    owner = item.get("owner") or {}
    return RepoHit(
        full_name=item.get("fullName") or "",
        url=item.get("url") or "",
        description=(item.get("description") or "")[:400] or None,
        language=item.get("language") or None,
        stars=int(item.get("stargazersCount") or 0),
        pushed_at=item.get("updatedAt"),
        owner=owner.get("login") if isinstance(owner, dict) else None,
    )


def _repo_from_api(item: dict) -> RepoHit:
    owner = item.get("owner") or {}
    return RepoHit(
        full_name=item.get("full_name") or "",
        url=item.get("html_url") or "",
        description=(item.get("description") or "")[:400] or None,
        language=item.get("language") or None,
        stars=int(item.get("stargazers_count") or 0),
        pushed_at=item.get("pushed_at"),
        owner=owner.get("login") if isinstance(owner, dict) else None,
    )


def search_repos(query: str, limit: int = 10, *, fresh: bool = False, timeout: int | None = None) -> dict:
    """Search public repositories. {query, backend, results: [RepoHit], error?}"""
    query = (query or "").strip()
    if not query:
        return {"query": query, "backend": None, "results": [], "error": "empty query"}

    if not fresh:
        hit = cache.get("github", f"repos:{query}", limit=limit)
        if hit:
            return hit

    tried: list[str] = []
    for backend in backends.candidates("github"):
        tried.append(backend)
        repos: list[RepoHit] = []
        if backend == "gh-cli":
            p = backends.run(
                "gh",
                ["search", "repos", query, "--sort", "stars", "--limit", str(limit), "--json", _GH_JSON_FIELDS],
                timeout=timeout,
            )
            if p.ok:
                try:
                    repos = [_repo_from_gh(i) for i in json.loads(p.out or "[]")]
                except json.JSONDecodeError:
                    repos = []
            else:
                log.debug("gh search repos failed (%s): %s", p.reason, p.err[:200])
        elif backend == "github-api":
            repos = _api_search_repos(query, limit, timeout)

        if repos:
            out = {
                "query": query,
                "backend": backend,
                "tried": tried,
                "results": [r.as_dict() for r in repos if r.full_name],
            }
            cache.put("github", f"repos:{query}", out, backend=backend, limit=limit)
            return out

    return {
        "query": query,
        "backend": None,
        "tried": tried,
        "results": [],
        "error": "no GitHub backend returned results",
        "hint": backends.HINTS.get("gh-cli", ""),
    }


def _api_search_repos(query: str, limit: int, timeout: int | None) -> list[RepoHit]:
    try:
        r = httpx.get(
            f"{API}/search/repositories",
            params={"q": query, "sort": "stars", "per_page": min(limit, 50)},
            headers=_api_headers(),
            timeout=timeout or backends.TIMEOUT,
        )
        if r.status_code >= 400:
            log.debug("github api search -> %s", r.status_code)
            return []
        return [_repo_from_api(i) for i in (r.json() or {}).get("items", [])]
    except Exception as e:  # noqa: BLE001
        log.debug("github api search failed: %s", e)
        return []


def find_org(company: str, *, website: str | None = None, timeout: int | None = None) -> str | None:
    """Best guess at a company's GitHub org login, or None.

    Deliberately conservative: an org whose name or blog does not corroborate the
    company is not returned at all, because a wrong org poisons every downstream
    signal (repos, languages, later contact mining).
    """
    company = (company or "").strip()
    if not company:
        return None
    needle = "".join(ch for ch in company.lower() if ch.isalnum())
    domain = None
    if website:
        domain = website.split("//")[-1].split("/")[0].removeprefix("www.").lower()

    candidates: list[dict] = []
    if "gh-cli" in backends.candidates("github"):
        p = backends.run(
            "gh",
            ["api", f"/search/users?q={company.replace(' ', '+')}+type:org&per_page=5"],
            timeout=timeout,
        )
        if p.ok:
            try:
                candidates = (json.loads(p.out or "{}") or {}).get("items") or []
            except json.JSONDecodeError:
                candidates = []
    if not candidates:
        try:
            r = httpx.get(
                f"{API}/search/users",
                params={"q": f"{company} type:org", "per_page": 5},
                headers=_api_headers(),
                timeout=timeout or backends.TIMEOUT,
            )
            candidates = (r.json() or {}).get("items", []) if r.status_code < 400 else []
        except Exception as e:  # noqa: BLE001
            log.debug("github org lookup failed: %s", e)
            return None

    for item in candidates:
        login = (item.get("login") or "").lower()
        if not login:
            continue
        stem = "".join(ch for ch in login if ch.isalnum())
        # exact, or the company name plus a short suffix — orgs are routinely
        # pluralised or given an -hq/-ai tail ("Anthropic" -> "anthropics")
        if stem == needle or (needle and stem.startswith(needle) and len(stem) - len(needle) <= 2):
            return item["login"]
    if domain:
        for item in candidates:
            blog = (item.get("blog") or "").lower()
            if domain and domain in blog:
                return item["login"]
    return None


def org_repos(org: str, limit: int = 8, *, fresh: bool = False, timeout: int | None = None) -> dict:
    """An org's most recently pushed public repos."""
    org = (org or "").strip().rstrip("/").split("/")[-1]
    if not org:
        return {"org": org, "results": [], "error": "empty org"}

    if not fresh:
        hit = cache.get("github", f"org:{org}", limit=limit)
        if hit:
            return hit

    repos: list[RepoHit] = []
    backend = None
    if "gh-cli" in backends.candidates("github"):
        p = backends.run(
            "gh", ["api", f"/orgs/{org}/repos?sort=pushed&per_page={min(limit, 50)}"], timeout=timeout
        )
        if p.ok:
            try:
                repos = [_repo_from_api(i) for i in json.loads(p.out or "[]")]
                backend = "gh-cli"
            except json.JSONDecodeError:
                repos = []
    if not repos:
        try:
            r = httpx.get(
                f"{API}/orgs/{org}/repos",
                params={"sort": "pushed", "per_page": min(limit, 50)},
                headers=_api_headers(),
                timeout=timeout or backends.TIMEOUT,
            )
            if r.status_code < 400:
                repos = [_repo_from_api(i) for i in r.json() or []]
                backend = "github-api"
        except Exception as e:  # noqa: BLE001
            log.debug("org repos failed for %s: %s", org, e)

    out = {
        "org": org,
        "backend": backend,
        "results": [r.as_dict() for r in repos[:limit] if r.full_name],
    }
    if not repos:
        out["error"] = f"no public repos found for org {org}"
    else:
        cache.put("github", f"org:{org}", out, backend=backend or "", limit=limit)
    return out


def search_users(query: str, limit: int = 10, *, timeout: int | None = None) -> dict:
    """Search GitHub people — the raw hits, not contact records.

    Turning a person into a contact row (verification, e-mail patterns, the
    recruiter flag) is `contacts.github_miner`'s job and stays there.
    """
    query = (query or "").strip()
    if not query:
        return {"query": query, "results": [], "error": "empty query"}

    items: list[dict] = []
    backend = None
    if "gh-cli" in backends.candidates("github"):
        p = backends.run(
            "gh", ["api", f"/search/users?q={query.replace(' ', '+')}&per_page={min(limit, 50)}"], timeout=timeout
        )
        if p.ok:
            try:
                items = (json.loads(p.out or "{}") or {}).get("items") or []
                backend = "gh-cli"
            except json.JSONDecodeError:
                items = []
    if not items:
        try:
            r = httpx.get(
                f"{API}/search/users",
                params={"q": query, "per_page": min(limit, 50)},
                headers=_api_headers(),
                timeout=timeout or backends.TIMEOUT,
            )
            if r.status_code < 400:
                items = (r.json() or {}).get("items", [])
                backend = "github-api"
        except Exception as e:  # noqa: BLE001
            log.debug("github user search failed: %s", e)

    return {
        "query": query,
        "backend": backend,
        "results": [
            {"login": i.get("login"), "url": i.get("html_url"), "type": i.get("type")}
            for i in items[:limit]
            if i.get("login")
        ],
    }
