"""Reddit reading — what people actually say about a company.

Be honest about this one. Agent Reach's own Reddit channel documents that there is
**no zero-config path**: anonymous `.json` endpoints are blocked, and Reddit closed
self-service API registration. Every working backend rides a logged-in session:

    rdt      pipx install rdt-cli, then `rdt login`   (cookie from your browser)
    opencli  desktop only, reuses an existing Chrome session

So this module's normal answer on a fresh machine is "unavailable, here is how to
enable it" — not an empty list pretending nothing was said.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from jobhunter.research import backends, cache
from jobhunter.research.models import RedditPost

log = logging.getLogger(__name__)


def _as_posts(data: Any) -> list[RedditPost]:
    """Map whatever the backend returned onto RedditPost.

    The two CLIs disagree on envelope shape and key names, and both change; this
    walks for the first list of post-shaped dicts and reads keys leniently.
    """
    items: list[dict] = []
    if isinstance(data, list):
        items = [i for i in data if isinstance(i, dict)]
    elif isinstance(data, dict):
        if data.get("ok") is False:
            return []
        for key in ("data", "results", "posts", "items", "children"):
            value = data.get(key)
            if isinstance(value, list):
                items = [i for i in value if isinstance(i, dict)]
                break
            if isinstance(value, dict):
                for inner in ("posts", "results", "items", "children"):
                    if isinstance(value.get(inner), list):
                        items = [i for i in value[inner] if isinstance(i, dict)]
                        break
                if items:
                    break

    posts: list[RedditPost] = []
    for raw in items:
        item = raw.get("data") if isinstance(raw.get("data"), dict) else raw
        title = item.get("title") or item.get("name") or ""
        permalink = item.get("url") or item.get("permalink") or item.get("link") or ""
        if permalink.startswith("/"):
            permalink = "https://www.reddit.com" + permalink
        if not title or not permalink:
            continue
        posts.append(
            RedditPost(
                title=str(title)[:300],
                url=permalink,
                subreddit=item.get("subreddit") or item.get("sub") or None,
                author=item.get("author") or None,
                score=int(item.get("score") or item.get("ups") or 0),
                num_comments=int(item.get("num_comments") or item.get("comments") or 0),
                created=str(item.get("created_utc") or item.get("created") or "") or None,
                text=(item.get("selftext") or item.get("text") or "")[:1500] or None,
            )
        )
    return posts


def _parse(payload: str) -> list[RedditPost]:
    payload = (payload or "").strip()
    if not payload:
        return []
    try:
        return _as_posts(json.loads(payload))
    except json.JSONDecodeError:
        pass
    # rdt prints YAML for some subcommands; parse it only if PyYAML is already loaded
    try:
        import yaml

        return _as_posts(yaml.safe_load(payload))
    except Exception:  # noqa: BLE001
        return []


def search(
    query: str,
    limit: int = 10,
    *,
    subreddit: str | None = None,
    sort: str = "relevance",
    fresh: bool = False,
    timeout: int | None = None,
) -> dict:
    """Search Reddit posts. Returns {query, backend, results, error?, hint?}."""
    query = (query or "").strip()
    if not query:
        return {"query": query, "results": [], "error": "empty query"}

    key = f"{subreddit or '*'}:{sort}:{query}"
    if not fresh:
        hit = cache.get("reddit", key, limit=limit)
        if hit:
            return hit

    usable = backends.candidates("reddit")
    if not usable:
        return {
            "query": query,
            "backend": None,
            "results": [],
            "error": "no Reddit backend installed",
            "hint": (
                "Reddit has no anonymous path. Install one: "
                f"{backends.HINTS['rdt']} — or, on a desktop, {backends.HINTS['opencli']}"
            ),
        }

    tried: list[str] = []
    failures: list[str] = []
    for backend in usable:
        tried.append(backend)
        if backend == "rdt":
            args = ["search", query, "--json", "-n", str(limit), "-s", sort]
            if subreddit:
                args += ["-r", subreddit]
            p = backends.run("rdt", args, timeout=timeout)
        else:  # opencli
            args = ["reddit", "search", query, "-f", "json"]
            if subreddit:
                args += ["-r", subreddit]
            p = backends.run("opencli", args, timeout=timeout)

        payload = p.out if p.ok else (p.out or p.err)
        posts = _parse(payload)
        if posts:
            out = {
                "query": query,
                "subreddit": subreddit,
                "backend": backend,
                "tried": tried,
                "results": [x.as_dict() for x in posts[:limit]],
            }
            cache.put("reddit", key, out, backend=backend, limit=limit)
            return out
        failures.append(f"{backend}: {_reason(payload, p)}")

    return {
        "query": query,
        "backend": None,
        "tried": tried,
        "results": [],
        "error": "; ".join(failures) or "no Reddit backend returned results",
        "hint": "the installed backend is not logged in — run `rdt login` (Reddit requires a session)",
    }


def _reason(payload: str, proc: backends.Proc) -> str:
    """One short line about why a backend gave nothing — auth is the usual answer."""
    blob = (payload or "")[:400].lower()
    if "forbidden" in blob or "403" in blob or "unauthor" in blob or "login" in blob:
        return "not authenticated"
    if proc.reason == "timeout":
        return "timed out"
    if proc.reason == "missing":
        return "not installed"
    return (proc.err or "no results").strip().splitlines()[0][:120] if (proc.err or "").strip() else "no results"


def status() -> dict:
    """Is a Reddit backend actually usable right now? Cheap, read-only."""
    if not backends.resolve("rdt"):
        return {"authenticated": False, "backend": None, "hint": backends.HINTS["rdt"]}
    p = backends.run("rdt", ["status"], timeout=30)
    blob = (p.out or "") + (p.err or "")
    authed = '"authenticated": !!bool "true"' in blob or '"authenticated": true' in blob.lower()
    return {
        "authenticated": authed,
        "backend": "rdt",
        "hint": "" if authed else "run `rdt login` to import a Reddit session cookie from your browser",
    }
