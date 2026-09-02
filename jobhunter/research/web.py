"""Web search and page reading.

Search runs through Exa — semantic, so a query can describe the ideal page instead
of guessing keywords, which is what makes "AI startups that raised a seed round in
the UAE this year" answerable at all. Two ways in, tried in order:

  exa-mcp   `mcporter call exa.web_search_exa` — the path Agent Reach configures,
            no key of ours involved (mcporter holds it)
  exa-api   https://api.exa.ai with EXA_API_KEY from the environment

Reading a page: Jina Reader (public, no key), then Exa's fetch tool, then plain
httpx through the existing scraper's HTML-to-text. Every backend is optional; the
first one that returns content wins and says so in `backend`.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx

from jobhunter import CONFIG
from jobhunter.research import backends, cache
from jobhunter.research.models import Page, SearchResult
from jobhunter.scrapers.base import html_to_text

log = logging.getLogger(__name__)

RESEARCH = CONFIG.get("research") or {}
MAX_CHARS = int(RESEARCH.get("max_chars", 12000))
MCP_SERVER = str(RESEARCH.get("mcporter_server", "exa"))
UA = (CONFIG.get("sources") or {}).get(
    "user_agent",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36",
)

JINA = "https://r.jina.ai/"
EXA_API = "https://api.exa.ai/search"
EXA_CONTENTS_API = "https://api.exa.ai/contents"


# ------------------------------------------------------------------ url safety


def public_http_url(url: str) -> str:
    """Reject anything that is not a public http(s) URL.

    These URLs come out of search results and LLM-adjacent text, so they are not
    trusted input: a `file://` or `http://169.254.169.254/` would turn the research
    layer into a way to read the local machine.
    """
    url = (url or "").strip()
    if not url:
        raise ValueError("empty URL")
    if "://" not in url:
        url = "https://" + url
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"unsupported scheme: {parsed.scheme}")
    host = (parsed.hostname or "").lower()
    if not host or host in ("localhost", "localhost.localdomain") or host.endswith(".local"):
        raise ValueError(f"non-public host: {host or '(none)'}")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return url  # a name, not a literal IP — DNS is the network's problem
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
        raise ValueError(f"non-public address: {host}")
    return url


# ------------------------------------------------------------------ exa parsing

_FIELD_RE = re.compile(r"^(Title|URL|Published|Author|Score|Highlights|Text):\s*(.*)$")


def _parse_exa_text(blob: str) -> list[SearchResult]:
    """Exa's MCP tool returns human-readable blocks separated by a `---` line."""
    results: list[SearchResult] = []
    for chunk in re.split(r"\n\s*---+\s*\n", blob):
        chunk = chunk.strip()
        if not chunk:
            continue
        fields: dict[str, str] = {}
        body: list[str] = []
        current: str | None = None
        for line in chunk.splitlines():
            m = _FIELD_RE.match(line.strip())
            if m:
                key, value = m.group(1), m.group(2)
                if key in ("Highlights", "Text"):
                    current = key
                    if value:
                        body.append(value)
                else:
                    current = None
                    fields[key] = value.strip()
                continue
            if current:
                body.append(line)
        url = fields.get("URL", "").strip()
        if not url:
            continue
        snippet = "\n".join(ln for ln in body if ln.strip() != "...").strip()
        results.append(
            SearchResult(
                title=fields.get("Title") or url,
                url=url,
                snippet=snippet[:4000] or None,
                published=fields.get("Published") or None,
                author=fields.get("Author") or None,
                source="exa",
            )
        )
    return results


def _mcp_text(payload: str) -> str:
    """Pull the text blocks out of an MCP `--output json` envelope."""
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return payload  # mcporter fell back to text output; use it as-is
    blocks = data.get("content") if isinstance(data, dict) else None
    if not isinstance(blocks, list):
        return payload
    return "\n\n".join(b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("text"))


def _exa_mcp_call(tool: str, args: dict, timeout: int) -> str | None:
    p = backends.run(
        "mcporter",
        ["call", f"{MCP_SERVER}.{tool}", "--args", json.dumps(args), "--output", "json"],
        timeout=timeout,
    )
    if not p.ok:
        log.debug("mcporter %s failed (%s): %s", tool, p.reason, p.err[:200])
        return None
    text = _mcp_text(p.out)
    return text or None


# ------------------------------------------------------------------ search


def search(
    query: str,
    limit: int = 8,
    *,
    fresh: bool = False,
    timeout: int | None = None,
) -> dict:
    """Semantic web search. Returns {query, backend, results: [...], error?}.

    Never raises on a dead backend — an empty `results` with a populated `error`
    is a truthful answer and lets a caller carry on with the other channels.
    """
    query = (query or "").strip()
    if not query:
        return {"query": query, "backend": None, "results": [], "error": "empty query"}

    if not fresh:
        hit = cache.get("search", query, limit=limit)
        if hit:
            return hit

    tried: list[str] = []
    timeout = timeout or backends.TIMEOUT
    for backend in backends.candidates("web_search"):
        tried.append(backend)
        results: list[SearchResult] = []
        if backend == "exa-mcp":
            text = _exa_mcp_call("web_search_exa", {"query": query, "numResults": limit}, timeout)
            if text:
                results = _parse_exa_text(text)
        elif backend == "exa-api":
            results = _exa_api_search(query, limit, timeout)
        if results:
            out = {
                "query": query,
                "backend": backend,
                "tried": tried,
                "results": [r.as_dict() for r in results[:limit]],
            }
            cache.put("search", query, out, backend=backend, limit=limit)
            return out

    return {
        "query": query,
        "backend": None,
        "tried": tried,
        "results": [],
        "error": "no web-search backend returned results",
        "hint": backends.HINTS.get("exa-mcp", ""),
    }


def _exa_api_search(query: str, limit: int, timeout: int) -> list[SearchResult]:
    key = backends.secret("EXA_API_KEY")
    if not key:
        return []
    try:
        r = httpx.post(
            EXA_API,
            headers={"x-api-key": key, "Content-Type": "application/json"},
            json={
                "query": query,
                "numResults": limit,
                "contents": {"text": {"maxCharacters": 2000}, "highlights": True},
            },
            timeout=timeout,
        )
        if r.status_code >= 400:
            log.debug("exa api -> %s", r.status_code)
            return []
        data = r.json()
    except Exception as e:  # noqa: BLE001 — a dead backend falls through to the next
        log.debug("exa api failed: %s", e)
        return []

    out: list[SearchResult] = []
    for item in (data or {}).get("results") or []:
        highlights = item.get("highlights") or []
        snippet = " … ".join(highlights) if highlights else (item.get("text") or "")
        out.append(
            SearchResult(
                title=item.get("title") or item.get("url") or "",
                url=item.get("url") or "",
                snippet=(snippet or "")[:4000] or None,
                published=item.get("publishedDate"),
                author=item.get("author"),
                source="exa",
                score=item.get("score"),
            )
        )
    return [r for r in out if r.url]


# ------------------------------------------------------------------ page read


def read(
    url: str,
    *,
    max_chars: int | None = None,
    fresh: bool = False,
    timeout: int | None = None,
    prefer: str | None = None,
) -> dict:
    """Fetch one page as readable text. Returns a `Page` dict, or {error}.

    `prefer` moves one backend to the front for this call — the same override
    Agent Reach's `ordered_backends()` gives a user, at call granularity. Pass
    `prefer="direct"` when probing a URL that probably 404s: a local httpx fetch
    costs a second, where Jina Reader can spend half a minute rendering it.
    """
    try:
        url = public_http_url(url)
    except ValueError as e:
        return {"url": url, "error": str(e)}

    limit = max_chars or MAX_CHARS
    if not fresh:
        hit = cache.get("page", url, max_chars=limit)
        if hit:
            return hit

    timeout = timeout or backends.TIMEOUT
    order = backends.candidates("page_read")
    if prefer and prefer in order:
        order.insert(0, order.pop(order.index(prefer)))
    tried: list[str] = []
    for backend in order:
        tried.append(backend)
        text = title = None
        if backend == "jina":
            title, text = _read_jina(url, timeout)
        elif backend == "exa-mcp":
            text = _exa_mcp_call("web_fetch_exa", {"urls": [url], "maxCharacters": limit}, timeout)
        elif backend == "direct":
            title, text = _read_direct(url, timeout)
        if text and text.strip():
            page = Page(
                url=url,
                title=title,
                text=text.strip()[:limit],
                backend=backend,
                fetched_at=datetime.now(timezone.utc),
                truncated=len(text) > limit,
            )
            out = page.as_dict()
            out["tried"] = tried
            cache.put("page", url, out, backend=backend, max_chars=limit)
            return out

    return {"url": url, "tried": tried, "text": "", "error": "no page-read backend returned content"}


_ANTIBOT = re.compile(r"(just a moment\.\.\.|performing security verification|attention required! \| cloudflare)", re.I)


def _read_jina(url: str, timeout: int) -> tuple[str | None, str | None]:
    """Jina Reader returns Markdown with a `Title:` header. Public, no key needed."""
    try:
        r = httpx.get(
            JINA + url,
            headers={"User-Agent": UA, "Accept": "text/plain"},
            timeout=timeout,
            follow_redirects=True,
        )
        if r.status_code >= 400:
            return None, None
        body = r.text
    except Exception as e:  # noqa: BLE001
        log.debug("jina failed for %s: %s", url, e)
        return None, None

    if _ANTIBOT.search(body[:4000]):
        log.debug("jina returned an anti-bot challenge for %s", url)
        return None, None

    title = None
    m = re.search(r"^Title:\s*(.+)$", body[:2000], re.M)
    if m:
        title = m.group(1).strip()
    m = re.search(r"^Markdown Content:\s*$", body, re.M)
    if m:
        body = body[m.end():]
    return title, body


def _read_direct(url: str, timeout: int) -> tuple[str | None, str | None]:
    """Last resort: fetch it ourselves and reuse the scrapers' HTML-to-text."""
    try:
        r = httpx.get(
            url,
            headers={"User-Agent": UA, "Accept": "text/html,*/*;q=0.8"},
            timeout=timeout,
            follow_redirects=True,
        )
        if r.status_code >= 400:
            return None, None
        html = r.text
    except Exception as e:  # noqa: BLE001
        log.debug("direct fetch failed for %s: %s", url, e)
        return None, None
    title = None
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    if m:
        title = re.sub(r"\s+", " ", m.group(1)).strip()[:200]
    return title, html_to_text(html, limit=MAX_CHARS)
