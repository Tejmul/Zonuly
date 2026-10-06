"""The tick — one bounded slice of the pipeline, run by launchd, so nobody has to ask.

FINAL-PLAN-V3 §6 / CHOKEPOINTS §9.1.8: an hourly job that takes a lock, does the most
useful bounded piece of work it can inside the day's budgets, and exits. Two kinds:

    free   keyless and unmetered — YC directory, job boards, roles, hiring verification,
           website-finding for name-only companies, the dedup gate, the people hunt,
           reply polling, pay re-read, grading and the priority regrade. Hourly, and on wake.
    paid   the capped channels — Exa (company facts, discovery), X (posts), and the model
           passes (descriptions, origin stories). The full run is daily just after the caps
           reset; with `tick.paid_hourly` a smaller slice also runs at the end of every free
           tick. Every capped channel is PACED: by hour h (0–23) it may have spent at most
           cap × (h+1)/24 of its daily cap — the daily caps are spread, never raised.

Every stage commits per item; a tick that is killed loses nothing and the next one
resumes. Two ticks never overlap: an fcntl lock on data/tick.lock. Progress is written to
the `setting` table (tick:last:<kind>) and to logs/tick-<kind>.log, so `tick status`
answers "when did it last run, and what did it do".
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import plistlib
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from jobhunter import CONFIG, ROOT

log = logging.getLogger(__name__)

_T = CONFIG.get("tick") or {}
LOCK = ROOT / "data" / "tick.lock"
LOGS = ROOT / "logs"
LABEL = "com.zonuly.tick"
PYTHON = str(ROOT / ".venv" / "bin" / "python")
FREE_INTERVAL_S = int(_T.get("free_interval_seconds", 3600))
PAID_HOUR = int(_T.get("paid_hour", 6))
PAID_MINUTE = int(_T.get("paid_minute", 15))
# per-tick limits: small enough that a tick finishes well inside its interval
L = {
    "verify": int(_T.get("verify_per_tick", 60)),
    "people": int(_T.get("people_per_tick", 40)),
    "describe": int(_T.get("describe_per_tick", 40)),
    "story": int(_T.get("story_per_tick", 40)),
    "facts": int(_T.get("facts_per_tick", 90)),
    "levels": int(_T.get("levels_per_tick", 60)),
    "linkedin": int(_T.get("linkedin_per_tick", 15)),
    "exa_queries": int(_T.get("exa_discovery_queries_per_day", 6)),
    "websites": int(_T.get("websites_per_tick", 30)),
    "facts_keyless": int(_T.get("facts_keyless_per_tick", 20)),
}
PAID_HOURLY = bool(_T.get("paid_hourly", True))
# the one-off cleanup's merge/quarantine pass, hourly. Off until the first
# `scripts/cleanup_companies.py --apply` has been reviewed and run by hand.
HYGIENE_HOURLY = bool(_T.get("company_hygiene", False))


# ------------------------------------------------------------------ bookkeeping

def _record(kind: str, summary: dict) -> None:
    from jobhunter.db import get_session, set_setting

    with get_session() as s:
        set_setting(s, f"tick:last:{kind}", json.dumps({"at": datetime.now().isoformat(timespec="seconds"), **summary}, default=str)[:4000])


def last(kind: str) -> dict | None:
    from jobhunter.db import get_session, get_setting

    with get_session() as s:
        raw = get_setting(s, f"tick:last:{kind}", "")
    try:
        return json.loads(raw) if raw else None
    except json.JSONDecodeError:
        return None


class _Lock:
    def __enter__(self):
        LOCK.parent.mkdir(exist_ok=True)
        self.fh = open(LOCK, "w")
        try:
            fcntl.flock(self.fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.fh.close()
            raise RuntimeError("another tick is running")
        self.fh.write(f"{os.getpid()} {datetime.now().isoformat()}\n")
        self.fh.flush()
        return self

    def __exit__(self, *a):
        fcntl.flock(self.fh, fcntl.LOCK_UN)
        self.fh.close()


# ------------------------------------------------------------------ the slices

def _step(summary: dict, name: str, fn, *args, **kw) -> None:
    t = time.time()
    try:
        out = fn(*args, **kw)
        summary[name] = out if isinstance(out, (dict, int, str)) else (out.as_dict() if hasattr(out, "as_dict") else len(out))
    except Exception as e:  # noqa: BLE001 — one stage failing must not end the tick
        log.exception("tick step %s failed", name)
        summary[name] = {"error": str(e)[:200]}
    summary.setdefault("_seconds", {})[name] = round(time.time() - t)


FULL_SCRAPE_KEY = "tick.full_scrape_day"


def full_scrape_if_due() -> dict:
    """The once-a-day refresh of every known board, owned by the tick rather than by the
    API's 08:00 cron.

    The 08:00 job lives in APScheduler inside the API process, and APScheduler freezes
    while the laptop sleeps. Seven mornings running the lid was shut at 08:00; on each
    the job came due, the machine woke after 09:00, the one-hour misfire grace had
    expired, and the job was skipped until the next day. Zero runs in a week with the
    API up the whole time. The tick's other roles step only ever reads a board once, so
    nothing was refreshing existing boards at all.

    launchd fires this tick hourly AND on wake, so the first tick after the lid opens
    does the refresh - whatever the hour. Keyed by local date, so it runs once a day
    however many ticks follow it."""
    from jobhunter import pipeline
    from jobhunter.db import get_session, get_setting, set_setting, init_db

    init_db()
    today = datetime.now().strftime("%Y-%m-%d")
    with get_session() as session:
        if get_setting(session, FULL_SCRAPE_KEY) == today:
            return {"skipped": "already scraped today"}
    out = pipeline.scrape().as_dict()
    with get_session() as session:
        set_setting(session, FULL_SCRAPE_KEY, today)
        session.commit()
    out["day"] = today
    return out


def poll_replies() -> dict:
    """Read Gmail for replies — hourly, so replies land even when the API (and its
    APScheduler) is not running. A no-op with an error dict when Gmail is not configured;
    never raises. The time and the error go to the `setting` table for the dashboard."""
    from jobhunter.db import get_session, set_setting

    try:
        from jobhunter.outreach import tracker

        out = tracker.poll()
    except Exception as e:  # noqa: BLE001 — a mailbox problem must not end the tick
        log.warning("reply poll failed: %s", e)
        out = {"error": f"{type(e).__name__}: {str(e)[:200]}"}
    try:
        with get_session() as session:
            set_setting(session, "outreach:last_poll_at", datetime.now().isoformat(timespec="seconds"))
            set_setting(session, "outreach:last_poll_error", str(out.get("error") or ""))
    except Exception as e:  # noqa: BLE001
        log.warning("reply poll: could not record the poll time: %s", e)
    return out


def hygiene_step() -> dict:
    """The dedup gate, hourly: name keys for new rows always; the full merge/quarantine
    pass only once `tick.company_hygiene` is switched on."""
    from jobhunter import hygiene

    out: dict = {"name_keys": hygiene.backfill_keys()}
    if HYGIENE_HOURLY:
        res = hygiene.run(dry_run=False)
        out["applied"] = {k: v for k, v in (res.get("applied") or {}).items() if k != "regrade"}
    return out


def _mark_empty() -> dict:
    from jobhunter import hygiene

    return hygiene.mark_empty()


def run_free(budget_s: int = 1800) -> dict:
    """Keyless work, in the order the 2,000-target needs it: leads, then proof, then fresh companies."""
    from jobhunter import enrich, harvest, hiring_verify, targeting

    s: dict = {"kind": "free", "started": datetime.now().isoformat(timespec="seconds")}
    t0 = time.time()
    # replies first: cheap, and a "yes" should never wait behind a scrape
    _step(s, "poll_replies", poll_replies)
    # the daily refresh of every board goes first: it is the one thing that must happen
    # today, and the budget checks below would otherwise let a slow tick starve it
    _step(s, "full_scrape", full_scrape_if_due)
    _step(s, "hygiene", hygiene_step)
    # the companies we hold nothing about go to the front of every queue below
    _step(s, "scrape_first", _mark_empty)
    # priority before the queues read it: overrated, priority score, city for every row
    _step(s, "priority", targeting.refresh_priority)
    # free discovery: funding-news RSS (amounts + dates, no Exa) then its boards/roles
    _step(s, "funding_rss", harvest.admit_funding_rss)
    # name-only companies (funding feeds, X, directories) get a verified website, keyless:
    # the hosted Exa MCP search + a direct fetch that must name the company
    _step(s, "websites", enrich.find_websites, limit=L["websites"], keyless=True)
    if L["facts_keyless"]:
        _step(s, "facts_keyless", lambda: _count(enrich.facts_pending(limit=L["facts_keyless"], keyless=True)))
    _step(s, "probe", harvest.probe_ats)
    _step(s, "roles", harvest.scrape_roles)
    _step(s, "people", harvest.find_people, limit=L["people"])
    # public-LinkedIn leads for the companies GitHub can't cover — free, gapped, no login
    from jobhunter.contacts import linkedin_public

    _step(s, "linkedin", linkedin_public.find_pending, limit=L.get("linkedin", 15))
    if time.time() - t0 < budget_s:
        _step(s, "verify", lambda: {"checked": len(hiring_verify.verify_pending(limit=L["verify"], tiers=("tier1", "tier2", "prospect", "unknown")))})
    if time.time() - t0 < budget_s:
        _step(s, "yc", harvest.admit_yc)
        _step(s, "probe", harvest.probe_ats)
        _step(s, "roles", harvest.scrape_roles)
    if time.time() - t0 < budget_s:
        _step(s, "people_no_roles", harvest.find_people, limit=L["people"] // 2, require_roles=False)
    _step(s, "pay", targeting.extract_job_pay, only_missing=True)
    _step(s, "grade", targeting.grade_companies, regrade=False)
    _step(s, "priority_after", targeting.refresh_priority)
    if PAID_HOURLY and time.time() - t0 < budget_s:
        s["paid_slice"] = run_paid(hourly=True)
    s["status"] = harvest.status()
    return s


# ------------------------------------------------------------------ pacing the capped channels

PACE_KEY = "tick:paid_used"


def paced(cap: int, used: int, *, hour: int | None = None) -> int:
    """How much of a daily cap may be spent now: by hour h, at most cap × (h+1)/24 in
    total. A tick that missed hours catches up; nothing ever exceeds the daily cap."""
    import math

    h = datetime.now().hour if hour is None else hour
    return max(0, min(cap, math.ceil(cap * (h + 1) / 24)) - used)


def _used_today() -> dict:
    from jobhunter.db import get_session, get_setting

    with get_session() as s:
        raw = get_setting(s, PACE_KEY, "")
    try:
        data = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        data = {}
    today = datetime.now().strftime("%Y-%m-%d")
    return data if data.get("day") == today else {"day": today}


def _spend(key: str, n: int) -> None:
    from jobhunter.db import get_session, set_setting

    data = _used_today()
    data[key] = int(data.get(key, 0)) + int(n)
    with get_session() as s:
        set_setting(s, PACE_KEY, json.dumps(data))


def run_paid(hourly: bool = False) -> dict:
    """The capped channels, each reading its budget first and spending only its paced share.

    The daily run (06:15) also does discovery (Exa queries, X posts), the full regrade and
    the graph sync; an hourly slice does only the paced per-company passes."""
    from jobhunter import enrich, harvest, targeting
    from jobhunter.research import web, x_search

    s: dict = {"kind": "paid-slice" if hourly else "paid", "started": datetime.now().isoformat(timespec="seconds"),
               "budgets_before": {"exa": web.exa_budget(), "x": x_search.budget(), "scrapedo": web.scrapedo_budget()}}
    used = _used_today()
    # paced only when slices run hourly; otherwise the one daily run may use the whole day
    pace = paced if (PAID_HOURLY or hourly) else (lambda cap, spent: max(0, cap - spent))
    if not hourly:
        # discovery first: a handful of rotating Exa queries a day keeps new companies arriving
        day = datetime.now().timetuple().tm_yday
        pool = harvest.DEFAULT_EXA_QUERIES
        todays = [pool[(day * L["exa_queries"] + i) % len(pool)] for i in range(L["exa_queries"])]
        if not used.get("discovery"):
            _step(s, "exa_discovery", harvest.admit_exa, todays)
            if x_search.session_present():
                _step(s, "x_posts", harvest.admit_x)
            _spend("discovery", 1)
        _step(s, "probe", harvest.probe_ats)
        _step(s, "roles", harvest.scrape_roles)
    # the company card (round, amount, date, HQ, website) — Exa's metered company index,
    # inside the paced share of exa_daily_cap
    exa = web.exa_budget()
    # a lookup can cost two searches (domain-filtered, then open), so half the Exa share
    facts_n = min(pace(L["facts"], int(used.get("facts", 0))), pace(exa["cap"], exa["used"]) // 2)
    if facts_n:
        res = enrich.facts_pending(limit=facts_n)
        _spend("facts", len(res))
        s["facts"] = _count(res)
    # the model passes: descriptions for bare sites, then origin stories (free models,
    # capped in INR by the OpenRouter ledger; paced here so 18 rpm is never the wall)
    n = pace(L["describe"], int(used.get("describe", 0)))
    if n:
        res = enrich.enrich_pending(limit=n, use_search=False, missing="description")
        _spend("describe", len(res))
        s["describe"] = _count(res)
    n = pace(L["story"], int(used.get("story", 0)))
    if n:
        res = enrich.enrich_pending(limit=n, use_search=False, missing="story")
        _spend("story", len(res))
        s["story"] = _count(res)
    # levels.fyi pay via scrape.do — inside the paced share of scrapedo_daily
    from jobhunter import levels

    sd = web.scrapedo_budget()
    n = min(pace(L["levels"], int(used.get("levels", 0))),
            pace(sd["daily_cap"], sd["daily_cap"] - sd["daily_left"]))
    if n:
        _step(s, "levels", lambda: levels.lookup_pending(limit=n))
        _spend("levels", n)
    if not hourly:
        _step(s, "grade", targeting.grade_companies, regrade=True)
        _step(s, "kg", _kg_sync)
        s["status"] = harvest.status()
    s["budgets_after"] = {"exa": web.exa_budget(), "x": x_search.budget(), "scrapedo": web.scrapedo_budget()}
    return s


def _count(res: list[dict]) -> dict:
    return {"companies": len(res), "described": sum(1 for r in res if r.get("description")),
            "story": sum(1 for r in res if r.get("story")), "funding": sum(1 for r in res if r.get("funding")),
            "errors": sum(1 for r in res if r.get("error"))}


def _kg_sync() -> dict:
    from jobhunter.kg import brief, sync

    out = sync.sync_all()
    brief.write()
    return {"synced": True, **({k: v for k, v in out.items() if isinstance(v, int)} if isinstance(out, dict) else {})}


def run(kind: str = "free") -> dict:
    """Entry point for launchd and the CLI. Takes the lock, runs one slice, records it."""
    LOGS.mkdir(exist_ok=True)
    try:
        with _Lock():
            summary = run_free() if kind == "free" else run_paid() if kind == "paid" else {**run_free(), "paid": run_paid()}
    except RuntimeError as e:
        return {"kind": kind, "skipped": str(e)}
    summary["finished"] = datetime.now().isoformat(timespec="seconds")
    _record(kind, {k: v for k, v in summary.items() if k != "status"} | {"status": summary.get("status")})
    return summary


# ------------------------------------------------------------------ launchd

def _agents_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def _plist(kind: str) -> dict:
    base = {
        "Label": f"{LABEL}.{kind}",
        "ProgramArguments": [PYTHON, str(ROOT / "scripts" / "run.py"), "tick", "run", "--kind", kind],
        "WorkingDirectory": str(ROOT),
        "EnvironmentVariables": {"PATH": "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin", "HOME": str(Path.home())},
        "StandardOutPath": str(LOGS / f"tick-{kind}.log"),
        "StandardErrorPath": str(LOGS / f"tick-{kind}.log"),
        "RunAtLoad": True,          # a tick on login / when the agent is (re)loaded — i.e. when the laptop is open
        "ProcessType": "Background",
        "Nice": 5,
    }
    if kind == "free":
        base["StartInterval"] = FREE_INTERVAL_S      # hourly; a missed interval fires on wake
    else:
        base["StartCalendarInterval"] = {"Hour": PAID_HOUR, "Minute": PAID_MINUTE}   # daily, after the caps reset
    return base


def install() -> dict:
    """Write both LaunchAgents and load them. Idempotent."""
    LOGS.mkdir(exist_ok=True)
    out = {}
    for kind in ("free", "paid"):
        path = _agents_dir() / f"{LABEL}.{kind}.plist"
        path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["launchctl", "unload", str(path)], capture_output=True)
        path.write_bytes(plistlib.dumps(_plist(kind)))
        r = subprocess.run(["launchctl", "load", str(path)], capture_output=True, text=True)
        out[kind] = {"plist": str(path), "loaded": r.returncode == 0, "stderr": r.stderr.strip()[:200]}
    return out


def uninstall() -> dict:
    out = {}
    for kind in ("free", "paid"):
        path = _agents_dir() / f"{LABEL}.{kind}.plist"
        r = subprocess.run(["launchctl", "unload", str(path)], capture_output=True, text=True)
        if path.exists():
            path.unlink()
        out[kind] = {"unloaded": r.returncode == 0}
    return out


def status() -> dict:
    out = {"lock": LOCK.read_text().strip() if LOCK.exists() else None, "python": PYTHON}
    r = subprocess.run(["launchctl", "list"], capture_output=True, text=True)
    loaded = {ln.split("\t")[-1]: ln.split("\t")[0] for ln in r.stdout.splitlines() if LABEL in ln}
    out["pacing"] = {"paid_hourly": PAID_HOURLY, "used_today": _used_today(),
                     "per_day": {k: L[k] for k in ("facts", "describe", "story", "levels")}}
    for kind in ("free", "paid"):
        label = f"{LABEL}.{kind}"
        out[kind] = {
            "loaded": label in loaded, "pid": loaded.get(label) if loaded.get(label, "-") != "-" else None,
            "schedule": f"every {FREE_INTERVAL_S // 60} min + on login/wake" if kind == "free" else f"daily {PAID_HOUR:02d}:{PAID_MINUTE:02d} + on login",
            "last": last(kind), "log": str(LOGS / f"tick-{kind}.log"),
        }
    return out


__all__ = ["run", "run_free", "run_paid", "install", "uninstall", "status", "last"]
