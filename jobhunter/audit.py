"""The hourly audit — "each hour it should verify what was scraped" (Tejmul, 2026-10-07).

The tick scrapes every ten minutes; this checks the work once an hour, as a guardrail
rather than a progress bar. Each run answers:

    fill      per-field fill rates over the active companies, now vs one hour ago vs
              24 hours ago (description ≥60 chars, stage, amount, date, investors,
              valuation, hiring known / verified, careers url, people, confirmed referrers)
    output    companies / contacts / roles added in the last hour, fields written
              (extraction_write), companies that reached 100% complete
    rejects   what the guardrails refused in the last hour, by reason and by channel
    referrals ready referrers added, addresses found (by pattern / by source), companies
              that went from no ready referrer with an address to one, companies that became
              complete, re-verification and re-check results (referrals.audit_block)
    channels  each research channel (hosted Exa MCP, Jina, direct, GitHub, scrape.do,
              DuckDuckGo, OpenRouter): calls, failure rate, last success / failure, Exa MCP
              cooldowns, whether OpenRouter's daily ceiling was hit; the Agent Reach CLIs
              actually run (`--version`)
    sample    20 writes from the last hour re-validated against today's validators
    stall     nothing new in the last hour (no field written, no company, contact or role
              added) while the tick ran, or a channel failing
              more than 80% → a macOS notification and an ALERT line in logs/audit.log

Each report is stored in `setting` (audit:last, and audit:history — the last 48) and
appended to logs/audit.log as one JSON line. Runs under its own LaunchAgent,
com.zonuly.audit, every hour (`python scripts/run.py audit install`).

Layering: db, channels, guardrails, tick (its last-run record), notify. It never changes
a scraped value; the one thing it writes besides its report is the derived completeness
score (guardrails.refresh_completeness), so the numbers it reports are current.
"""

from __future__ import annotations

import json
import logging
import plistlib
import random
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import text
from sqlmodel import col, func, select

from jobhunter import CONFIG, ROOT, channels, guardrails
from jobhunter.db import (Company, Contact, ExtractionReject, ExtractionWrite, Job, engine, get_session,
                          get_setting, init_db, set_setting, utcnow)

log = logging.getLogger(__name__)

_A = CONFIG.get("audit") or {}
LABEL = "com.zonuly.audit"
LOGS = ROOT / "logs"
LOG_FILE = LOGS / "audit.log"
INTERVAL_S = int(_A.get("interval_seconds", 3600))
HISTORY = int(_A.get("history", 48))
SAMPLE = int(_A.get("sample", 20))
FAIL_ALERT = float(_A.get("channel_fail_alert", 0.8))
MIN_CALLS = int(_A.get("channel_min_calls", 8))
# channels whose failure means scraping is broken (a direct fetch failing is mostly dead
# company sites, so it is reported, never alerted on)
ALERT_CHANNELS = tuple(_A.get("alert_channels") or ("jina", "github", "openrouter", "scrapedo"))
#: Interchangeable search backends: one resting (Exa's free MCP is rate-limited by IP for
#: hours at a time) is routine while another answers, so they alert as a group — only
#: when web search as a whole is failing.
SEARCH_GROUP = tuple(_A.get("search_channels") or ("jina-ddg", "jina-startpage", "exa-mcp"))
KEY_LAST, KEY_HISTORY = "audit:last", "audit:history"

FIELDS = ("description", "funding_stage", "funding_amount", "funding_date", "investors", "valuation",
          "hiring_known", "hiring_verified", "careers_url", "has_people", "people_with_email",
          "referrers_3", "referrer_with_email", "complete")


# ------------------------------------------------------------------ measurements

def fill_rates(session) -> dict:
    """{companies, rates: {field: 0–1}, counts: {field: n}} over active companies."""
    rows = session.exec(select(Company).where(col(Company.status).is_(None))).all()
    n = len(rows) or 1
    people = dict(session.exec(select(Contact.company_id, func.count()).group_by(Contact.company_id)).all())
    with_email = dict(session.exec(select(Contact.company_id, func.count()).where(
        col(Contact.email).is_not(None)).group_by(Contact.company_id)).all())
    refs = guardrails.referrer_counts(session)
    counts = {f: 0 for f in FIELDS}
    for c in rows:
        r, re_ = refs.get(c.id, (0, 0))
        counts["description"] += bool(c.description and len(c.description) >= guardrails.DESC_MIN)
        counts["funding_stage"] += bool(c.funding_stage)
        counts["funding_amount"] += bool(c.funding_amount_usd_m)
        counts["funding_date"] += bool(c.funding_announced)
        counts["investors"] += bool(c.funding_investors and c.funding_investors not in ("[]", ""))
        counts["valuation"] += bool(c.valuation_usd_m)
        counts["hiring_known"] += c.hiring_status not in (None, "unchecked", "unreachable")
        counts["hiring_verified"] += c.hiring_status == "verified"
        counts["careers_url"] += bool(c.careers_url)
        counts["has_people"] += people.get(c.id, 0) > 0
        counts["people_with_email"] += with_email.get(c.id, 0) > 0
        counts["referrers_3"] += r >= guardrails.REFERRERS_WANTED
        counts["referrer_with_email"] += re_ >= 1
        counts["complete"] += (c.completeness or 0) >= 100
    avg = round(sum((c.completeness or 0) for c in rows) / n, 1)
    return {"companies": len(rows), "avg_completeness": avg, "counts": counts,
            "rates": {f: round(v / n, 4) for f, v in counts.items()}}


def contact_labels(session) -> dict:
    return {
        "affiliation": {k or "unlabelled": v for k, v in session.exec(
            select(Contact.affiliation, func.count()).group_by(Contact.affiliation)).all()},
        "address": {k or "none": v for k, v in session.exec(
            select(Contact.confidence, func.count()).group_by(Contact.confidence)).all()},
        "email_kind": {k or "none": v for k, v in session.exec(
            select(Contact.email_kind, func.count()).group_by(Contact.email_kind)).all()},
    }


def throughput(session, since: datetime) -> dict:
    def count(model, column) -> int:
        return session.exec(select(func.count()).select_from(model).where(col(column) >= since)).one()

    writes = dict(session.exec(select(ExtractionWrite.field, func.count()).where(
        col(ExtractionWrite.at) >= since).group_by(ExtractionWrite.field)).all())
    return {
        "companies_added": count(Company, Company.created_at),
        "contacts_added": count(Contact, Contact.created_at),
        "roles_added": count(Job, Job.scraped_at),
        "contacts_relabelled": count(Contact, Contact.affiliation_checked_at),
        "fields_written": sum(writes.values()),
        "writes_by_field": dict(sorted(writes.items(), key=lambda kv: -kv[1])),
    }


def rejects(session, since: datetime) -> dict:
    by_reason = session.exec(select(ExtractionReject.field, ExtractionReject.reason, func.count()).where(
        col(ExtractionReject.at) >= since).group_by(ExtractionReject.field, ExtractionReject.reason)).all()
    by_channel = dict(session.exec(select(ExtractionReject.channel, func.count()).where(
        col(ExtractionReject.at) >= since).group_by(ExtractionReject.channel)).all())

    def norm(reason: str) -> str:
        # "too short (41 chars, need 60)" and "too short (12 chars…)" are one reason
        return reason.split(" (")[0].split(": replaced by")[0].split(": kept")[0][:80]

    grouped: dict[str, int] = {}
    for fld, reason, n in by_reason:
        key = f"{fld}: {norm(reason)}"
        grouped[key] = grouped.get(key, 0) + n
    return {"total": sum(grouped.values()),
            "by_reason": dict(sorted(grouped.items(), key=lambda kv: -kv[1])[:25]),
            "by_channel": {k or "unknown": v for k, v in sorted(by_channel.items(), key=lambda kv: -kv[1])}}


def openrouter_health(since: datetime) -> dict:
    """From the model ledger (llm_call): calls, failures, and whether a daily ceiling
    (ours in INR, or the free tier's per-day limit) was hit today."""
    out: dict = {"ok": 0, "fail": 0, "daily_ceiling_hit": False, "last_error": None}
    try:
        with engine.connect() as conn:
            row = conn.execute(text("SELECT SUM(ok), SUM(1 - ok) FROM llm_call WHERE ts >= :s"),
                               {"s": since.isoformat()}).one()
            out["ok"], out["fail"] = int(row[0] or 0), int(row[1] or 0)
            day = utcnow().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
            hit = conn.execute(text(
                "SELECT error, ts FROM llm_call WHERE ok = 0 AND ts >= :d AND (error LIKE '%per-day%' "
                "OR error LIKE '%per day%' OR error LIKE '%free-models-per-day%' OR error LIKE '%daily%') "
                "ORDER BY ts DESC LIMIT 1"), {"d": day}).first()
            last = conn.execute(text("SELECT error, ts FROM llm_call WHERE ok = 0 ORDER BY ts DESC LIMIT 1")).first()
            okl = conn.execute(text("SELECT ts FROM llm_call WHERE ok = 1 ORDER BY ts DESC LIMIT 1")).first()
    except Exception as e:  # noqa: BLE001 — no ledger yet is "no calls"
        out["error"] = str(e)[:120]
        return out
    out["daily_ceiling_hit"] = bool(hit)
    if hit:
        out["daily_ceiling"] = {"at": hit[1][:19] + "Z", "error": (hit[0] or "")[:160]}
    if last:
        out["last_error"], out["last_fail_at"] = (last[0] or "")[:200], last[1][:19] + "Z"
    out["last_ok_at"] = okl[0][:19] + "Z" if okl else None
    try:
        from jobhunter import openrouter

        b = openrouter.budget_status()
        out["budget_over_cap"] = b["over_cap"]
        out["daily_ceiling_hit"] = out["daily_ceiling_hit"] or b["over_cap"]
    except Exception:  # noqa: BLE001
        pass
    tot = out["ok"] + out["fail"]
    out["fail_rate"] = round(out["fail"] / tot, 3) if tot else None
    return out


def tools() -> dict:
    """The Agent Reach CLIs, actually run — `which` is not proof of health."""
    from jobhunter.research import backends

    out = {}
    for exe in ("mcporter", "gh", "yt-dlp"):
        try:
            status, detail = backends.probe(exe, timeout=20)
        except Exception as e:  # noqa: BLE001
            status, detail = "error", str(e)[:120]
        out[exe] = {"status": status, "detail": detail[:80]}
    return out


def github_budget() -> dict:
    """GitHub's core budget (the /rate_limit call itself is free)."""
    import httpx

    from jobhunter.contacts import github_miner

    try:
        r = httpx.get(f"{github_miner.API}/rate_limit", headers=github_miner.headers(), timeout=15)
        core = (r.json().get("resources") or {}).get("core") or {}
        return {"limit": core.get("limit"), "remaining": core.get("remaining"),
                "resets": datetime.fromtimestamp(core["reset"]).isoformat(timespec="minutes") if core.get("reset") else None}
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {str(e)[:100]}"}


def _aware(stamp: str | None) -> str | None:
    """A local naive timestamp → ISO with the local offset, so the dashboard reads it right."""
    if not stamp:
        return None
    try:
        dt = datetime.fromisoformat(str(stamp).replace(" ", "T"))
    except ValueError:
        return str(stamp)
    return (dt if dt.tzinfo else dt.astimezone()).isoformat(timespec="seconds")


def channel_health() -> dict:
    hour = channels.summary(1)
    day = channels.summary(24)
    out: dict = {}
    for ch in sorted(set(hour) | set(day)):
        h, d = hour.get(ch) or {}, day.get(ch) or {}
        out[ch] = {"hour": {k: h.get(k) for k in ("ok", "fail", "cooldowns", "fail_rate")},
                   "day": {k: d.get(k) for k in ("ok", "fail", "cooldowns", "fail_rate")},
                   "last_ok_at": _aware(d.get("last_ok_at")), "last_fail_at": _aware(d.get("last_fail_at")),
                   "last_error": d.get("last_error")}
    return out


# ------------------------------------------------------------------ re-validate a sample

def _revalidate(session, w: ExtractionWrite) -> tuple[bool, str]:
    """Does what the row holds NOW still pass the validator that admitted it?"""
    if w.contact_id:
        p = session.get(Contact, w.contact_id)
        if p is None:
            return False, "contact gone"
        if w.field == "contact.name":
            c = guardrails.person_name(p.name)
            return c.ok, c.reason
        return True, ""
    c = session.get(Company, w.company_id) if w.company_id else None
    if c is None:
        return False, "company gone"
    if w.field == "description":
        chk = guardrails.description(c.description, name=c.name, domain=c.domain, strict=False)
        return chk.ok, chk.reason
    if w.field == "funding_stage":
        chk = guardrails.funding_stage(c.funding_stage)
        return chk.ok, chk.reason
    if w.field == "funding_amount_usd_m":
        chk = guardrails.funding_amount(c.funding_amount_usd_m, c.funding_evidence) if c.funding_amount_usd_m else None
        return (chk.ok, chk.reason) if chk else (False, "cleared since")
    if w.field == "funding_announced":
        chk = guardrails.funding_date(c.funding_announced, founded_year=c.founded_year)
        return chk.ok, chk.reason
    if w.field == "funding_investors":
        chk = guardrails.investors(c.funding_investors)
        return chk.ok, chk.reason
    if w.field == "valuation_usd_m":
        chk = guardrails.valuation(c.valuation_usd_m, amount=c.funding_amount_usd_m, total=c.funding_total_usd_m,
                                   stage=c.funding_stage) if c.valuation_usd_m else None
        return (chk.ok, chk.reason) if chk else (False, "cleared since")
    if w.field == "hiring_status":
        try:
            roles = json.loads(c.hiring_roles or "[]")
        except (TypeError, ValueError):
            roles = []
        chk = guardrails.hiring(c.hiring_status, roles=roles, careers_url=c.careers_url,
                                checked_at=c.hiring_checked_at, domain=c.domain)
        return chk.ok, chk.reason
    return True, ""


def sample(session, since: datetime, n: int = SAMPLE) -> dict:
    ids = session.exec(select(ExtractionWrite.id).where(col(ExtractionWrite.at) >= since)).all()
    pick = random.sample(list(ids), min(n, len(ids)))
    out = {"checked": 0, "passed": 0, "failed": []}
    for wid in pick:
        w = session.get(ExtractionWrite, wid)
        ok, why = _revalidate(session, w)
        out["checked"] += 1
        if ok:
            out["passed"] += 1
        else:
            out["failed"].append({"id": w.id, "company_id": w.company_id, "field": w.field,
                                  "value": (w.value or "")[:80], "why": why, "channel": w.channel})
    return out


# ------------------------------------------------------------------ the report

def _history() -> list[dict]:
    with get_session() as s:
        raw = get_setting(s, KEY_HISTORY, "")
    try:
        data = json.loads(raw) if raw else []
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def _closest(history: list[dict], target: datetime, tolerance_h: float) -> dict | None:
    best, gap = None, None
    for h in history:
        try:
            at = datetime.fromisoformat(h["at"])
        except (KeyError, ValueError):
            continue
        at = at if at.tzinfo else at.astimezone()
        d = abs((at - target).total_seconds()) / 3600
        if d <= tolerance_h and (gap is None or d < gap):
            best, gap = h, d
    return best


def _delta(now: dict, then: dict | None) -> dict | None:
    if not then:
        return None
    a, b = now["rates"], (then.get("fill") or {}).get("rates") or {}
    return {f: round(a.get(f, 0) - b.get(f, 0), 4) for f in FIELDS if f in b}


def stall_check(*, progress_last_hour: int, tick_ran: bool, channel_rates: dict[str, dict],
                openrouter: dict | None = None) -> list[str]:
    """The alerts. Pure, so the tests can call it: nothing new (fields written + rows
    added) while the tick ran, or an alert channel failing more than FAIL_ALERT of at
    least MIN_CALLS calls."""
    alerts: list[str] = []
    if tick_ran and progress_last_hour == 0:
        alerts.append("STALL: the tick ran in the last hour but nothing new was written")
    for ch, h in channel_rates.items():
        if ch not in ALERT_CHANNELS:
            continue
        ok, fail = int(h.get("ok") or 0), int(h.get("fail") or 0)
        if ok + fail >= MIN_CALLS and fail / (ok + fail) > FAIL_ALERT:
            alerts.append(f"CHANNEL: {ch} failed {fail} of {ok + fail} calls in the last hour")
    s_ok = sum(int((channel_rates.get(ch) or {}).get("ok") or 0) for ch in SEARCH_GROUP)
    s_fail = sum(int((channel_rates.get(ch) or {}).get("fail") or 0) for ch in SEARCH_GROUP)
    if s_ok + s_fail >= MIN_CALLS and s_fail / (s_ok + s_fail) > FAIL_ALERT:
        alerts.append(f"CHANNEL: web search failed {s_fail} of {s_ok + s_fail} calls in the last hour "
                      f"across {', '.join(SEARCH_GROUP)}")
    if openrouter:
        ok, fail = int(openrouter.get("ok") or 0), int(openrouter.get("fail") or 0)
        if ok + fail >= MIN_CALLS and fail / (ok + fail) > FAIL_ALERT:
            alerts.append(f"CHANNEL: openrouter failed {fail} of {ok + fail} calls in the last hour")
    return alerts


def _tick_ran(since: datetime) -> tuple[bool, dict | None]:
    from jobhunter import tick

    last = tick.last("free")
    if not last:
        return False, None
    try:
        at = datetime.fromisoformat(last.get("finished") or last.get("at"))
    except (TypeError, ValueError):
        return False, last
    return at >= since, {"at": _aware(last.get("at")), "finished": _aware(last.get("finished"))}


def run(*, notify: bool = True, probe_tools: bool = True) -> dict:
    """One audit. Stores and returns the report; notifies on an alert."""
    init_db()
    now_local = datetime.now().astimezone()
    since = utcnow() - timedelta(hours=1)
    with get_session() as s:
        guardrails.refresh_completeness(s)
        fill = fill_rates(s)
        report: dict = {
            "at": now_local.isoformat(timespec="seconds"),
            "fill": fill,
            "labels": contact_labels(s),
            "last_hour": throughput(s, since),
            "rejects": rejects(s, since),
            "rejects_24h": rejects(s, utcnow() - timedelta(hours=24))["total"],
            "sample": sample(s, since),
        }
        # referrals are the main thing (2026-10-08): who was added, which addresses were
        # found and how, which companies gained an address or became complete, re-checks
        try:
            from jobhunter import referrals

            report["referrals"] = referrals.audit_block(s, since)
        except Exception as e:  # noqa: BLE001 — a block failing must not lose the audit
            report["referrals"] = {"error": f"{type(e).__name__}: {str(e)[:160]}"}
    history = _history()
    prev_hour = _closest(history, now_local - timedelta(hours=1), 0.75)
    prev_day = _closest(history, now_local - timedelta(hours=24), 2.0)
    report["delta_hour"] = _delta(fill, prev_hour)
    report["delta_day"] = _delta(fill, prev_day)
    if prev_hour:
        report["last_hour"]["companies_completed"] = fill["counts"]["complete"] - (
            (prev_hour.get("fill") or {}).get("counts") or {}).get("complete", 0)
    ch = channel_health()
    orh = openrouter_health(since)
    report["channels"] = {"research": ch, "openrouter": orh, "github_budget": github_budget(),
                          "exa_mcp_cooldowns_hour": ((ch.get("exa-mcp") or {}).get("hour") or {}).get("cooldowns") or 0}
    if probe_tools:
        report["channels"]["tools"] = tools()
    ran, tick_last = _tick_ran(datetime.now() - timedelta(hours=1))
    report["tick"] = {"ran_last_hour": ran, "last": tick_last}
    lh = report["last_hour"]
    progress = lh["fields_written"] + lh["companies_added"] + lh["contacts_added"] + lh["roles_added"]
    report["alerts"] = stall_check(progress_last_hour=progress, tick_ran=ran,
                                   channel_rates={k: v["hour"] for k, v in ch.items()}, openrouter=orh)
    report["status"] = "alert" if report["alerts"] else "ok"
    _store(report, history)
    if report["alerts"]:
        msg = "; ".join(report["alerts"])
        log.warning("audit ALERT: %s", msg)
        _log_line({"at": report["at"], "ALERT": report["alerts"]})
        if notify:
            try:
                from jobhunter import notify as notifier

                notifier.send("ZoNuLy scraping needs a look", msg[:180], "hourly audit")
            except Exception as e:  # noqa: BLE001 — a notification must never fail the audit
                log.debug("audit notification failed: %s", e)
    return report


def _compact(report: dict) -> dict:
    """What the history keeps per hour: enough for the deltas and the trend, not the samples."""
    return {"at": report["at"], "status": report["status"], "alerts": report["alerts"],
            "fill": {k: report["fill"][k] for k in ("companies", "avg_completeness", "counts", "rates")},
            "last_hour": {k: v for k, v in report["last_hour"].items() if k != "writes_by_field"},
            "rejects": {"total": report["rejects"]["total"]},
            "referrals": {k: v for k, v in (report.get("referrals") or {}).items()
                          if k in ("ready_added", "ready_added_with_email", "companies_with_email",
                                   "companies_gained_email", "companies_became_complete",
                                   "linkedin_added", "emails_found", "companies_reached_target")}}


def _store(report: dict, history: list[dict]) -> None:
    history = (history + [_compact(report)])[-HISTORY:]
    with get_session() as s:
        set_setting(s, KEY_LAST, json.dumps(report, default=str))
        set_setting(s, KEY_HISTORY, json.dumps(history, default=str))
    _log_line(_compact(report))


def _log_line(obj: dict) -> None:
    LOGS.mkdir(exist_ok=True)
    with LOG_FILE.open("a") as fh:
        fh.write(json.dumps(obj, default=str) + "\n")


def latest() -> dict:
    """{last, history} for the API and the dashboard."""
    with get_session() as s:
        raw = get_setting(s, KEY_LAST, "")
    try:
        last = json.loads(raw) if raw else None
    except json.JSONDecodeError:
        last = None
    return {"last": last, "history": _history()}


# ------------------------------------------------------------------ launchd

def _plist() -> dict:
    from jobhunter import tick

    return {
        "Label": LABEL,
        "ProgramArguments": [tick.PYTHON, str(ROOT / "scripts" / "run.py"), "audit", "run"],
        "WorkingDirectory": str(ROOT),
        "EnvironmentVariables": {"PATH": f"{Path.home()}/.local/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin",
                                 "HOME": str(Path.home())},
        "StandardOutPath": str(LOGS / "audit-run.log"),
        "StandardErrorPath": str(LOGS / "audit-run.log"),
        "StartInterval": INTERVAL_S,
        "RunAtLoad": False,
        "ProcessType": "Background",
        "Nice": 10,
    }


def install() -> dict:
    LOGS.mkdir(exist_ok=True)
    path = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["launchctl", "unload", str(path)], capture_output=True)
    path.write_bytes(plistlib.dumps(_plist()))
    r = subprocess.run(["launchctl", "load", str(path)], capture_output=True, text=True)
    return {"plist": str(path), "loaded": r.returncode == 0, "stderr": r.stderr.strip()[:200],
            "every": f"{INTERVAL_S // 60} min"}


def uninstall() -> dict:
    path = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    r = subprocess.run(["launchctl", "unload", str(path)], capture_output=True, text=True)
    if path.exists():
        path.unlink()
    return {"unloaded": r.returncode == 0}


def status() -> dict:
    r = subprocess.run(["launchctl", "list"], capture_output=True, text=True)
    loaded = any(LABEL in ln for ln in r.stdout.splitlines())
    last = latest()["last"]
    return {"loaded": loaded, "every": f"{INTERVAL_S // 60} min", "log": str(LOG_FILE),
            "last_at": (last or {}).get("at"), "last_status": (last or {}).get("status"),
            "alerts": (last or {}).get("alerts")}


__all__ = ["run", "latest", "install", "uninstall", "status", "stall_check", "fill_rates"]
