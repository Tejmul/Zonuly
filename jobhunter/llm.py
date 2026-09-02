"""The model surface every stage calls — now served by OpenRouter.

This used to be a local-model client. It is now a thin adapter over
`jobhunter.openrouter`, keeping the same function signatures so the nine call
sites in matcher / normalize / resume / outreach did not have to be rewritten.
Two things did change, and both are deliberate:

  * **`alias=`** — a call site now says whether it wants `cheap`, `judge` or
    `writer` work. Which model that is lives in `config.yaml`. Calls that do not
    say get `cheap`, because most of them should be.
  * **`embed()` no longer works.** OpenRouter serves 421 models and none of them
    are embedding models, so there is nothing to route to. The resume↔JD
    prefilter it used to feed is model-free now — see `jobhunter.fit`.

The old local-runtime knobs (`num_ctx`, `keep_alive`) went with it: context is the
model's business on a hosted API, and nothing is resident to keep alive.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from jobhunter import openrouter
from jobhunter.openrouter import (  # re-exported so callers can catch them from here
    Answer,
    BudgetExceeded,
    LLMUnavailable,
)

log = logging.getLogger(__name__)

DEFAULT_ALIAS = openrouter.DEFAULT_ALIAS

__all__ = [
    "chat",
    "chat_json",
    "embed",
    "embed_one",
    "health",
    "Answer",
    "LLMUnavailable",
    "BudgetExceeded",
    "EmbeddingsUnsupported",
]


class EmbeddingsUnsupported(LLMUnavailable):
    """OpenRouter has no embeddings endpoint. Use `jobhunter.fit` instead."""


def chat(
    prompt: str,
    system: str | None = None,
    *,
    json_mode: bool = False,
    temperature: float | None = None,
    num_ctx: int | None = None,      # noqa: ARG001 — accepted for signature compatibility
    num_predict: int | None = None,
    model: str | None = None,
    think: bool = False,             # noqa: ARG001 — see below
    alias: str = DEFAULT_ALIAS,
    purpose: str = "",
) -> str:
    """Single-turn chat. Returns the assistant text with any reasoning stripped.

    `num_predict` maps to `max_tokens`. `num_ctx` is ignored — a hosted model's
    context window is not ours to set. `think` is ignored too: reasoning models
    reason whether or not we ask, and the trace is stripped either way.
    """
    answer = openrouter.complete(
        prompt,
        system,
        alias=alias,
        json_mode=json_mode,
        temperature=temperature,
        max_tokens=num_predict,
        model=model,
        purpose=purpose or alias,
    )
    return answer.text


def chat_json(
    prompt: str,
    system: str | None = None,
    *,
    default: Any = None,
    retries: int = 2,
    **kw: Any,
) -> Any:
    """Chat in JSON mode with parse-retry. Returns `default` if the model never
    produces valid JSON.

    A budget refusal is not a parse failure: it propagates immediately rather than
    burning the retries, because trying again cannot help.
    """
    last_raw = ""
    for attempt in range(retries + 1):
        nudge = "" if attempt == 0 else "\n\nYour previous reply was not valid JSON. Reply with JSON only."
        try:
            raw = chat(prompt + nudge, system, json_mode=True, **kw)
        except BudgetExceeded:
            raise
        except LLMUnavailable as e:
            log.error("model call failed: %s", e)
            return default
        last_raw = raw
        parsed = _extract_json(raw)
        if parsed is not None:
            return parsed
        log.warning("LLM returned unparseable JSON (attempt %d/%d)", attempt + 1, retries + 1)
    log.error("Giving up on JSON parse; last raw output: %s", last_raw[:400])
    return default


# ---------------------------------------------------------------- embeddings (gone)

_EMBED_MSG = (
    "OpenRouter serves no embedding models, so llm.embed() has nothing to route to. "
    "The resume<->JD prefilter is model-free now: use jobhunter.fit.score() / "
    "matcher.prefilter(). If you genuinely need vectors, add a dedicated embeddings "
    "provider — that is a separate key and a separate cost."
)


def embed(texts: list[str], *, model: str | None = None) -> list[list[float]]:
    raise EmbeddingsUnsupported(_EMBED_MSG)


def embed_one(text: str, **kw: Any) -> list[float]:
    raise EmbeddingsUnsupported(_EMBED_MSG)


# ---------------------------------------------------------------- health


def health() -> dict:
    """Provider status for `doctor` and `/api/health`.

    Keeps the `model_present` / `embed_present` keys the previous health call returned
    so the existing callers keep rendering; `embed_present` is now permanently
    false and says why.
    """
    status = openrouter.health()
    aliases = status.get("aliases") or {}
    return {
        **status,
        "model": aliases.get(DEFAULT_ALIAS) or "(no alias configured)",
        "models": sorted(set(aliases.values())),
        "model_present": bool(aliases),
        "embed_model": None,
        "embed_present": False,
        "embed_note": "OpenRouter has no embeddings endpoint — prefilter is model-free (jobhunter.fit)",
    }


# ---------------------------------------------------------------- helpers


def _extract_json(raw: str) -> Any:
    """Parse JSON, tolerating markdown fences and leading/trailing prose."""
    import json

    raw = raw.strip()
    if not raw:
        return None
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    # fall back to the first balanced {...} or [...] block
    for opener, closer in (("{", "}"), ("[", "]")):
        start = raw.find(opener)
        if start == -1:
            continue
        depth, in_str, esc = 0, False, False
        for i, ch in enumerate(raw[start:], start):
            if esc:
                esc = False
                continue
            if ch == "\\":
                esc = True
                continue
            if ch == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(raw[start : i + 1])
                    except json.JSONDecodeError:
                        break
    return None
