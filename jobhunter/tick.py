"""The tick — one bounded slice of the pipeline, run by launchd, so nobody has to ask.

FINAL-PLAN-V3 §6 / CHOKEPOINTS §9.1.8: an hourly job that takes a lock, does the most
useful bounded piece of work it can inside the day's budgets, and exits. Two kinds:

    free   keyless and unmetered — YC directory, job boards, roles, hiring verification,
           the people hunt, pay re-read, grading. Hourly, and on wake.
    paid   the capped channels — Exa (company facts, discovery), X (posts), and the model
           passes (descriptions, origin stories). Daily just after the caps reset, and on
           login. Every step reads its budget first and spends only what is left.

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
    "exa_queries": int(_T.get("exa_discovery_queries_per_day", 6)),
}


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


def run_free(budget_s: int = 1800) -> dict:
    """Keyless work, in the order the 2,000-target needs it: leads, then proof, then fresh companies."""
    from jobhunter import harvest, hiring_verify, targeting

    s: dict = {"kind": "free", "started": datetime.now().isoformat(timespec="seconds")}
    t0 = time.time()
    _step(s, "people", harvest.find_people, limit=L["people"])
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
    s["status"] = harvest.status()
    return s


def run_paid() -> dict:
    """The capped channels, each reading its budget first."""
    from jobhunter import enrich, harvest
    from jobhunter.research import web, x_search

    s: dict = {"kind": "paid", "started": datetime.now().isoformat(timespec="seconds"),
               "budgets_before": {"exa": web.exa_budget(), "x": x_search.budget()}}
    # discovery first: a handful of rotating Exa queries a day keeps new companies arriving
    day = datetime.now().timetuple().tm_yday
    pool = harvest.DEFAULT_EXA_QUERIES
    todays = [pool[(day * L["exa_queries"] + i) % len(pool)] for i in range(L["exa_queries"])]
    _step(s, "exa_discovery", harvest.admit_exa, todays)
    if x_search.session_present():
        _step(s, "x_posts", harvest.admit_x)
    _step(s, "probe", harvest.probe_ats)
    _step(s, "roles", harvest.scrape_roles)
    # the company card (headcount, HQ, round) for companies with roles — the pay bottleneck
    facts_n = min(L["facts"], max(0, web.exa_budget()["left"] - 5))
    if facts_n:
        _step(s, "facts", lambda: {"companies": len(enrich.facts_pending(limit=facts_n))})
    # the model passes: descriptions for bare sites, then origin stories
    _step(s, "describe", lambda: _count(enrich.enrich_pending(limit=L["describe"], use_search=False, missing="description")))
    _step(s, "story", lambda: _count(enrich.enrich_pending(limit=L["story"], use_search=False, missing="story")))
    # levels.fyi pay via scrape.do — the fix for "pay unknown" on mid-size companies
    from jobhunter import levels

    _step(s, "levels", lambda: levels.lookup_pending(limit=L["levels"]))
    from jobhunter import targeting

    _step(s, "grade", targeting.grade_companies, regrade=True)
    _step(s, "kg", _kg_sync)
    s["budgets_after"] = {"exa": web.exa_budget(), "x": x_search.budget()}
    s["status"] = harvest.status()
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
    for kind in ("free", "paid"):
        label = f"{LABEL}.{kind}"
        out[kind] = {
            "loaded": label in loaded, "pid": loaded.get(label) if loaded.get(label, "-") != "-" else None,
            "schedule": f"every {FREE_INTERVAL_S // 60} min + on login/wake" if kind == "free" else f"daily {PAID_HOUR:02d}:{PAID_MINUTE:02d} + on login",
            "last": last(kind), "log": str(LOGS / f"tick-{kind}.log"),
        }
    return out


__all__ = ["run", "run_free", "run_paid", "install", "uninstall", "status", "last"]
