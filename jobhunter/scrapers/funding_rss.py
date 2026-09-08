"""Funding-news RSS — funded startups with the amount and the date, for free.

FINAL-PLAN-V3 §4.3 listed this as a gap: TechCrunch, EU-Startups, Sifted, YourStory,
Finsmes publish a round a day in their feeds, each headline carrying the company, the
amount and (from the feed) the date. feedparser reads them with no key and no Exa, which
is exactly the pay/discovery bottleneck. Every item is turned into {name, region, stage,
amount_usd_m, announced, evidence} and handed to the admission gate like any other source.

Zero budget, zero session. The feeds are the plan's Channel-A funding feeds, finally wired.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone

import feedparser
import httpx

log = logging.getLogger(__name__)

# feed -> the region it mostly covers (None = infer from the text)
FEEDS: dict[str, str | None] = {
    "https://techcrunch.com/category/fundraising/feed/": None,
    "https://techcrunch.com/category/venture/feed/": None,
    "https://www.eu-startups.com/feed/": "eu",
    "https://www.finsmes.com/feed": None,
    "https://tech.eu/feed/": "eu",
    "https://yourstory.com/feed": "india",
    "https://inc42.com/feed/": "india",
    "https://sifted.eu/feed": "eu",
}

_AMOUNT = re.compile(
    r"(?P<cur>[$€£])\s?(?P<num>\d+(?:\.\d+)?)\s?(?P<unit>billion|million|bn|b|m|k)?\b", re.I)
_STAGE = re.compile(r"\b(pre-seed|pre seed|seed|series\s+[a-f])\b", re.I)
# "<Company> raises/secures/nabs/lands/closes ..." — the name is what precedes the verb
_HEAD = re.compile(
    r"^(?P<name>.{2,60}?)\s+(?:raises?|raised|secures?|secured|nabs?|lands?|closes?|closed|"
    r"bags?|pulls? in|picks? up|banks?|snags?|scores?|gets?|receives?)\b", re.I)
_CUR_USD = {"$": 1.0, "€": 1.08, "£": 1.27}
_MULT = {"billion": 1000.0, "bn": 1000.0, "b": 1000.0, "million": 1.0, "m": 1.0, "k": 0.001}
_LEAD_NOISE = re.compile(r"^(exclusive|breaking|report|update|just in)\s*[:\-]?\s*", re.I)


def _amount_usd_m(text: str) -> float | None:
    m = _AMOUNT.search(text)
    if not m:
        return None
    num = float(m.group("num"))
    unit = (m.group("unit") or "m").lower()
    usd = num * _MULT.get(unit, 1.0) * _CUR_USD.get(m.group("cur"), 1.0)
    return round(usd, 1) if 0.1 <= usd <= 5000 else None


def _name(title: str) -> str | None:
    t = _LEAD_NOISE.sub("", title or "").strip()
    m = _HEAD.match(t)
    if not m:
        return None
    name = m.group("name").strip(" ,'\"")
    name = re.sub(r"^(uk'?s|us'?s|india'?s|germany'?s|europe'?s|the)\s+", "", name, flags=re.I).strip()
    return name if 1 < len(name) <= 60 and len(name.split()) <= 6 else None


def _announced(entry) -> str | None:
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            return datetime(*t[:3]).strftime("%Y-%m-%d")
    return None


#: Per feed, and for the whole pass. Both are needed: a timeout stops one dead feed,
#: a budget stops eight slow ones adding up to something the hourly tick cannot fit in.
FEED_TIMEOUT_S = 12.0
TOTAL_BUDGET_S = 90.0


def _read(url: str, timeout: float) -> bytes | None:
    """Fetch a feed with a deadline.

    Never `feedparser.parse(url)`. Given a URL, feedparser does its own fetch through
    urllib with no timeout, so a feed that accepts the connection and then goes quiet
    blocks forever. On 2026-09-07 that is exactly what happened: this step ran for
    68,358 seconds — 19 hours — and because the tick holds a lock and skips if another
    tick is running, every hourly run in that window was skipped. feedparser is happy
    to parse bytes we have already fetched ourselves, on our own clock.
    """
    from jobhunter.scrapers.base import HEADERS

    try:
        r = httpx.get(url, timeout=timeout, follow_redirects=True, headers=HEADERS)
        r.raise_for_status()
        return r.content
    except Exception as e:  # noqa: BLE001 — one bad feed must not sink the pass
        log.debug("feed %s failed: %s", url, e)
        return None


def fetch() -> list[dict]:
    """Read every funding feed and return one record per item that names a company and a round."""
    out: dict[str, dict] = {}
    deadline = time.monotonic() + TOTAL_BUDGET_S
    for url, region in FEEDS.items():
        left = deadline - time.monotonic()
        if left <= 0:
            log.warning("funding_rss: budget spent, %d feeds unread", len(FEEDS) - len(out))
            break
        raw = _read(url, min(FEED_TIMEOUT_S, left))
        if raw is None:
            continue
        try:
            d = feedparser.parse(raw)
        except Exception as e:  # noqa: BLE001
            log.debug("feed %s did not parse: %s", url, e)
            continue
        for e in d.entries:
            title = e.get("title", "")
            summary = re.sub(r"<[^>]+>", " ", e.get("summary", ""))
            blob = f"{title}. {summary}"
            name = _name(title)
            if not name:
                continue
            amt = _amount_usd_m(blob)
            stage_m = _STAGE.search(blob)
            key = re.sub(r"[^a-z0-9]", "", name.lower())
            if key in out:
                continue
            out[key] = {
                "name": name, "region": region,
                "stage": stage_m.group(1).lower().replace("pre seed", "pre-seed") if stage_m else None,
                "amount_usd_m": amt, "announced": _announced(e),
                "url": e.get("link"), "title": title[:200],
            }
    log.info("funding_rss: %d rounds across %d feeds", len(out), len(FEEDS))
    return list(out.values())
