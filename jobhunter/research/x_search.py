"""X search through a throwaway session — the one login-gated read this project allows.

X's search page is a shell; the posts arrive in a `SearchTimeline` GraphQL response.
So we drive one headless page with the throwaway account's cookies, and read that
JSON off the wire instead of parsing volatile HTML. Nothing is typed, clicked, liked,
followed or posted — the session only *reads*, and only search.

Why this is allowed when LinkedIn is not (MOTIV §6, `constraint:no-linkedin-automation`):
the account is a throwaway created for this, holds nothing we need, and is never the
account we would write to anyone from. A suspension is reported and the step stops;
it is never retried with a new account.

Limits, all in config.yaml `research:` — x_daily_searches (counted before the call,
like Exa), x_min_gap_seconds between searches, and the session cookies come only from
the environment (X_AUTH_TOKEN, X_CT0), never from config or argv.

Records only: {url, handle, name, text, published, post_id}. They are *claims* — a
post saying "we're hiring" — and hiring_verify decides against the company's own site.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

from jobhunter import CONFIG, secret
from jobhunter.research import cache

log = logging.getLogger(__name__)

_R = CONFIG.get("research") or {}
DAILY_CAP = int(_R.get("x_daily_searches", 10))
MIN_GAP_S = float(_R.get("x_min_gap_seconds", 30))
_USED_KEY = "x_search_used"       # "YYYY-MM-DD:count"
_last_call = 0.0

UA = (CONFIG.get("sources") or {}).get(
    "user_agent",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36",
)

_LOGIN_WALL = re.compile(r"(log in|sign up|suspended|something went wrong)", re.I)


def session_present() -> bool:
    return bool(secret("X_AUTH_TOKEN") and secret("X_CT0"))


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def budget() -> dict:
    """{cap, used, left} for today; everything 0 without a session."""
    if not session_present():
        return {"cap": 0, "used": 0, "left": 0}
    from jobhunter.db import get_session, get_setting

    with get_session() as s:
        raw = get_setting(s, _USED_KEY, "")
    day, _, count = raw.partition(":")
    used = int(count) if day == _today() and count.isdigit() else 0
    return {"cap": DAILY_CAP, "used": used, "left": max(0, DAILY_CAP - used)}


def _spend() -> None:
    from jobhunter.db import get_session, get_setting, set_setting

    with get_session() as s:
        raw = get_setting(s, _USED_KEY, "")
        day, _, count = raw.partition(":")
        used = int(count) if day == _today() and count.isdigit() else 0
        set_setting(s, _USED_KEY, f"{_today()}:{used + 1}")


def _walk_tweets(obj: Any):
    """Every Tweet object anywhere in a SearchTimeline payload."""
    if isinstance(obj, dict):
        if obj.get("__typename") == "Tweet" and isinstance(obj.get("legacy"), dict):
            yield obj
        for v in obj.values():
            yield from _walk_tweets(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_tweets(v)


def _record(t: dict) -> dict | None:
    legacy = t.get("legacy") or {}
    user = (((t.get("core") or {}).get("user_results") or {}).get("result") or {})
    ulegacy = user.get("legacy") or {}
    handle = ulegacy.get("screen_name") or ((user.get("core") or {}).get("screen_name"))
    post_id = t.get("rest_id") or legacy.get("id_str")
    if not handle or not post_id:
        return None
    text = ((t.get("note_tweet") or {}).get("note_tweet_results") or {}).get("result", {}).get("text") \
        or legacy.get("full_text") or ""
    return {
        "url": f"https://x.com/{handle}/status/{post_id}",
        "post_id": str(post_id),
        "handle": handle,
        "name": ulegacy.get("name") or (user.get("core") or {}).get("name"),
        "text": text[:2000] or None,
        "published": legacy.get("created_at"),
        "source": "x",
    }


def search(query: str, limit: int = 20, *, fresh: bool = False, timeout_s: int = 60) -> dict:
    """Latest posts matching `query`. Returns {query, posts, backend, error?}."""
    query = (query or "").strip()
    if not query:
        return {"query": query, "posts": [], "backend": None, "error": "empty query"}
    if not session_present():
        return {"query": query, "posts": [], "backend": None,
                "error": "no X session — set X_AUTH_TOKEN and X_CT0 in .env (throwaway account only)"}
    if not fresh:
        hit = cache.get("x-search", query, limit=limit)
        if hit:
            return hit
    b = budget()
    if b["left"] <= 0:
        return {"query": query, "posts": [], "backend": None,
                "error": f"x: daily cap of {b['cap']} searches reached (research.x_daily_searches)"}

    global _last_call
    wait = MIN_GAP_S - (time.monotonic() - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.monotonic()
    _spend()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"query": query, "posts": [], "backend": None,
                "error": "playwright not installed — uv sync, then `playwright install chromium`"}

    payloads: list[Any] = []
    title = landed = ""
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(user_agent=UA, viewport={"width": 1280, "height": 900})
            ctx.add_cookies([
                {"name": "auth_token", "value": secret("X_AUTH_TOKEN"), "domain": ".x.com",
                 "path": "/", "secure": True, "httpOnly": True},
                {"name": "ct0", "value": secret("X_CT0"), "domain": ".x.com", "path": "/", "secure": True},
            ])
            page = ctx.new_page()

            def on_response(resp):
                if "SearchTimeline" in resp.url and resp.status == 200:
                    try:
                        payloads.append(resp.json())
                    except Exception:  # noqa: BLE001
                        pass

            page.on("response", on_response)
            page.goto(f"https://x.com/search?q={quote(query)}&src=typed_query&f=live",
                      wait_until="domcontentloaded", timeout=timeout_s * 1000)
            # one scroll pulls the second page of results; more than that is greedy
            page.wait_for_timeout(6000)
            if len(list(_walk_tweets(payloads))) < limit:
                page.mouse.wheel(0, 2500)
                page.wait_for_timeout(4000)
            title, landed = page.title(), page.url
            browser.close()
    except Exception as e:  # noqa: BLE001 — a dead browser is an answer, not a crash
        return {"query": query, "posts": [], "backend": "x-session", "error": f"browser: {type(e).__name__}: {str(e)[:160]}"}

    posts: list[dict] = []
    seen: set[str] = set()
    for t in _walk_tweets(payloads):
        r = _record(t)
        if r and r["post_id"] not in seen:
            seen.add(r["post_id"])
            posts.append(r)
        if len(posts) >= limit:
            break

    out: dict = {"query": query, "backend": "x-session", "posts": posts, "responses": len(payloads),
                 "budget": budget()}
    if not posts:
        if not payloads and _LOGIN_WALL.search(title or ""):
            out["error"] = (f"X did not serve search (page: '{title[:60]}', landed on {landed[:60]}) — "
                            "the session is logged out or the account is restricted. Not retried.")
        else:
            out["error"] = "no posts in the response"
    else:
        cache.put("x-search", query, out, backend="x-session", limit=limit)
    log.info("x search: %r -> %d posts (%d responses)", query, len(posts), len(payloads))
    return out


__all__ = ["search", "budget", "session_present"]
