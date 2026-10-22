"""Apify as one more people source — the company People tab, with no LinkedIn account of ours.

`harvestapi/linkedin-company-employees` returns the list LinkedIn's company People tab
shows (25 a page, up to 2,500 per search) without our cookie. Per the research
(2026-10-18) it is almost certainly served from the vendor's own LinkedIn accounts —
our LinkedIn account is never involved: the only credential is APIFY_TOKEN, an Apify
account. Apify is pay-per-use (about $5 of free credit a month); no subscription.

Guards, all here so a caller cannot skip them:
    budget   apify.daily_usd (config.yaml `actors.apify.daily_usd`, default $0.50) — the
             cost of a call is estimated BEFORE it runs from maxItems × the mode's price
             and refused when it would cross today's cap; the estimate is booked in
             data/actors/apify_spend.jsonl
    cache    the same input inside 24 h is answered from data/actors/apify_cache/
    pinned   actor id + mode strings are the ones read from the actor's published input
             schema (api.apify.com/v2/acts/harvestapi~linkedin-company-employees/builds/default)

Records come back normalised to the person shape actors/people.py uses.
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime

import httpx

from jobhunter import CONFIG, secret
from jobhunter.actors.core import ACTOR_DIR

ACTOR_ID = "harvestapi~linkedin-company-employees"
MODES = {  # the enum strings the actor's input schema publishes, and $ per profile
    "short": ("Short ($4 per 1k)", 0.004),
    "full": ("Full ($8 per 1k)", 0.008),
    "email": ("Full + email search ($12 per 1k)", 0.012),
}
START_FEE = 0.02
_CONF = ((CONFIG.get("actors") or {}).get("apify") or {})
DAILY_USD = float(_CONF.get("daily_usd", 0.50))
CACHE_HOURS = float(_CONF.get("cache_hours", 24))
SPEND_FILE = ACTOR_DIR / "apify_spend.jsonl"
CACHE_DIR = ACTOR_DIR / "apify_cache"


def token() -> str | None:
    return secret("APIFY_TOKEN") or None


def spent_today(label: str | None = None) -> float:
    """Today's booked Apify spend — all of it, or only the rows a caller booked under `label`."""
    if not SPEND_FILE.exists():
        return 0.0
    today = datetime.now().strftime("%Y-%m-%d")
    total = 0.0
    for line in SPEND_FILE.read_text().splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("day") == today and (label is None or row.get("label") == label):
            total += float(row.get("usd", 0))
    return round(total, 4)


def status() -> dict:
    return {"token": bool(token()), "actor": ACTOR_ID, "spent_today_usd": spent_today(), "daily_usd": DAILY_USD}


def _normalise(item: dict) -> dict:
    loc = item.get("location") or {}
    parsed = loc.get("parsed") or {} if isinstance(loc, dict) else {}
    exp = item.get("experience") or []
    current = [{"company": e.get("companyName"), "company_url": e.get("companyLinkedinUrl"),
                "title": e.get("position"), "start": (e.get("startDate") or {}).get("text")
                if isinstance(e.get("startDate"), dict) else e.get("startDate")}
               for e in exp if not e.get("endDate") or str(e.get("endDate")).lower() == "present"]
    if not current:
        current = [{"company": c.get("companyName"), "company_url": c.get("companyLinkedinUrl"), "title": None,
                    "start": None} for c in item.get("currentPosition") or []]
    name = " ".join(x for x in (item.get("firstName"), item.get("lastName")) if x).strip() or None
    return {
        "name": name,
        "headline": item.get("headline"),
        "title": (current[0].get("title") if current else None) or item.get("headline"),
        "linkedin_url": item.get("linkedinUrl"),
        "location": (loc.get("linkedinText") if isinstance(loc, dict) else loc) or parsed.get("text"),
        "current": current,
        "email": (item.get("emails") or [None])[0] if isinstance(item.get("emails"), list) else item.get("email"),
        "open_to_work": item.get("openToWork"),
        "hiring": item.get("hiring"),
    }


def company_employees(company_url: str, *, max_items: int = 50, mode: str = "short",
                      job_titles: list[str] | None = None, locations: list[str] | None = None) -> dict:
    """{people: [...], cost_usd, cached} or {error}. Never raises."""
    if not token():
        return {"error": "APIFY_TOKEN not set — add it to .env (an Apify account token, never LinkedIn)"}
    label, per = MODES.get(mode, MODES["short"])
    inp = {"companies": [company_url], "maxItems": int(max_items), "profileScraperMode": label,
           "companyBatchMode": "all_at_once"}
    if job_titles:
        inp["jobTitles"] = job_titles
    if locations:
        inp["locations"] = locations
    key = hashlib.sha1(json.dumps(inp, sort_keys=True).encode()).hexdigest()
    cp = CACHE_DIR / f"{key}.json"
    if cp.exists() and time.time() - cp.stat().st_mtime < CACHE_HOURS * 3600:
        return {"people": json.loads(cp.read_text()), "cost_usd": 0.0, "cached": True}
    estimate = round(max_items * per + START_FEE, 4)
    if spent_today() + estimate > DAILY_USD:
        return {"error": f"apify daily cap: ${spent_today()} spent + ${estimate} estimated > ${DAILY_USD}"}
    try:
        r = httpx.post(f"https://api.apify.com/v2/acts/{ACTOR_ID}/run-sync-get-dataset-items",
                       params={"token": token(), "timeout": 280}, json=inp, timeout=300)
    except httpx.HTTPError as e:
        return {"error": f"apify request failed: {e}"}
    if r.status_code >= 400:
        return {"error": f"apify HTTP {r.status_code}: {r.text[:200]}"}
    items = r.json() if r.headers.get("content-type", "").startswith("application/json") else []
    people = [_normalise(x) for x in items if isinstance(x, dict) and (x.get("linkedinUrl") or x.get("firstName"))]
    cost = round(len(people) * per + START_FEE, 4)
    ACTOR_DIR.mkdir(parents=True, exist_ok=True)
    with open(SPEND_FILE, "a") as fh:
        fh.write(json.dumps({"day": datetime.now().strftime("%Y-%m-%d"), "actor": ACTOR_ID, "company": company_url,
                             "items": len(people), "usd": cost, "mode": mode}) + "\n")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cp.write_text(json.dumps(people))
    return {"people": people, "cost_usd": cost, "cached": False}


def run_actor(actor_id: str, inp: dict, *, per_item: float, start_fee: float, max_items: int,
              label: str | None = None, cap_usd: float | None = None, timeout_s: int = 280) -> dict:
    """Any pinned Apify actor under the same guards as company_employees — {items, cost_usd, cached}
    or {error}. Never raises. The estimate (max_items × per_item + start_fee) is checked BEFORE the
    run against today's cap (and `cap_usd`, a caller's own share of it, when given); the actual cost
    is booked after. Same input inside the cache window → the cached items, free."""
    if not token():
        return {"error": "APIFY_TOKEN not set — add it to .env"}
    key = hashlib.sha1(json.dumps({"actor": actor_id, **inp}, sort_keys=True).encode()).hexdigest()
    cp = CACHE_DIR / f"{key}.json"
    if cp.exists() and time.time() - cp.stat().st_mtime < CACHE_HOURS * 3600:
        return {"items": json.loads(cp.read_text()), "cost_usd": 0.0, "cached": True}
    estimate = round(max_items * per_item + start_fee, 4)
    if spent_today() + estimate > DAILY_USD:
        return {"error": f"apify daily cap: ${spent_today()} spent + ${estimate} estimated > ${DAILY_USD}"}
    if cap_usd is not None and spent_today(label) + estimate > cap_usd:
        return {"error": f"{label} cap: ${spent_today(label)} spent + ${estimate} estimated > ${cap_usd}"}
    try:
        r = httpx.post(f"https://api.apify.com/v2/acts/{actor_id}/run-sync-get-dataset-items",
                       params={"token": token(), "timeout": timeout_s}, json=inp, timeout=timeout_s + 20)
    except httpx.HTTPError as e:
        return {"error": f"apify request failed: {e}"}
    if r.status_code >= 400:
        return {"error": f"apify HTTP {r.status_code}: {r.text[:200]}"}
    items = r.json() if r.headers.get("content-type", "").startswith("application/json") else []
    items = [x for x in items if isinstance(x, dict)] if isinstance(items, list) else []
    cost = round(len(items) * per_item + start_fee, 4)
    ACTOR_DIR.mkdir(parents=True, exist_ok=True)
    with open(SPEND_FILE, "a") as fh:
        fh.write(json.dumps({"day": datetime.now().strftime("%Y-%m-%d"), "actor": actor_id, "label": label,
                             "items": len(items), "usd": cost}) + "\n")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cp.write_text(json.dumps(items))
    return {"items": items, "cost_usd": cost, "cached": False}
