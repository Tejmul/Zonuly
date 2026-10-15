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
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import httpx

from jobhunter import CONFIG, channels
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
JINA_UA = "ZoNuLy/1.0 (+research; jina-reader)"
EXA_API = "https://api.exa.ai/search"
EXA_CONTENTS_API = "https://api.exa.ai/contents"
SCRAPEDO_API = "https://api.scrape.do/"

# scrape.do — a proxy fetcher that owns the IP-rotation risk, so it reads pages that
# block a datacenter fetch (careers pages behind Cloudflare, levels.fyi, company sites).
# 1,000 requests/month on the free plan; spent only when jina and direct both fail.
SCRAPEDO_MONTHLY = int(RESEARCH.get("scrapedo_monthly", 1000))
SCRAPEDO_DAILY = int(RESEARCH.get("scrapedo_daily", 120))
_SCRAPEDO_MONTH_KEY = "scrapedo_used"      # "YYYY-MM:count"
_SCRAPEDO_DAY_KEY = "scrapedo_used_day"    # "YYYY-MM-DD:count"


def _scrapedo_token() -> str:
    return backends.secret("Scrape_dog", "SCRAPEDO_TOKEN", "SCRAPE_DO_TOKEN")


def scrapedo_budget() -> dict:
    """{monthly_left, daily_left}. Everything 0 without a token."""
    if not _scrapedo_token():
        return {"monthly_cap": 0, "monthly_left": 0, "daily_cap": 0, "daily_left": 0}
    from jobhunter.db import get_session, get_setting

    now = datetime.now(timezone.utc)
    month, day = now.strftime("%Y-%m"), now.strftime("%Y-%m-%d")
    with get_session() as s:
        mraw = get_setting(s, _SCRAPEDO_MONTH_KEY, "")
        draw = get_setting(s, _SCRAPEDO_DAY_KEY, "")
    mk, _, mc = mraw.partition(":")
    dk, _, dc = draw.partition(":")
    mused = int(mc) if mk == month and mc.isdigit() else 0
    dused = int(dc) if dk == day and dc.isdigit() else 0
    return {"monthly_cap": SCRAPEDO_MONTHLY, "monthly_left": max(0, SCRAPEDO_MONTHLY - mused),
            "daily_cap": SCRAPEDO_DAILY, "daily_left": max(0, SCRAPEDO_DAILY - dused)}


def _scrapedo_spend() -> None:
    from jobhunter.db import get_session, get_setting, set_setting

    now = datetime.now(timezone.utc)
    month, day = now.strftime("%Y-%m"), now.strftime("%Y-%m-%d")
    with get_session() as s:
        mraw = get_setting(s, _SCRAPEDO_MONTH_KEY, "")
        draw = get_setting(s, _SCRAPEDO_DAY_KEY, "")
        mk, _, mc = mraw.partition(":")
        dk, _, dc = draw.partition(":")
        mused = int(mc) if mk == month and mc.isdigit() else 0
        dused = int(dc) if dk == day and dc.isdigit() else 0
        set_setting(s, _SCRAPEDO_MONTH_KEY, f"{month}:{mused + 1}")
        set_setting(s, _SCRAPEDO_DAY_KEY, f"{day}:{dused + 1}")


def _read_scrapedo(url: str, timeout: int, *, render: bool = False) -> tuple[str | None, str | None]:
    """Fetch through scrape.do and reduce to text. Budget-checked before the call."""
    tok = _scrapedo_token()
    if not tok:
        return None, None
    b = scrapedo_budget()
    if b["monthly_left"] <= 0 or b["daily_left"] <= 0:
        log.info("scrape.do budget exhausted (monthly %d, daily %d)", b["monthly_left"], b["daily_left"])
        return None, None
    params = {"token": tok, "url": url}
    if render:
        params["render"] = "true"
    _scrapedo_spend()
    try:
        r = httpx.get(SCRAPEDO_API, params=params, timeout=timeout, follow_redirects=True)
        if r.status_code >= 400:
            log.debug("scrape.do %s -> %s", url, r.status_code)
            channels.record("scrapedo", r.status_code == 404, f"HTTP {r.status_code}")
            return None, None
        html = r.text
    except Exception as e:  # noqa: BLE001
        log.debug("scrape.do failed for %s: %s", url, e)
        channels.record("scrapedo", False, f"{type(e).__name__}: {str(e)[:120]}")
        return None, None
    if _ANTIBOT.search(html[:4000]):
        channels.record("scrapedo", False, "anti-bot page")
        return None, None
    channels.record("scrapedo", True)
    title = None
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    if m:
        title = re.sub(r"\s+", " ", m.group(1)).strip()[:200]
    return title, html_to_text(html, limit=MAX_CHARS)


def fetch_html_scrapedo(url: str, *, render: bool = False, timeout: int = 45) -> str | None:
    """Raw HTML through scrape.do (for parsers that need the markup, e.g. levels.fyi)."""
    tok = _scrapedo_token()
    if not tok:
        return None
    b = scrapedo_budget()
    if b["monthly_left"] <= 0 or b["daily_left"] <= 0:
        return None
    params = {"token": tok, "url": url}
    if render:
        params["render"] = "true"
    _scrapedo_spend()
    try:
        r = httpx.get(SCRAPEDO_API, params=params, timeout=timeout, follow_redirects=True)
        return r.text if r.status_code < 400 else None
    except Exception as e:  # noqa: BLE001
        log.debug("scrape.do html failed for %s: %s", url, e)
        return None


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


# The hosted Exa MCP is free but rate-limited per IP, and it drops out for a minute at a
# time. A 429 used to end search for the whole run; now calls are paced, a transient
# failure is retried with backoff, and only a run of failures opens a short cooldown —
# after which search is tried again rather than abandoned.
_MCP_GAP_S = float(RESEARCH.get("exa_mcp_min_gap_seconds", 2.0))
_MCP_BACKOFF_S = tuple(RESEARCH.get("exa_mcp_backoff_seconds") or (3, 10, 30))
_MCP_COOLDOWN_S = float(RESEARCH.get("exa_mcp_cooldown_seconds", 300))
_MCP_RATE_LIMIT_COOLDOWN_S = float(RESEARCH.get("exa_mcp_rate_limit_cooldown_seconds", 3600))
_RATE_LIMITED = re.compile(r"\b429\b|rate limit|too many requests", re.I)
_TRANSIENT = re.compile(r"429|rate.?limit|too many requests|offline|fetch failed|timed out|ECONN|ETIMEDOUT|503|502", re.I)
_mcp_state = {"last": 0.0, "fails": 0, "cool_until": 0.0}


def _mcp_error(payload: str) -> str | None:
    """mcporter reports a dead server as {"server", "error", "issue"} — sometimes with exit 0."""
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        return None
    if isinstance(data, dict) and data.get("error") and "content" not in data:
        return str(data.get("error"))
    return None


def mcp_cooling() -> bool:
    """True while the hosted MCP is in its post-failure cooldown."""
    return time.monotonic() < _mcp_state["cool_until"]


def _exa_mcp_call(tool: str, args: dict, timeout: int) -> str | None:
    if mcp_cooling():
        _errors.append("exa-mcp: cooling down after repeated failures")
        return None
    for attempt in range(len(_MCP_BACKOFF_S) + 1):
        wait = _MCP_GAP_S - (time.monotonic() - _mcp_state["last"])
        if wait > 0:
            time.sleep(wait)
        _mcp_state["last"] = time.monotonic()
        p = backends.run(
            "mcporter",
            ["call", f"{MCP_SERVER}.{tool}", "--args", json.dumps(args), "--output", "json"],
            timeout=timeout,
        )
        envelope = _mcp_error(p.out)
        if p.ok and not envelope:
            _mcp_state["fails"] = 0
            channels.record("exa-mcp", True)
            return _mcp_text(p.out) or None
        detail = f"{p.err} {envelope or p.out[:300]}"
        channels.record("exa-mcp", False, f"{p.reason}: {detail.strip()[:200]}")
        if _RATE_LIMITED.search(detail):
            # Exa's free MCP limit is per IP and lasts far longer than a retry: the 5-minute
            # cooldown was hitting it 30-60 times an hour all night. Step aside for an hour.
            _mcp_state["cool_until"] = time.monotonic() + _MCP_RATE_LIMIT_COOLDOWN_S
            _mcp_state["fails"] = 0
            channels.record("exa-mcp", False, "rate-limited — resting an hour", cooldown=True)
            log.info("exa-mcp rate-limited — resting %ss; the other keyless backends carry on",
                     int(_MCP_RATE_LIMIT_COOLDOWN_S))
            _errors.append("exa-mcp: rate-limited (free MCP limit)")
            return None
        if p.reason == "missing" or not (_TRANSIENT.search(detail) or p.reason == "timeout"):
            log.debug("mcporter %s failed (%s): %s", tool, p.reason, p.err[:200])
            _errors.append(f"exa-mcp: {p.reason}: {p.err[:120]}")
            return None
        if attempt < len(_MCP_BACKOFF_S):
            log.info("exa-mcp transient failure (%s) — retrying in %ss", p.reason, _MCP_BACKOFF_S[attempt])
            time.sleep(_MCP_BACKOFF_S[attempt])
    _mcp_state["fails"] += 1
    if _mcp_state["fails"] >= 2:
        _mcp_state["cool_until"] = time.monotonic() + _MCP_COOLDOWN_S
        _mcp_state["fails"] = 0
        channels.record("exa-mcp", False, "cooling down after repeated failures", cooldown=True)
        log.warning("exa-mcp failing repeatedly — cooling down %ss, then trying again", int(_MCP_COOLDOWN_S))
    _errors.append("exa-mcp: rate-limited or offline after retries")
    return None


# ------------------------------------------------------------------ jina-ddg
#
# Keyless web search through two Agent Reach pieces: DuckDuckGo's HTML results page,
# fetched by Jina Reader. DuckDuckGo answers a direct request from this machine with a
# bot challenge (HTTP 202) and Jina's own search endpoint needs a key, but Jina Reader
# rendering the DDG page returns plain Markdown — title, the real URL inside DDG's
# `uddg=` redirect, date and snippet. Exa stops being a single point of failure.

#: The free search backends — what an hourly job may use without spending money. Three
#: independent engines, so one rate limit or bot check no longer takes search down.
KEYLESS: tuple[str, ...] = ("jina-ddg", "jina-startpage", "exa-mcp")

_JDDG_GAP_S = float(RESEARCH.get("jina_ddg_min_gap_seconds", 3.5))   # Jina keyless ≈ 20/min
_jina_last = 0.0
#: per engine read through Jina: consecutive failures and the cooldown they opened
_kl_state: dict[str, dict] = {"jina-ddg": {"fails": 0, "cool_until": 0.0},
                              "jina-startpage": {"fails": 0, "cool_until": 0.0}}


def _kl_cooling(name: str) -> bool:
    return time.monotonic() < _kl_state[name]["cool_until"]


def search_down() -> bool:
    """True only while every keyless search backend is in its failure cooldown."""
    return mcp_cooling() and all(_kl_cooling(n) for n in _kl_state)


def _kl_fail(name: str, why: str) -> None:
    channels.record(name, False, why)
    _errors.append(f"{name}: {why}")
    st = _kl_state[name]
    st["fails"] += 1
    if st["fails"] >= 3:
        st["cool_until"] = time.monotonic() + _MCP_COOLDOWN_S
        st["fails"] = 0
        channels.record(name, False, "cooling down after repeated failures", cooldown=True)
        log.warning("%s failing repeatedly — cooling down %ss", name, int(_MCP_COOLDOWN_S))


def _jina_read_engine(name: str, url: str, timeout: int) -> str | None:
    """Fetch a search-results page through Jina Reader, paced for Jina's keyless rate
    (shared by every engine read this way). None on failure, already recorded."""
    global _jina_last
    if _kl_cooling(name):
        _errors.append(f"{name}: cooling down after repeated failures")
        return None
    wait = _JDDG_GAP_S - (time.monotonic() - _jina_last)
    if wait > 0:
        time.sleep(wait)
    _jina_last = time.monotonic()
    try:
        r = httpx.get(JINA + url, headers={"User-Agent": JINA_UA, "Accept": "text/plain"},
                      timeout=timeout, follow_redirects=True)
    except Exception as e:  # noqa: BLE001 — a dead channel is an answer, not a crash
        _kl_fail(name, f"{type(e).__name__}: {str(e)[:120]}")
        return None
    if r.status_code >= 400:
        _kl_fail(name, f"HTTP {r.status_code}")
        return None
    return r.text


def _kl_ok(name: str, results: list) -> None:
    _kl_state[name]["fails"] = 0
    channels.record(name, True, f"{len(results)} results")
    if not results:
        _errors.append(f"{name}: no results on the page")


_JDDG_HIT = re.compile(r"^## \[(?P<title>.+?)\]\((?P<href>[^)\s]+)\)\s*$", re.M)


def _jina_ddg_search(query: str, limit: int, timeout: int) -> list[SearchResult]:
    from urllib.parse import quote_plus

    text = _jina_read_engine("jina-ddg", DDG_HTML + "?q=" + quote_plus(query), timeout)
    if text is None:
        return []
    if "bots use DuckDuckGo" in text[:2000] or "complete the following challenge" in text[:2000]:
        # DuckDuckGo challenged Jina's address: a failure (so the cooldown engages), not "no results"
        _kl_fail("jina-ddg", "DuckDuckGo bot challenge")
        return []
    results = _parse_jina_ddg(text, limit)
    _kl_ok("jina-ddg", results)
    return results


# ------------------------------------------------------------------ jina-startpage
#
# Startpage serves Google's results without a bot wall to Jina Reader, in the same
# Markdown shape as DuckDuckGo's: `## [title](url)` then a snippet that opens with the
# date. Sponsored rows sit above a "Web results" heading and go through an ad redirect.

STARTPAGE = "https://www.startpage.com/do/search"
_SP_DATE = re.compile(r"^(?P<d>[A-Z][a-z]{2} \d{1,2}, \d{4})\s*(?:\*\*)?\.\.\.(?:\*\*)?\s*")


def _jina_startpage_search(query: str, limit: int, timeout: int) -> list[SearchResult]:
    from urllib.parse import quote_plus

    text = _jina_read_engine("jina-startpage", STARTPAGE + "?q=" + quote_plus(query), timeout)
    if text is None:
        return []
    results = _parse_jina_startpage(text, limit)
    _kl_ok("jina-startpage", results)
    return results


def _parse_jina_startpage(text: str, limit: int) -> list[SearchResult]:
    from datetime import datetime as _dt

    cut = text.find("Web results")
    body = text[cut:] if cut >= 0 else text
    out: list[SearchResult] = []
    hits = list(_JDDG_HIT.finditer(body))
    seen: set[str] = set()
    for i, m in enumerate(hits):
        url = m.group("href")
        if (not url.startswith("http") or "startpage.com" in url or "admarketplace" in url
                or url in seen):
            continue
        seen.add(url)
        block = body[m.end(): hits[i + 1].start() if i + 1 < len(hits) else len(body)]
        snip = next((ln.strip() for ln in block.splitlines()
                     if ln.strip() and not ln.lstrip().startswith(("[", "!", "#"))), "")
        published = None
        d = _SP_DATE.match(snip)
        if d:
            try:
                published = _dt.strptime(d.group("d"), "%b %d, %Y").date().isoformat()
            except ValueError:
                published = None
            snip = snip[d.end():]
        out.append(SearchResult(title=_unbold(m.group("title")), url=url, snippet=_unbold(snip) or None,
                                published=published, source="jina-startpage"))
        if len(out) >= limit:
            break
    return out


def _unbold(text: str) -> str:
    """DDG bolds each matched word separately: `**developer****infrastructure**` is two
    words, so the seam between two bold runs is a space, not nothing."""
    import html

    text = re.sub(r"\*\*\*\*", " ", text)
    text = re.sub(r"\*\*|__", "", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _parse_jina_ddg(text: str, limit: int) -> list[SearchResult]:
    out: list[SearchResult] = []
    hits = list(_JDDG_HIT.finditer(text))
    for i, m in enumerate(hits):
        url = _ddg_target(m.group("href"))
        if not url.startswith("http") or "duckduckgo.com" in url:
            continue   # DDG's own ads and navigation
        block = text[m.end(): hits[i + 1].start() if i + 1 < len(hits) else len(text)]
        date = re.search(r"\b(\d{4}-\d{2}-\d{2})T\d{2}:\d{2}", block)
        # the snippet is the paragraph whose text is a bracketed link: [**Name** raised …](…)
        snip = next((ln for ln in block.splitlines() if ln.startswith("[") and not ln.startswith("[![")), "")
        snip = re.sub(r"\]\([^)]*\)", "", snip).lstrip("[")
        snip = _unbold(snip)
        title = _unbold(m.group("title"))
        out.append(SearchResult(title=title, url=url, snippet=snip or None,
                                published=date.group(1) if date else None, source="jina-ddg"))
        if len(out) >= limit:
            break
    return out


# ------------------------------------------------------------------ search


def search(
    query: str,
    limit: int = 8,
    *,
    fresh: bool = False,
    timeout: int | None = None,
    category: str | None = None,
    days: int | None = None,
    include_domains: list[str] | None = None,
    only: tuple[str, ...] | None = None,
) -> dict:
    """Semantic web search. Returns {query, backend, results: [...], error?}.

    `only` restricts the backends tried (e.g. KEYLESS — DuckDuckGo and Startpage via Jina, then the hosted MCP —
    so an hourly free job never falls through to the metered Exa API).

    `include_domains` restricts hits to those hosts (e.g. ["reddit.com"] to read what
    people say rather than what companies say). Exa-API only, like the two below.

    `category` narrows Exa's index ("news", "company", "github", "research paper",
    ... — "tweet" was retired by Exa, and x.com is not in its index at all);
    `days` keeps only pages published in the last N days. Both are Exa-API-only
    — the MCP tool exposes neither, so it is skipped when either is set rather
    than silently answering a different question.

    Never raises on a dead backend — an empty `results` with a populated `error`
    is a truthful answer and lets a caller carry on with the other channels.
    """
    query = (query or "").strip()
    if not query:
        return {"query": query, "backend": None, "results": [], "error": "empty query"}

    params = {"limit": limit, "category": category, "days": days,
              "domains": ",".join(sorted(include_domains)) if include_domains else None}
    if not fresh:
        hit = cache.get("search", query, **params)
        if hit:
            return hit

    tried: list[str] = []
    _errors.clear()
    timeout = timeout or backends.TIMEOUT
    for backend in backends.candidates("web_search"):
        if only and backend not in only:
            continue
        results: list[SearchResult] = []
        if backend == "exa-mcp":
            if category or days or include_domains:
                continue
            tried.append(backend)
            text = _exa_mcp_call("web_search_exa", {"query": query, "numResults": limit}, timeout)
            if text:
                results = _parse_exa_text(text)
        elif backend in ("jina-ddg", "jina-startpage"):
            if category or days or include_domains:
                continue   # no index categories or date filter here; do not answer a different question
            tried.append(backend)
            run = _jina_ddg_search if backend == "jina-ddg" else _jina_startpage_search
            results = run(query, limit, timeout)
        elif backend == "exa-api":
            tried.append(backend)
            results = _exa_api_search(query, limit, timeout, category=category, days=days,
                                      include_domains=include_domains)
        if results:
            out = {
                "query": query,
                "backend": backend,
                "tried": tried,
                "category": category,
                "days": days,
                "include_domains": include_domains,
                "results": [r.as_dict() for r in results[:limit]],
            }
            cache.put("search", query, out, backend=backend, **params)
            return out

    return {
        "query": query,
        "backend": None,
        "tried": tried,
        "results": [],
        "error": "; ".join(_errors) or "no web-search backend returned results",
        "hint": backends.HINTS.get("exa-mcp", ""),
    }


# Why the last search() got nothing, per backend. Search is synchronous and
# single-threaded here, so a module list is enough; it is reset on every call.
_errors: list[str] = []


# ------------------------------------------------------------------ exa budget
#
# The Exa key is a $10 free tier and there is no money behind it (Tejmul,
# 2026-09-03). One search with page text costs roughly a cent, so the whole
# allowance is ~1,000 searches; a runaway loop could spend it in an afternoon.
# The cap is counted per UTC day in the settings table, exactly like Hunter's
# monthly budget, and checked BEFORE the request. Cache hits never count.

EXA_DAILY_CAP = int(RESEARCH.get("exa_daily_cap", 40))
_EXA_USED_KEY = "exa_used"      # "YYYY-MM-DD:count"


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def exa_budget() -> dict:
    """{cap, used, left} for today. No key -> everything 0."""
    if not backends.secret("EXA_API_KEY"):
        return {"cap": 0, "used": 0, "left": 0}
    from jobhunter.db import get_session, get_setting

    with get_session() as session:
        raw = get_setting(session, _EXA_USED_KEY, "")
    day, _, count = raw.partition(":")
    used = int(count) if day == _today() and count.isdigit() else 0
    return {"cap": EXA_DAILY_CAP, "used": used, "left": max(0, EXA_DAILY_CAP - used)}


def _spend_exa() -> None:
    from jobhunter.db import get_session, get_setting, set_setting

    with get_session() as session:
        raw = get_setting(session, _EXA_USED_KEY, "")
        day, _, count = raw.partition(":")
        used = int(count) if day == _today() and count.isdigit() else 0
        set_setting(session, _EXA_USED_KEY, f"{_today()}:{used + 1}")


def _exa_api_search(
    query: str, limit: int, timeout: int, *, category: str | None = None, days: int | None = None,
    include_domains: list[str] | None = None,
) -> list[SearchResult]:
    key = backends.secret("EXA_API_KEY")
    if not key:
        return []
    budget = exa_budget()
    if budget["left"] <= 0:
        _errors.append(f"exa-api: daily cap of {budget['cap']} searches reached — resets at 00:00 UTC "
                       "(research.exa_daily_cap in config.yaml)")
        log.warning("exa api: daily cap %d reached, not searching", budget["cap"])
        return []
    body: dict = {
        "query": query,
        "numResults": limit,
        "contents": {"text": {"maxCharacters": 2000}, "highlights": True},
    }
    if category:
        body["category"] = category
    if include_domains:
        body["includeDomains"] = list(include_domains)
    if days:
        since = datetime.now(timezone.utc) - timedelta(days=days)
        body["startPublishedDate"] = since.strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        r = httpx.post(
            EXA_API,
            headers={"x-api-key": key, "Content-Type": "application/json"},
            json=body,
            timeout=timeout,
        )
        # Exa does not bill a request it rejects (400 INVALID_FLAGS cost 7 of the day's cap
        # on 2026-10-16), so only an answered or failed-upstream request counts
        if not 400 <= r.status_code < 500:
            _spend_exa()
        if r.status_code >= 400:
            # Exa says why in the body ("category no longer supported", bad key,
            # out of credits). That sentence is the answer; a bare status hides it.
            reason = r.text[:200]
            try:
                reason = str(r.json().get("error") or reason)[:200]
            except Exception:  # noqa: BLE001
                pass
            _errors.append(f"exa-api {r.status_code}: {reason}")
            channels.record("exa-api", False, f"{r.status_code}: {reason}")
            log.warning("exa api -> %s: %s", r.status_code, reason)
            return []
        data = r.json()
    except Exception as e:  # noqa: BLE001 — a dead backend falls through to the next
        _errors.append(f"exa-api: {type(e).__name__}: {str(e)[:160]}")
        channels.record("exa-api", False, f"{type(e).__name__}: {str(e)[:160]}")
        log.debug("exa api failed: %s", e)
        return []
    channels.record("exa-api", True)

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


# ------------------------------------------------------------------ X (twitter)

_X_HOST = re.compile(r"(^|\.)(x\.com|twitter\.com)$", re.I)
_X_STATUS = re.compile(r"^/([A-Za-z0-9_]{1,15})/status/(\d+)", re.I)


def _x_post(url: str) -> tuple[str, str] | None:
    """(author handle, post id) if this URL is an X post, else None."""
    parsed = urlparse(url if "://" in url else "https://" + url)
    if not _X_HOST.search((parsed.hostname or "").lower()):
        return None
    m = _X_STATUS.match(parsed.path or "")
    return (m.group(1), m.group(2)) if m else None


# Measured 2026-09-03: Exa returns nothing for x.com (its "tweet" category is
# retired and includeDomains=x.com yields 0 on every query), Jina's search
# endpoint needs a paid key, and the X API's search tier costs ~$200/month.
# DuckDuckGo's HTML endpoint with `site:x.com` is the one free channel that
# returned real posts — and it bot-challenged the second request of the run.
# So this is deliberately slow, cached for a day, and says so when blocked.

DDG_HTML = "https://html.duckduckgo.com/html/"
DDG_MIN_GAP_S = float(RESEARCH.get("ddg_min_gap_seconds", 10))
_ddg_last_call = 0.0
_DDG_CHALLENGE = re.compile(r"(anomaly|captcha|challenge|bots use DuckDuckGo too)", re.I)
_DDG_RESULT = re.compile(
    r'class="result__a"[^>]*href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>.*?'
    r'(?:class="result__snippet"[^>]*>(?P<snippet>.*?)</a>)?',
    re.S,
)


def _ddg_target(href: str) -> str:
    """DDG wraps every result in a redirect: /l/?uddg=<encoded target>."""
    from urllib.parse import parse_qs, unquote

    if "uddg=" not in href:
        return href
    return unquote(parse_qs(urlparse(href).query).get("uddg", [""])[0])


def _ddg_site_search(site: str, query: str, timeout: int) -> tuple[list[dict], str | None]:
    """([{url, title, snippet}], error). One polite request; never raises."""
    import time

    global _ddg_last_call
    wait = DDG_MIN_GAP_S - (time.monotonic() - _ddg_last_call)
    if wait > 0:
        time.sleep(wait)
    _ddg_last_call = time.monotonic()
    try:
        r = httpx.get(
            DDG_HTML,
            params={"q": f"site:{site} {query}"},
            headers={"User-Agent": UA, "Accept": "text/html"},
            timeout=timeout,
            follow_redirects=True,
        )
    except Exception as e:  # noqa: BLE001
        return [], f"duckduckgo: {type(e).__name__}: {str(e)[:120]}"
    body = r.text
    if r.status_code != 200 or _DDG_CHALLENGE.search(body[:6000]):
        return [], (f"duckduckgo bot challenge (HTTP {r.status_code}) — it rate-limits by IP; "
                    f"wait a few minutes and keep to one query per {DDG_MIN_GAP_S:.0f}s")
    out: list[dict] = []
    for m in _DDG_RESULT.finditer(body):
        url = _ddg_target(m.group("href"))
        title = html_to_text(m.group("title") or "", limit=300)
        snippet = html_to_text(m.group("snippet") or "", limit=1000)
        if url:
            out.append({"url": url, "title": title, "snippet": snippet})
    return out, None


def search_x(
    query: str,
    limit: int = 20,
    *,
    days: int | None = None,
    fresh: bool = False,
    timeout: int | None = None,
) -> dict:
    """Posts on X that answer `query`. Records only — no X account, no cookies,
    no X API: MOTIV §6's LinkedIn rule applied to X.

    Backed by a `site:x.com` web search (see the note above), so coverage is
    what a search engine has indexed, not what X shows a logged-in user, and
    the honest measure is the count this returns. DDG gives no dates, so
    `published` is None until the post itself is read; `days` is accepted for
    the API's sake and reported back, never silently applied.

    Every result is an x.com status URL — the post can always be quoted and
    linked downstream. Anything else the engine returned is dropped, not
    passed off as a post.
    """
    query = (query or "").strip()
    if not query:
        return {"query": query, "backend": None, "posts": [], "error": "empty query"}

    params = {"limit": limit}
    if not fresh:
        hit = cache.get("x", query, **params)
        if hit:
            return hit

    rows, error = _ddg_site_search("x.com", query, timeout or backends.TIMEOUT)
    posts: list[dict] = []
    seen: set[str] = set()
    dropped = 0
    for row in rows:
        who = _x_post(row["url"])
        if not who:
            dropped += 1
            continue
        handle, post_id = who
        if post_id in seen:
            continue
        seen.add(post_id)
        posts.append(
            {
                "url": f"https://x.com/{handle}/status/{post_id}",
                "handle": handle,
                "post_id": post_id,
                "text": (row.get("snippet") or row.get("title") or "")[:2000] or None,
                "published": None,
                "source": "duckduckgo",
            }
        )
        if len(posts) >= limit:
            break

    out = {
        "query": query,
        "backend": "duckduckgo" if posts else None,
        "days": days,
        "days_applied": False,
        "posts": posts,
        "dropped_non_posts": dropped,
    }
    if error:
        out["error"] = error
    elif not posts:
        out["error"] = "search engine returned no x.com posts for this query"
    else:
        cache.put("x", query, out, backend="duckduckgo", **params)
    return out


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
        elif backend == "scrapedo":
            title, text = _read_scrapedo(url, timeout)
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
    """Jina Reader returns Markdown with a `Title:` header. Public, no key needed.

    Jina is an API, not a website: sent the browser User-Agent the scrapers use, its
    Cloudflare front answers 403 "Just a moment…" (2026-10-07 — every read had been
    silently falling through to the direct fetch, which cannot render a JS page). A plain
    client UA gets the rendered Markdown."""
    try:
        r = httpx.get(
            JINA + url,
            headers={"User-Agent": JINA_UA, "Accept": "text/plain"},
            timeout=timeout,
            follow_redirects=True,
        )
        if r.status_code >= 400:
            # a 404 / 422 for a page that does not exist is an answer; 429 and 5xx are not
            channels.record("jina", r.status_code not in (402, 403, 429) and r.status_code < 500,
                            f"HTTP {r.status_code}")
            return None, None
        body = r.text
    except Exception as e:  # noqa: BLE001
        log.debug("jina failed for %s: %s", url, e)
        channels.record("jina", False, f"{type(e).__name__}: {str(e)[:120]}")
        return None, None

    if _ANTIBOT.search(body[:4000]):
        log.debug("jina returned an anti-bot challenge for %s", url)
        channels.record("jina", False, "anti-bot challenge")
        return None, None
    channels.record("jina", True)

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
            channels.record("direct", r.status_code < 500 and r.status_code not in (403, 429), f"HTTP {r.status_code}")
            return None, None
        html = r.text
    except Exception as e:  # noqa: BLE001
        log.debug("direct fetch failed for %s: %s", url, e)
        channels.record("direct", False, f"{type(e).__name__}: {str(e)[:120]}")
        return None, None
    channels.record("direct", True)
    title = None
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    if m:
        title = re.sub(r"\s+", " ", m.group(1)).strip()[:200]
    return title, html_to_text(html, limit=MAX_CHARS)
