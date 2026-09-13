"""Hunter.io free tier (25 searches/month).

The budget is tiny, so this is spent only on top-scored companies and only to
learn the company's *email pattern* — a pattern generalises to every employee we
found on GitHub, which is far better value than 25 individual lookups.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from jobhunter import CONFIG, secret
from jobhunter.db import get_session, get_setting, set_setting
from jobhunter.scrapers.base import get_json

log = logging.getLogger(__name__)

# Environment first, config.yaml as the legacy fallback - the same order github_miner
# uses. config.yaml is committed and its header says nothing in it is a secret, so
# until now there was no correct place to put this key at all.
API_KEY = secret("HUNTER_API_KEY") or (CONFIG.get("contacts") or {}).get("hunter_api_key") or ""
DOMAIN_SEARCH = "https://api.hunter.io/v2/domain-search?domain={domain}&api_key={key}&limit=10"

MONTHLY_BUDGET = 25
_USED_KEY = "hunter_used"      # "YYYY-MM:count"


@dataclass
class HunterResult:
    pattern: str | None = None          # e.g. "{first}.{last}"
    emails: list[dict] = None           # [{email, first_name, last_name, position, confidence}]

    def __post_init__(self) -> None:
        self.emails = self.emails or []


def available() -> bool:
    return bool(API_KEY)


def _month_key() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m")


def budget_left() -> int:
    if not API_KEY:
        return 0
    with get_session() as session:
        raw = get_setting(session, _USED_KEY, "")
    month, _, count = raw.partition(":")
    if month != _month_key():
        return MONTHLY_BUDGET
    try:
        return max(0, MONTHLY_BUDGET - int(count))
    except ValueError:
        return MONTHLY_BUDGET


def _spend() -> None:
    with get_session() as session:
        raw = get_setting(session, _USED_KEY, "")
        month, _, count = raw.partition(":")
        used = int(count) if month == _month_key() and count.isdigit() else 0
        set_setting(session, _USED_KEY, f"{_month_key()}:{used + 1}")


# Hunter reports patterns like "{first}.{last}"; normalise the handful of variants
_PATTERN_MAP = {
    "{first}": "{first}",
    "{last}": "{last}",
    "{first}{last}": "{first}{last}",
    "{first}.{last}": "{first}.{last}",
    "{first}_{last}": "{first}_{last}",
    "{first}-{last}": "{first}-{last}",
    "{f}{last}": "{f}{last}",
    "{f}.{last}": "{f}.{last}",
    "{first}{l}": "{first}{l}",
    "{first}.{l}": "{first}.{l}",
    "{f}{l}": "{f}{l}",
    "{last}{first}": "{last}{first}",
    "{last}.{first}": "{last}.{first}",
    "{last}{f}": "{last}{f}",
}


async def domain_search(http: httpx.AsyncClient, domain: str) -> HunterResult | None:
    """One Hunter lookup. Returns None if unconfigured or out of monthly budget."""
    if not API_KEY:
        return None
    if budget_left() <= 0:
        log.info("hunter: monthly budget exhausted, skipping %s", domain)
        return None

    data = await get_json(http, DOMAIN_SEARCH.format(domain=domain, key=API_KEY))
    _spend()
    if not isinstance(data, dict) or "data" not in data:
        log.warning("hunter: no result for %s", domain)
        return None

    d = data["data"]
    pattern = _PATTERN_MAP.get((d.get("pattern") or "").strip())
    emails = [
        {
            "email": e.get("value"),
            "first_name": e.get("first_name"),
            "last_name": e.get("last_name"),
            "position": e.get("position"),
            "confidence": e.get("confidence"),
        }
        for e in (d.get("emails") or [])
        if e.get("value")
    ]
    log.info("hunter: %s -> pattern=%s, %d emails (%d lookups left)", domain, pattern, len(emails), budget_left())
    return HunterResult(pattern=pattern, emails=emails)
