"""OpenRouter — the one place this program spends money on a model.

FINAL-PLAN-V3 §4.2, implemented:

  * **Aliases, not model ids, at call sites.** Stages ask for `cheap`, `judge` or
    `writer`; which model that is lives in `config.yaml` and changes without
    touching code.
  * **`num_retries = 0`.** The client never retries. A retry loop inside the
    client is how a rate limit turns into a bill; whoever owns the run owns the
    decision to try again.
  * **A cost row per call**, in the same SQLite file as everything else.
  * **A budget gate before the request, not after.** Over the daily or monthly cap
    raises `BudgetExceeded` — which is not a retryable error.
  * **Think-stripping**, and content that opens with an unclosed `<think>` is
    discarded rather than shown as an answer.

No embeddings: OpenRouter serves none (421 models, zero embedding models). The
resume↔JD prefilter is model-free instead — see `jobhunter/fit.py`.

The key comes from `OPENROUTER_API_KEY` in the environment or a gitignored `.env`,
never from `config.yaml`.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx

from jobhunter import CONFIG, secret
from jobhunter.db import DB_PATH

log = logging.getLogger(__name__)

_OR = CONFIG.get("openrouter") or {}

BASE_URL = str(_OR.get("base_url", "https://openrouter.ai/api/v1")).rstrip("/")
ALIASES: dict[str, str] = dict(_OR.get("aliases") or {})
DEFAULT_ALIAS = str(_OR.get("default_alias", "cheap"))
ENABLED = bool(_OR.get("enabled", True))
FREE_ONLY = bool(_OR.get("free_only", True))
RPM = float(_OR.get("requests_per_minute", 18) or 0)
USD_TO_INR = float(_OR.get("usd_to_inr", 88))
DAILY_CAP_INR = float(_OR.get("daily_inr_cap", 100))
MONTHLY_CAP_INR = float(_OR.get("monthly_inr_cap", 2500))
MAX_TOKENS = int(_OR.get("max_tokens", 1200))
TEMPERATURE = float(_OR.get("temperature", 0.3))
# A cold reasoning model can sit for a while before the first token. One long read
# timeout is still bounded — an untimed wait is the thing that is forbidden.
TIMEOUT = httpx.Timeout(connect=10.0, read=float(_OR.get("timeout", 180)), write=30.0, pool=10.0)

# Optional attribution headers; OpenRouter shows them on the activity page.
_REFERER = str(_OR.get("referer", "https://github.com/Tejmul/zonuly"))
_TITLE = str(_OR.get("app_title", "ZoNuLy JobHunter"))

KEY_ENV = "OPENROUTER_API_KEY"


class LLMUnavailable(RuntimeError):
    """No key, provider unreachable, or the model refused the request."""


class BudgetExceeded(LLMUnavailable):
    """The daily or monthly cap is spent. Deliberately NOT retryable."""


class NotFree(LLMUnavailable):
    """`free_only` is on and the model would have cost money. Never a surprise bill."""


class RateLimited(LLMUnavailable):
    """OpenRouter said 429. On the free tier this is the normal ceiling, not a fault.

    Retryable — but by whoever owns the run, after a wait. Not here: a client that
    retries a rate limit by itself is how a queue turns into a stampede.
    """


@dataclass
class Answer:
    """One completed model call, with what it cost."""

    text: str
    alias: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    cost_inr: float = 0.0
    latency_ms: int = 0
    finish_reason: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if k != "raw"}
        d["chars"] = len(self.text)
        return d


# ---------------------------------------------------------------- cost ledger

SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_call (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    alias       TEXT NOT NULL,
    model       TEXT NOT NULL,
    purpose     TEXT,                  -- which stage asked, for the cost breakdown
    tokens_in   INTEGER NOT NULL DEFAULT 0,
    tokens_out  INTEGER NOT NULL DEFAULT 0,
    cost_usd    REAL NOT NULL DEFAULT 0,
    cost_inr    REAL NOT NULL DEFAULT 0,
    latency_ms  INTEGER NOT NULL DEFAULT 0,
    ok          INTEGER NOT NULL DEFAULT 1,
    error       TEXT
);
CREATE INDEX IF NOT EXISTS ix_llm_call_ts ON llm_call(ts);
"""

_READY = False


def _conn() -> sqlite3.Connection:
    global _READY
    conn = sqlite3.connect(DB_PATH, timeout=15)
    if not _READY:
        conn.executescript(SCHEMA)
        conn.commit()
        _READY = True
    return conn


def _record(
    *,
    alias: str,
    model: str,
    purpose: str,
    tokens_in: int = 0,
    tokens_out: int = 0,
    cost_usd: float = 0.0,
    latency_ms: int = 0,
    ok: bool = True,
    error: str | None = None,
) -> None:
    """Write the cost row. A failed ledger write must never lose the answer."""
    try:
        with _conn() as conn:
            conn.execute(
                "INSERT INTO llm_call (ts, alias, model, purpose, tokens_in, tokens_out,"
                " cost_usd, cost_inr, latency_ms, ok, error) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                    alias,
                    model,
                    purpose,
                    tokens_in,
                    tokens_out,
                    round(cost_usd, 6),
                    round(cost_usd * USD_TO_INR, 4),
                    latency_ms,
                    1 if ok else 0,
                    (error or "")[:500] or None,
                ),
            )
            conn.commit()
    except sqlite3.Error as e:  # noqa: BLE001
        log.debug("ledger write failed: %s", e)


def spend(days: int | None = None, *, month: bool = False) -> dict:
    """What has been spent, and on what. `month` = calendar month to date.

    The window is a string comparison on ISO timestamps, so `ts` is normalized to
    the 'T' separator first: a row written as '2026-09-02 10:00' sorts before every
    '2026-09-02T…' row and would drop out of the window entirely — under-reporting
    spend on the query the budget gate depends on.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if month:
        since = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    elif days is not None:
        since = now.replace(hour=0, minute=0, second=0, microsecond=0)
        since = since.fromordinal(since.toordinal() - max(0, days - 1))
    else:
        since = now.replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        with _conn() as conn:
            total, calls = conn.execute(
                "SELECT COALESCE(SUM(cost_inr), 0), COUNT(*) FROM llm_call"
                " WHERE REPLACE(ts, ' ', 'T') >= ?",
                (since.isoformat(),),
            ).fetchone()
            by_alias = conn.execute(
                "SELECT alias, COUNT(*), COALESCE(SUM(cost_inr), 0) FROM llm_call"
                " WHERE REPLACE(ts, ' ', 'T') >= ? GROUP BY alias ORDER BY 3 DESC",
                (since.isoformat(),),
            ).fetchall()
            by_purpose = conn.execute(
                "SELECT COALESCE(purpose, '?'), COUNT(*), COALESCE(SUM(cost_inr), 0) FROM llm_call"
                " WHERE REPLACE(ts, ' ', 'T') >= ? GROUP BY 1 ORDER BY 3 DESC LIMIT 12",
                (since.isoformat(),),
            ).fetchall()
    except sqlite3.Error as e:  # noqa: BLE001
        return {"error": str(e), "inr": 0.0, "calls": 0}
    return {
        "since": since.isoformat(),
        "inr": round(total, 3),
        "calls": calls,
        "by_alias": {a: {"calls": c, "inr": round(v, 3)} for a, c, v in by_alias},
        "by_purpose": {p: {"calls": c, "inr": round(v, 3)} for p, c, v in by_purpose},
    }


def budget_status() -> dict:
    today = spend()
    this_month = spend(month=True)
    return {
        "day": {"spent_inr": today["inr"], "cap_inr": DAILY_CAP_INR,
                "remaining_inr": round(DAILY_CAP_INR - today["inr"], 3)},
        "month": {"spent_inr": this_month["inr"], "cap_inr": MONTHLY_CAP_INR,
                  "remaining_inr": round(MONTHLY_CAP_INR - this_month["inr"], 3)},
        "over_cap": today["inr"] >= DAILY_CAP_INR or this_month["inr"] >= MONTHLY_CAP_INR,
    }


def check_budget() -> None:
    """Raise BudgetExceeded if a cap is already spent. Called before every request.

    Checked *before* the call, not after: a cap that only notices once the money is
    gone is a report, not a gate.
    """
    status = budget_status()
    if status["day"]["spent_inr"] >= DAILY_CAP_INR:
        raise BudgetExceeded(
            f"daily OpenRouter cap reached: ₹{status['day']['spent_inr']:.2f} of ₹{DAILY_CAP_INR:.2f}. "
            "Raise openrouter.daily_inr_cap in config.yaml to continue — editing the cap is the approval."
        )
    if status["month"]["spent_inr"] >= MONTHLY_CAP_INR:
        raise BudgetExceeded(
            f"monthly OpenRouter cap reached: ₹{status['month']['spent_inr']:.2f} of ₹{MONTHLY_CAP_INR:.2f}. "
            "Raise openrouter.monthly_inr_cap in config.yaml to continue."
        )


# ---------------------------------------------------------------- client

_client: httpx.Client | None = None


def client() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(base_url=BASE_URL, timeout=TIMEOUT)
    return _client


def api_key() -> str:
    return secret(KEY_ENV)


def configured() -> bool:
    return bool(api_key())


def model_for(alias: str) -> str:
    """Resolve an alias to a model id. An unknown alias is a config error, not a guess."""
    if alias in ALIASES:
        return str(ALIASES[alias])
    if "/" in alias:
        return alias  # an explicit model id passed through
    raise LLMUnavailable(
        f"unknown model alias '{alias}'. Known: {', '.join(sorted(ALIASES)) or '(none configured)'}"
        " — see the `openrouter.aliases` block in config.yaml"
    )


def is_free(model_id: str) -> bool:
    """OpenRouter marks zero-cost models with a `:free` suffix on the id."""
    return model_id.endswith(":free") or model_id == "openrouter/free"


def enforce_free(model_id: str) -> None:
    if FREE_ONLY and not is_free(model_id):
        raise NotFree(
            f"'{model_id}' is a paid model and openrouter.free_only is true, so no call was made. "
            "Point the alias at a `:free` model (python scripts/run.py models list --free), "
            "or set free_only: false in config.yaml if you mean to spend."
        )


_last_request = 0.0


def _space_requests() -> None:
    """Keep a gap between calls so the free tier's per-minute limit is not the thing
    that stops a scoring run. Sleeps at most one interval, never unbounded."""
    global _last_request
    if RPM <= 0:
        return
    interval = 60.0 / RPM
    wait = interval - (time.monotonic() - _last_request)
    if 0 < wait <= interval:
        time.sleep(wait)
    _last_request = time.monotonic()


def complete(
    prompt: str,
    system: str | None = None,
    *,
    alias: str = DEFAULT_ALIAS,
    json_mode: bool = False,
    temperature: float | None = None,
    max_tokens: int | None = None,
    model: str | None = None,
    purpose: str = "",
    today_iso: bool = True,
) -> Answer:
    """One completion. Never retries; raises on failure so the caller decides."""
    if not ENABLED:
        raise LLMUnavailable("openrouter.enabled is false in config.yaml — no model call was made")
    # Config first, credentials second: "that alias points at a paid model" is a more
    # useful thing to hear than "no key", and it is knowable without one.
    model_id = model or model_for(alias)
    enforce_free(model_id)

    key = api_key()
    if not key:
        raise LLMUnavailable(
            f"{KEY_ENV} is not set. Put it in .env (see .env.example) or the environment — "
            "never in config.yaml. Get one at https://openrouter.ai/keys"
        )

    check_budget()
    _space_requests()
    sys_text = (system or "").strip()
    if today_iso:
        # §4.2: every prompt gets the date. Models are confidently wrong about
        # "recently" and "this year" otherwise.
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        sys_text = (sys_text + f"\n\nToday's date is {stamp}.").strip()

    messages: list[dict[str, str]] = []
    if sys_text:
        messages.append({"role": "system", "content": sys_text})
    messages.append({"role": "user", "content": prompt})

    payload: dict[str, Any] = {
        "model": model_id,
        "messages": messages,
        "stream": False,
        "temperature": TEMPERATURE if temperature is None else temperature,
        "max_tokens": max_tokens or MAX_TOKENS,
        # ask OpenRouter to return what the call actually cost rather than
        # estimating it from a price table that goes stale
        "usage": {"include": True},
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "HTTP-Referer": _REFERER,
        "X-Title": _TITLE,
    }

    started = time.monotonic()
    try:
        r = client().post("/chat/completions", json=payload, headers=headers)
        r.raise_for_status()
        body = r.json()
    except httpx.TimeoutException as e:
        elapsed = int((time.monotonic() - started) * 1000)
        _record(alias=alias, model=model_id, purpose=purpose, latency_ms=elapsed, ok=False, error="timeout")
        raise LLMUnavailable(f"OpenRouter timed out after {elapsed}ms ({model_id})") from e
    except httpx.HTTPStatusError as e:
        elapsed = int((time.monotonic() - started) * 1000)
        code = e.response.status_code
        detail = _error_detail(e.response)
        _record(alias=alias, model=model_id, purpose=purpose, latency_ms=elapsed, ok=False,
                error=f"{code}: {detail}")
        if code == 401:
            raise LLMUnavailable(f"OpenRouter rejected {KEY_ENV} (401). Check the key at https://openrouter.ai/keys") from e
        if code == 402:
            raise BudgetExceeded(f"OpenRouter account is out of credit (402): {detail}") from e
        if code == 404:
            raise LLMUnavailable(
                f"model '{model_id}' not found on OpenRouter — the free roster changes, "
                "re-check with `python scripts/run.py models list --free`"
            ) from e
        if code == 429:
            raise RateLimited(
                f"rate limited on {model_id}. Free models share a per-minute and a per-day "
                "ceiling; the daily one lifts substantially once the account holds a few "
                f"credits. Wait and re-run the stage — the client does not retry. ({detail})"
            ) from e
        if code == 503:
            raise LLMUnavailable(
                f"no free provider available for {model_id} right now (503) — try another "
                "free alias from the list in config.yaml"
            ) from e
        raise LLMUnavailable(f"OpenRouter error {code} ({model_id}): {detail}") from e
    except httpx.HTTPError as e:
        elapsed = int((time.monotonic() - started) * 1000)
        _record(alias=alias, model=model_id, purpose=purpose, latency_ms=elapsed, ok=False, error=str(e)[:200])
        raise LLMUnavailable(f"OpenRouter unreachable: {e}") from e

    elapsed = int((time.monotonic() - started) * 1000)
    choices = body.get("choices") or []
    if not choices:
        err = (body.get("error") or {}).get("message") or "no choices returned"
        _record(alias=alias, model=model_id, purpose=purpose, latency_ms=elapsed, ok=False, error=str(err)[:200])
        raise LLMUnavailable(f"OpenRouter returned no completion ({model_id}): {err}")

    choice = choices[0]
    text = _clean(((choice.get("message") or {}).get("content")) or "")
    usage = body.get("usage") or {}
    tokens_in = int(usage.get("prompt_tokens") or 0)
    tokens_out = int(usage.get("completion_tokens") or 0)
    cost_usd = float(usage.get("cost") or 0.0)

    _record(
        alias=alias,
        model=body.get("model") or model_id,
        purpose=purpose,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=cost_usd,
        latency_ms=elapsed,
        ok=True,
    )

    answer = Answer(
        text=text,
        alias=alias,
        model=body.get("model") or model_id,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=cost_usd,
        cost_inr=round(cost_usd * USD_TO_INR, 4),
        latency_ms=elapsed,
        finish_reason=choice.get("finish_reason"),
        raw=body,
    )
    if answer.finish_reason == "length":
        log.warning(
            "%s hit max_tokens (%s) on %s — the answer is truncated",
            model_id, payload["max_tokens"], purpose or "a call",
        )
    return answer


def _error_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text[:300]
    err = payload.get("error")
    if isinstance(err, dict):
        return str(err.get("message") or err)[:300]
    return str(err or payload)[:300]


# ---------------------------------------------------------------- text cleanup

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def _clean(text: str) -> str:
    """Strip reasoning traces. §4.2: an unclosed `<think>` means the model ran out
    of budget mid-thought — that is not an answer, so it is discarded."""
    text = _THINK_RE.sub("", text)
    if "</think>" in text:
        text = text.rsplit("</think>", 1)[1]
    if "<think>" in text:
        text = text.split("<think>")[0]
    return text.strip()


# ---------------------------------------------------------------- health


def health() -> dict:
    """Key, aliases, caps and today's spend. Never raises; never echoes the key."""
    key_present = configured()
    paid = sorted(a for a, m in ALIASES.items() if not is_free(str(m)))
    out: dict[str, Any] = {
        "provider": "openrouter",
        "base_url": BASE_URL,
        "enabled": ENABLED,
        "key_present": key_present,
        "key_env": KEY_ENV,
        "aliases": dict(ALIASES),
        "free_only": FREE_ONLY,
        "all_aliases_free": not paid,
        "paid_aliases": paid,
        "requests_per_minute": RPM,
        "budget": budget_status(),
        "spend_today": spend(),
    }
    out["ok"] = bool(key_present and ENABLED and not out["budget"]["over_cap"])
    if paid and FREE_ONLY:
        out["ok"] = False
        out["hint"] = (
            f"free_only is on but these aliases point at paid models: {', '.join(paid)} — "
            "they will be refused before any call is made"
        )
    elif not key_present:
        out["hint"] = f"set {KEY_ENV} in .env (see .env.example) — https://openrouter.ai/keys"
    elif not ENABLED:
        out["hint"] = "openrouter.enabled is false in config.yaml"
    elif out["budget"]["over_cap"]:
        out["hint"] = "budget cap reached — raise openrouter.daily_inr_cap / monthly_inr_cap"
    return out


def verify(alias: str = DEFAULT_ALIAS) -> dict:
    """Actually spend one tiny call to prove the key, the alias and the ledger work."""
    try:
        answer = complete(
            "Reply with the single word: ready",
            "You reply with exactly one word.",
            alias=alias,
            max_tokens=16,
            temperature=0.0,
            purpose="verify",
        )
    except LLMUnavailable as e:
        return {"ok": False, "alias": alias, "error": str(e)}
    return {"ok": True, **answer.as_dict()}


def models(query: str = "", limit: int = 20, *, free: bool = False) -> dict:
    """List models OpenRouter serves. Unauthenticated and free to call — this is how
    you re-check the roster, which changes as free models come and go."""
    try:
        r = httpx.get(f"{BASE_URL}/models", timeout=30)
        r.raise_for_status()
        data = r.json().get("data") or []
    except Exception as e:  # noqa: BLE001
        return {"error": str(e), "models": []}
    q = query.lower().strip()
    hits = [m for m in data if not q or q in (m.get("id") or "").lower()]
    if free:
        hits = [
            m for m in hits
            if (_price(m, "prompt") or 0) == 0 and (_price(m, "completion") or 0) == 0
        ]
        hits.sort(key=lambda m: -((m.get("top_provider") or {}).get("context_length") or 0))
    return {
        "total": len(data),
        "matched": len(hits),
        "models": [
            {
                "id": m.get("id"),
                "context": (m.get("top_provider") or {}).get("context_length") or m.get("context_length"),
                "prompt_usd_per_m": _price(m, "prompt"),
                "completion_usd_per_m": _price(m, "completion"),
                "free": is_free(m.get("id") or ""),
                "json_mode": "response_format" in (m.get("supported_parameters") or []),
            }
            for m in hits[:limit]
        ],
    }


def _price(model: dict, kind: str) -> float | None:
    try:
        return round(float((model.get("pricing") or {}).get(kind, 0)) * 1_000_000, 4)
    except (TypeError, ValueError):
        return None


def resolve_aliases() -> dict:
    """Check every configured alias against the live model list. Catches a typo or
    a model that was retired, before a stage tries to use it."""
    listed = models(limit=10_000)
    if listed.get("error"):
        return {"error": listed["error"], "aliases": dict(ALIASES)}
    known = {m["id"]: m for m in listed.get("models", [])}
    return {
        alias: {
            "model": mid,
            "available": mid in known,
            "free": is_free(str(mid)),
            "json_mode": (known.get(mid) or {}).get("json_mode"),
            "context": (known.get(mid) or {}).get("context"),
        }
        for alias, mid in ALIASES.items()
    }
