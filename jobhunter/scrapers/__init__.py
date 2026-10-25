"""Scraper registry. Every module exposes `async fetch(http, companies) -> list[RawJob]`."""

from __future__ import annotations

import importlib
import logging
from typing import Any, Callable

log = logging.getLogger(__name__)

# name -> module path. Playwright-backed scrapers are last and default-off in config.
REGISTRY: dict[str, str] = {
    "greenhouse": "jobhunter.scrapers.greenhouse",
    "lever": "jobhunter.scrapers.lever",
    "ashby": "jobhunter.scrapers.ashby",
    "hn_hiring": "jobhunter.scrapers.hn_hiring",
    "remoteok": "jobhunter.scrapers.remoteok",
    "wwr": "jobhunter.scrapers.wwr",
    "yc": "jobhunter.scrapers.yc",
    "wellfound": "jobhunter.scrapers.wellfound",
    "freehire": "jobhunter.scrapers.freehire",
    "cutshort": "jobhunter.scrapers.cutshort",
    "instahyre": "jobhunter.scrapers.instahyre",
}


def get_fetcher(name: str) -> Callable[..., Any] | None:
    path = REGISTRY.get(name)
    if not path:
        log.warning("unknown scraper: %s", name)
        return None
    try:
        return importlib.import_module(path).fetch
    except Exception as e:  # noqa: BLE001 — e.g. Playwright not installed
        log.warning("scraper %s unavailable: %s", name, e)
        return None
