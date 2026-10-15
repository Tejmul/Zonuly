"""The watchdog — a cheap check that something is stuck, so somebody hears about it.

2026-10-15: a slow job held the SQLite write lock for hours, the referral job did zero work
and the tick hung for seven hours, and nothing told anyone. This answers, in well under a
second and without touching the database's write path for long:

    stuck     a job's lock file names a live process that has run past its limit while its
              log has been silent for `watchdog.idle_minutes` (or past three times its limit,
              silent or not). CPU% and process state are reported so "waiting on a lock /
              a socket" (0% CPU, state S) is visible in the mail.
    stale     a job that should run every few minutes has not finished one for a while, no
              live process holds its lock, and the laptop has been awake long enough that
              sleep is not the explanation.
    db        the database refused a write lock for `watchdog.db_locked_minutes` across
              checks (the first refusal is remembered in data/watchdog.json — not in the DB,
              which is the thing that is locked).
    outreach  the bounce brake is on, sends failed, or Gmail is refusing the sign-in.

`check()` returns issues as {key, title, detail}; it never sends anything. notify_bot turns
them into URGENT mail (deduplicated per key). With `watchdog.kill_stuck: true` a stuck job's
process is sent SIGTERM — off by default: the default is to alert, not to act.

Layering: db, tick (lock paths and last-run records), outreach (read-only). Nothing imports it
but notify_bot, audit and the CLI.
"""

from __future__ import annotations

import json
import logging
import os
import re
import signal
import sqlite3
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path

from jobhunter import CONFIG, ROOT

log = logging.getLogger(__name__)

_W = CONFIG.get("watchdog") or {}
KILL_STUCK = bool(_W.get("kill_stuck", False))
IDLE_MINUTES = float(_W.get("idle_minutes", 20))
DB_LOCKED_MINUTES = float(_W.get("db_locked_minutes", 10))
_MAX = {"free": 60, "paid": 90, "send": 20, "referrals": 45, **(_W.get("max_minutes") or {})}
_STALE = {"free": 60, "send": 30, "referrals": 60, "audit": 150, **(_W.get("stale_minutes") or {})}
STATE_FILE = ROOT / "data" / "watchdog.json"
LOGS = ROOT / "logs"

#: (job, lock file, log file) — the paid tick shares the free tick's lock; the holder's
#: command line says which one it is
JOBS = (
    ("free", ROOT / "data" / "tick.lock", LOGS / "tick-free.log"),
    ("send", ROOT / "data" / "send.lock", LOGS / "tick-send.log"),
    ("referrals", ROOT / "data" / "referrals.lock", LOGS / "referrals.log"),
)
_LOG_OF = {"free": LOGS / "tick-free.log", "paid": LOGS / "tick-paid.log",
           "send": LOGS / "tick-send.log", "referrals": LOGS / "referrals.log"}


# ------------------------------------------------------------------ small probes

def _read_lock(path: Path) -> tuple[int | None, datetime | None]:
    """The "<pid> <iso start>" line a tick lock holds."""
    try:
        line = path.read_text().strip().splitlines()[0]
        pid, stamp = line.split(maxsplit=1)
        return int(pid), datetime.fromisoformat(stamp.strip())
    except (OSError, ValueError, IndexError):
        return None, None


def _proc(pid: int) -> dict | None:
    """{cpu, state, etime, command} for a live pid, None when it is gone."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return None
    except PermissionError:
        pass
    try:
        out = subprocess.run(["ps", "-o", "pcpu=,state=,etime=,command=", "-p", str(pid)],
                             capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return {"cpu": None, "state": "?", "etime": "?", "command": ""}
    if not out:
        return None
    parts = out.split(None, 3)
    if len(parts) < 4:
        return {"cpu": None, "state": "?", "etime": "?", "command": out}
    try:
        cpu = float(parts[0])
    except ValueError:
        cpu = None
    return {"cpu": cpu, "state": parts[1], "etime": parts[2], "command": parts[3]}


def _job_of(command: str, default: str) -> str:
    if "referrals" in command:
        return "referrals"
    m = re.search(r"--kind[ =](\w+)", command)
    return m.group(1) if m else default


def _awake_since() -> datetime | None:
    """When the machine last woke (or booted). None when it cannot be read (not macOS)."""
    best = None
    for key in ("kern.waketime", "kern.boottime"):
        try:
            out = subprocess.run(["sysctl", "-n", key], capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        m = re.search(r"sec = (\d+)", out)
        if m and int(m.group(1)) > 0:
            at = datetime.fromtimestamp(int(m.group(1)))
            best = at if best is None or at > best else best
    return best


def _state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    try:
        STATE_FILE.parent.mkdir(exist_ok=True)
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, default=str))
        tmp.replace(STATE_FILE)
    except OSError as e:
        log.debug("watchdog state not saved: %s", e)


def _mins(td: timedelta) -> int:
    return int(td.total_seconds() // 60)


# ------------------------------------------------------------------ the checks

def stuck_jobs(now: datetime | None = None, *, kill: bool | None = None) -> list[dict]:
    """Live lock holders past their limit with a silent log."""
    now = now or datetime.now()
    kill = KILL_STUCK if kill is None else kill
    issues = []
    for default, lock, _log in JOBS:
        pid, started = _read_lock(lock)
        if not pid or not started:
            continue
        p = _proc(pid)
        if p is None or ("run.py" not in p["command"] and "jobhunter" not in p["command"]):
            continue   # the holder is gone (the lock line outlives it) or the pid was reused
        job = _job_of(p["command"], default)
        elapsed = now - started
        logf = _LOG_OF.get(job, _log)
        try:
            idle = now - datetime.fromtimestamp(logf.stat().st_mtime)
        except OSError:
            idle = elapsed
        limit = float(_MAX.get(job, 60))
        silent = idle >= timedelta(minutes=IDLE_MINUTES)
        if elapsed < timedelta(minutes=limit) or (not silent and elapsed < timedelta(minutes=3 * limit)):
            continue
        waiting = p["cpu"] is not None and p["cpu"] < 1.0
        detail = (f"The {job} job (pid {pid}) has held {lock.name} for {_mins(elapsed)} min "
                  f"(limit {int(limit)}); its log {logf.name} last moved {_mins(idle)} min ago. "
                  f"CPU {p['cpu']}%, state {p['state']}, elapsed {p['etime']}"
                  f"{' — it looks blocked waiting (a lock or a socket), not working' if waiting else ''}.\n"
                  f"Command: {p['command'][:200]}")
        action = None
        if kill:
            try:
                os.kill(pid, signal.SIGTERM)
                action = f"sent SIGTERM to pid {pid} (watchdog.kill_stuck is on)"
            except OSError as e:
                action = f"could not stop pid {pid}: {e}"
            detail += f"\nAction: {action}"
        else:
            detail += (f"\nNothing was killed (watchdog.kill_stuck is off). To stop it yourself: kill {pid}"
                       " — every stage commits per item, so the next run resumes.")
        issues.append({"key": f"stuck:{job}", "title": f"{job} job stuck for {_mins(elapsed)} min",
                       "detail": detail, "pid": pid, "killed": bool(action and action.startswith("sent"))})
    return issues


def _last_finished(kind: str) -> datetime | None:
    from jobhunter.db import get_session, get_setting

    key = "audit:last" if kind == "audit" else f"tick:last:{kind}"
    with get_session() as s:
        raw = get_setting(s, key, "")
    try:
        d = json.loads(raw) if raw else None
    except json.JSONDecodeError:
        # a record written before 2026-10-16 could be cut mid-JSON; its "at" leads it
        m = re.match(r'\{"at": "([^"]+)"', raw)
        d = {"at": m.group(1)} if m else None
    if not isinstance(d, dict):
        return None
    stamp = d.get("finished") or d.get("at")
    try:
        at = datetime.fromisoformat(str(stamp))
    except (TypeError, ValueError):
        return None
    return at.astimezone().replace(tzinfo=None) if at.tzinfo else at


def stale_jobs(now: datetime | None = None, *, awake_since: datetime | None = None) -> list[dict]:
    """Jobs that have not finished a run for a while, with nobody holding their lock."""
    now = now or datetime.now()
    awake_since = awake_since if awake_since is not None else _awake_since()
    held = set()
    for default, lock, _log in JOBS:
        pid, _ = _read_lock(lock)
        p = _proc(pid) if pid else None
        if p and ("run.py" in p["command"] or "jobhunter" in p["command"]):
            held.add(_job_of(p["command"], default))
    issues = []
    for kind, minutes in _STALE.items():
        if kind in held or not minutes:
            continue
        try:
            last = _last_finished(kind)
        except Exception as e:  # noqa: BLE001 — a locked/odd DB must not stop the other checks
            log.debug("last run of %s unreadable: %s", kind, e)
            continue
        if last is None:
            continue
        limit = timedelta(minutes=float(minutes))
        if now - last < limit:
            continue
        if awake_since and now - awake_since < limit:
            continue   # the laptop was asleep: launchd will catch up on its own
        issues.append({"key": f"stale:{kind}", "title": f"{kind} job has not run for {_mins(now - last)} min",
                       "detail": f"The last {kind} run finished at {last:%Y-%m-%d %H:%M} — {_mins(now - last)} min "
                                 f"ago (expected within {int(float(minutes))}). No process holds its lock, so "
                                 f"launchd is not starting it or it is failing at start. Check "
                                 f"`python scripts/run.py tick status` and logs/."})
    return issues


def db_locked(now: datetime | None = None, *, wait_s: float = 5.0, db_path: Path | None = None) -> list[dict]:
    """Ask SQLite for a write lock for a few seconds; remember when it first refused."""
    from jobhunter.db import DB_PATH

    now = now or datetime.now()
    path = db_path or DB_PATH
    state = _state()
    locked, err = False, ""
    try:
        conn = sqlite3.connect(str(path), timeout=wait_s)
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.rollback()
        finally:
            conn.close()
    except sqlite3.OperationalError as e:
        locked, err = "locked" in str(e) or "busy" in str(e), str(e)
    if not locked:
        if state.pop("db_locked_since", None):
            _save_state(state)
        return []
    since_raw = state.get("db_locked_since")
    try:
        since = datetime.fromisoformat(since_raw) if since_raw else None
    except ValueError:
        since = None
    if since is None:
        state["db_locked_since"] = now.isoformat(timespec="seconds")
        _save_state(state)
        return []
    if now - since < timedelta(minutes=DB_LOCKED_MINUTES):
        return []
    holders = []
    for default, lock, _l in JOBS:
        pid, started = _read_lock(lock)
        p = _proc(pid) if pid else None
        if p:
            holders.append(f"  pid {pid} ({_job_of(p['command'], default)}, since {started:%H:%M}, CPU {p['cpu']}%)")
    return [{"key": "db-locked", "title": f"database write-locked for {_mins(now - since)} min",
             "detail": f"SQLite has refused a write lock since {since:%H:%M} ({err}). Every job that writes is "
                       f"waiting. Live job processes:\n" + ("\n".join(holders) or "  none of the tick jobs — "
                       "look for a long script or the API server (`lsof jobhunter.db`).")}]


def outreach_problems(now: datetime | None = None) -> list[dict]:
    """Bounce brake on, recent send failures, Gmail refusing the sign-in."""
    from sqlmodel import col, select

    from jobhunter.db import Email, get_session, get_setting
    from jobhunter.outreach import suppress

    now = now or datetime.now()
    issues = []
    with get_session() as s:
        why = suppress.brake(s)
        if why:
            issues.append({"key": "bounce-brake", "title": "bounce brake tripped",
                           "detail": f"{why}. Look at the bounced addresses (Outreach → Track) before "
                                     "approving more guessed ones."})
        from jobhunter.db import utcnow

        since = utcnow() - timedelta(hours=72)
        failed = s.exec(select(Email).where(Email.status == "failed", col(Email.approved_at) >= since)).all()
        failed = [e for e in failed if not (e.error or "").startswith("bounced")]
        if failed:
            newest = max(e.id for e in failed)
            lines = "\n".join(f"  #{e.id} to {e.to_email}: {(e.error or '')[:160]}" for e in failed[-10:])
            issues.append({"key": f"send-failed:{newest}", "title": f"{len(failed)} send failure(s)",
                           "detail": f"These approved emails failed to send (last 72 h):\n{lines}"})
        err = get_setting(s, "outreach:last_poll_error", "")
    if re.search(r"refused the sign-in|app password missing|GmailNotConfigured|AUTHENTICATIONFAILED|"
                 r"Invalid credentials|Username and Password not accepted", err or "", re.I):
        issues.append({"key": "gmail-auth", "title": "Gmail sign-in failing",
                       "detail": f"The reply poll could not sign in to Gmail: {err[:300]}\nRe-create the app "
                                 "password (myaccount.google.com/apppasswords) and paste it in Settings → Email."})
    return issues


def check(now: datetime | None = None, *, kill: bool | None = None, include_outreach: bool = True) -> list[dict]:
    """Every check; one failing check is reported as an issue of its own, never raised."""
    t = time.time()
    issues: list[dict] = []
    probes = [("stuck", lambda: stuck_jobs(now, kill=kill)), ("db", lambda: db_locked(now)),
              ("stale", lambda: stale_jobs(now))]
    if include_outreach:
        probes.append(("outreach", lambda: outreach_problems(now)))
    for name, fn in probes:
        try:
            issues += fn()
        except Exception as e:  # noqa: BLE001
            log.warning("watchdog %s check failed: %s", name, e)
            if "locked" in str(e):
                issues.append({"key": "db-locked-read", "title": "database locked during the watchdog",
                               "detail": f"The {name} check could not read the database: {str(e)[:200]}"})
    log.debug("watchdog: %d issue(s) in %.1fs", len(issues), time.time() - t)
    return issues


__all__ = ["check", "stuck_jobs", "stale_jobs", "db_locked", "outreach_problems", "KILL_STUCK"]
