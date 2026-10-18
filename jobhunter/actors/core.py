"""Actor plumbing — what Apify sells, kept on this machine.

An actor is a function with a typed input that pushes records into a dataset. Around it,
this module gives every actor the same four things Apify's platform gives theirs:

    run ledger   data/actors/runs.jsonl — one line per run: actor, input, counts, status,
                 the dataset path, the error if it died
    dataset      data/actors/datasets/<run_id>.jsonl — the records, one per line
    cache        data/actors/cache/<sha1(url)>.html — a page fetched inside the TTL is
                 never fetched again (re-running an actor costs nothing)
    politeness   one Fetcher per host and route (host + first path segment): a minimum gap + jitter between requests, a daily
                 cap, and a circuit breaker — the moment the host shows a login wall
                 (redirect to /authwall, /uas/login, /checkpoint, status 999/429, or the
                 page arrives without the data it always carries) the host cools down
                 for `cooldown_hours` and every actor on it stops. Pushing through a wall
                 is how an IP gets flagged; waiting is free.

What it never does: log in, carry a session cookie, or use a personal account
(memory: no personal accounts in scraping). Guest pages only. A proxy is optional —
ACTOR_PROXY=http://user:pass@host:port routes every actor request through it.

Layering: reads config, writes only under data/actors/. No database writes — the CLI
decides what of a dataset enters the Contact / Company tables, through guardrails.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import httpx

from jobhunter import CONFIG, ROOT

log = logging.getLogger(__name__)

ACTOR_DIR = ROOT / "data" / "actors"
CACHE_DIR = ACTOR_DIR / "cache"
DATASET_DIR = ACTOR_DIR / "datasets"
RUNS_FILE = ACTOR_DIR / "runs.jsonl"
STATE_FILE = ACTOR_DIR / "state.json"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)

#: per-host politeness; config.yaml `actors.hosts.<host>` overrides any key
_DEFAULT_HOST = {
    "min_interval_s": 4.0,     # never two requests to a host closer than this
    "jitter_s": 3.0,           # plus up to this much, so the rhythm is not a metronome
    "daily_cap": 300,          # requests per host per local day
    "cooldown_hours": 6,       # how long a login wall stops the host
    "cache_ttl_hours": 72,
    "soft_miss_limit": 3,      # pages in a row without their data = a wall without a redirect
}
#: 2026-10-18, measured from one home IP: /jobs-guest answered 429 after ~8 requests 5–9 s
#: apart, /company and /in answered 999 after 1–2. A trickle is the only free way through —
#: Apify's answer is a residential-proxy pool (set ACTOR_PROXY and raise these).
_HOSTS = {"www.linkedin.com": {"min_interval_s": 20.0, "jitter_s": 25.0, "daily_cap": 120}}

_WALL_URL = ("/authwall", "/uas/login", "/checkpoint", "/login", "/signup")
_WALL_STATUS = (429, 999)


class Blocked(Exception):
    """The host is cooling down, or its daily cap is spent. The run stops cleanly."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def route_key(url: str) -> str:
    """host + first path segment — www.linkedin.com/in, www.linkedin.com/jobs-guest."""
    u = httpx.URL(url)
    seg = u.path.strip("/").split("/")[0]
    return f"{u.host}/{seg}" if seg else u.host


def _host_conf(host: str) -> dict:
    host = host.split("/")[0]
    conf = dict(_DEFAULT_HOST)
    conf.update(_HOSTS.get(host, {}))
    conf.update(((CONFIG.get("actors") or {}).get("hosts") or {}).get(host, {}))
    return conf


# ------------------------------------------------------------------ state (breaker + caps)

def _load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    ACTOR_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1))
    tmp.replace(STATE_FILE)


def host_status(host: str | None = None) -> dict:
    """{host: {cooldown_until, reason, day, used, cap}} — what `actor status` prints."""
    state = _load_state()
    out = {}
    for h, s in state.items():
        if host and h != host:
            continue
        cap = _host_conf(h)["daily_cap"]
        today = datetime.now().strftime("%Y-%m-%d")
        out[h] = {
            "cooling_down": bool(s.get("cooldown_until")) and s["cooldown_until"] > _now().isoformat(),
            "cooldown_until": s.get("cooldown_until"),
            "reason": s.get("reason"),
            "used_today": s.get("count", 0) if s.get("day") == today else 0,
            "daily_cap": cap,
        }
    return out


def reset_host(host: str) -> None:
    state = _load_state()
    state.pop(host, None)
    _save_state(state)


# ------------------------------------------------------------------ fetcher

@dataclass
class Page:
    url: str
    final_url: str
    status: int
    text: str
    cached: bool = False


class Fetcher:
    """One politeness core for every actor. `get()` returns a Page, None for a plain
    miss (404, network error), and raises Blocked when the host must not be touched."""

    def __init__(self, *, fresh: bool = False):
        self.fresh = fresh
        self.network_calls = 0
        self.cache_hits = 0
        self._last: dict[str, float] = {}
        self._misses: dict[str, int] = {}
        proxy = os.environ.get("ACTOR_PROXY") or None
        # impit (Apify's Rust client, shipped with crawlee) speaks Chrome's TLS and HTTP/2
        # fingerprint and sends Chrome's own headers; httpx's Python handshake is the
        # first thing a bot check reads. httpx stays as the fallback.
        try:
            import impit

            self._http = impit.Client(browser="chrome", follow_redirects=True, timeout=25.0,
                                      proxy=proxy, headers={"Accept-Language": "en-US,en;q=0.9"})
            self.client_kind = "impit-chrome"
        except Exception:  # noqa: BLE001 — import or init failure: plain httpx
            self._http = httpx.Client(
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                },
                timeout=httpx.Timeout(25.0), follow_redirects=True, proxy=proxy,
            )
            self.client_kind = "httpx"

    def close(self) -> None:
        try:
            self._http.close()
        except Exception:  # noqa: BLE001
            pass

    # -- cache
    @staticmethod
    def _cache_path(url: str) -> Path:
        return CACHE_DIR / (hashlib.sha1(url.encode()).hexdigest() + ".html")

    def _cached(self, url: str, ttl_h: float) -> str | None:
        p = self._cache_path(url)
        if self.fresh or not p.exists():
            return None
        if time.time() - p.stat().st_mtime > ttl_h * 3600:
            return None
        return p.read_text(encoding="utf-8", errors="ignore")

    def _store(self, url: str, text: str) -> None:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self._cache_path(url).write_text(text, encoding="utf-8")

    # -- breaker
    def _check_open(self, host: str, conf: dict) -> None:
        state = _load_state()
        s = state.get(host) or {}
        until = s.get("cooldown_until")
        if until and until > _now().isoformat():
            raise Blocked(f"{host} cooling down until {until} ({s.get('reason')})")
        today = datetime.now().strftime("%Y-%m-%d")
        if s.get("day") == today and s.get("count", 0) >= conf["daily_cap"]:
            raise Blocked(f"{host} daily cap of {conf['daily_cap']} spent")

    def _count(self, host: str) -> None:
        state = _load_state()
        s = state.setdefault(host, {})
        today = datetime.now().strftime("%Y-%m-%d")
        if s.get("day") != today:
            s["day"], s["count"] = today, 0
        s["count"] = s.get("count", 0) + 1
        _save_state(state)

    def trip(self, host: str, reason: str) -> None:
        conf = _host_conf(host)
        state = _load_state()
        s = state.setdefault(host, {})
        s["cooldown_until"] = (_now() + timedelta(hours=conf["cooldown_hours"])).isoformat()
        s["reason"] = reason
        _save_state(state)
        log.warning("actors: %s tripped (%s) — cooling down %sh", host, reason, conf["cooldown_hours"])
        raise Blocked(f"{host}: {reason}")

    def _pace(self, host: str, conf: dict) -> None:
        last = self._last.get(host)
        if last is not None:
            gap = conf["min_interval_s"] + random.uniform(0, conf["jitter_s"])
            wait = last + gap - time.monotonic()
            if wait > 0:
                time.sleep(wait)
        self._last[host] = time.monotonic()

    # -- the call
    def get(self, url: str, *, expect: Callable[[str], bool] | None = None,
            params: dict | None = None) -> Page | None:
        """GET through cache → breaker → pace → network. `expect(text)` says whether the
        page carries the data this kind of page always carries; a run of pages that do
        not is a wall that did not bother to redirect."""
        full = str(httpx.URL(url, params=params)) if params else url
        u = httpx.URL(full)
        conf = _host_conf(u.host)
        # the breaker and the cap are per route: a wall on /in (profiles) says nothing
        # about /jobs-guest, which LinkedIn serves logged-out by design
        host = route_key(full)
        hit = self._cached(full, conf["cache_ttl_hours"])
        if hit is not None:
            self.cache_hits += 1
            return Page(full, full, 200, hit, cached=True)
        self._check_open(host, conf)
        self._pace(host, conf)
        self._count(host)
        self.network_calls += 1
        try:
            r = self._http.get(full)
        except Exception as e:  # noqa: BLE001 — httpx and impit raise their own families
            log.debug("actors: GET %s failed: %s", full, e)
            return None
        final = str(r.url)
        if r.status_code in _WALL_STATUS:
            self.trip(host, f"status {r.status_code} on {full[:120]}")
        if any(w in httpx.URL(final).path for w in _WALL_URL):
            self.trip(host, f"login wall ({httpx.URL(final).path}) on {full[:120]}")
        if r.status_code >= 400:
            return None
        text = r.text
        if expect is not None and not expect(text):
            n = self._misses.get(host, 0) + 1
            self._misses[host] = n
            if n >= conf["soft_miss_limit"]:
                self.trip(host, f"{n} pages in a row without their data (last {full[:120]})")
            return Page(full, final, r.status_code, text)     # not cached: it is not the page
        self._misses[host] = 0
        self._store(full, text)
        return Page(full, final, r.status_code, text)


# ------------------------------------------------------------------ runs + datasets

@dataclass
class Run:
    actor: str
    input: dict
    run_id: str = ""
    items: int = 0
    stats: dict = field(default_factory=dict)
    fetcher: Fetcher | None = None
    _fh: Any = None

    def __post_init__(self):
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.run_id = f"{self.actor}-{stamp}-{random.randint(100, 999)}"
        DATASET_DIR.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.dataset_path, "a", encoding="utf-8")

    @property
    def dataset_path(self) -> Path:
        return DATASET_DIR / f"{self.run_id}.jsonl"

    def push(self, item: dict) -> None:
        self._fh.write(json.dumps(item, default=str, ensure_ascii=False) + "\n")
        self._fh.flush()
        self.items += 1

    def finish(self, status: str, error: str | None = None) -> dict:
        self._fh.close()
        row = {
            "run_id": self.run_id, "actor": self.actor, "input": self.input,
            "status": status, "error": error, "items": self.items,
            "network_calls": self.fetcher.network_calls if self.fetcher else 0,
            "cache_hits": self.fetcher.cache_hits if self.fetcher else 0,
            "stats": self.stats, "dataset": str(self.dataset_path.relative_to(ROOT)),
            "finished_at": _now().isoformat(),
        }
        with open(RUNS_FILE, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str) + "\n")
        return row


def read_dataset(run_id: str) -> list[dict]:
    p = DATASET_DIR / f"{run_id}.jsonl"
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


def recent_runs(limit: int = 20) -> list[dict]:
    if not RUNS_FILE.exists():
        return []
    lines = RUNS_FILE.read_text(encoding="utf-8").splitlines()[-limit:]
    return [json.loads(x) for x in reversed(lines) if x.strip()]
