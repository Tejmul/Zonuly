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
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from jobhunter import CONFIG, ROOT

log = logging.getLogger(__name__)

_T = CONFIG.get("tick") or {}
#: the whole-table recomputes run at most this often (2026-10-29: ~2.5 min of every tick)
RECOMPUTE_MINUTES = int(_T.get("recompute_minutes", 60))
LOCK = ROOT / "data" / "tick.lock"
# 2026-10-24: the paid tick (scrape.do + metered Exa) took tick.lock too, so with the free tick now
# running back to back it skipped every 06:15 start from 10-19 on ("another tick is running")
PAID_LOCK = ROOT / "data" / "tick-paid.lock"
LOGS = ROOT / "logs"
LABEL = "com.zonuly.tick"
PYTHON = str(ROOT / ".venv" / "bin" / "python")
FREE_INTERVAL_S = int(_T.get("free_interval_seconds", 3600))
PAID_HOUR = int(_T.get("paid_hour", 6))
PAID_MINUTE = int(_T.get("paid_minute", 15))
# per-tick limits: small enough that a tick finishes well inside its interval
L = {
    "verify": int(_T.get("verify_per_tick", 60)),
    "hunter": int(_T.get("hunter_per_tick", 2)),       # 2026-10-19: Hunter lookups per tick (25/month free)
    "apify": int(_T.get("apify_per_tick", 1)),         # 2026-10-19: People tabs bought per tick (cap: actors.apify.daily_usd)
    "people": int(_T.get("people_per_tick", 40)),
    # 2026-10-08 "referrals are the main thing": the people step runs first and is the
    # dominant work — at most this many seconds of it per tick (the free channels pace it)
    "people_budget": int(_T.get("people_budget_seconds", 1500)),
    "reverify": int(_T.get("reverify_per_tick", 150)),
    "revalidate": int(_T.get("revalidate_per_tick", 30)),
    "describe": int(_T.get("describe_per_tick", 40)),
    "story": int(_T.get("story_per_tick", 40)),
    "facts": int(_T.get("facts_per_tick", 90)),
    "levels": int(_T.get("levels_per_tick", 60)),
    "linkedin": int(_T.get("linkedin_per_tick", 15)),
    "exa_queries": int(_T.get("exa_discovery_queries_per_day", 6)),
    "websites": int(_T.get("websites_per_tick", 30)),
    "facts_keyless": int(_T.get("facts_keyless_per_tick", 20)),
    "careers": int(_T.get("careers_per_tick", 40)),
    # GitHub affiliation evidence (contacts/affiliation.py): companies per tick, inside the
    # hourly GitHub budget minus a reserve the people hunt keeps
    "affiliation": int(_T.get("affiliation_per_tick", 20)),
    "affiliation_reserve": int(_T.get("affiliation_github_reserve", 1500)),
    # keyless discovery once the fill queues are exhausted (backoff), queries per tick
    "discover_queries": int(_T.get("discover_queries_per_tick", 4)),
    "saturated_below": int(_T.get("saturated_below", 10)),
    # new Bengaluru / Hyderabad startups per tick (india.py), after the ★ list and fill work
    "india": int(_T.get("india_discovery_per_tick", 30)),
    # 2026-10-14 "scrape it COMPLETELY": the fill pass (fill.py) — description with no model,
    # hiring proof from any role, LinkedIn guest facts, funding — ★ list → India → remote
    "fill": int(_T.get("fill_per_tick", 8)),
    "fill_budget": int(_T.get("fill_budget_seconds", 360)),
    "fill_linkedin": int(_T.get("fill_linkedin_reads_per_tick", 16)),
}
# 2026-10-14 "leads first": a tick's working time and how it is split. Leads (the people
# step, address resolution, re-verification) run first inside their share; the company
# steps share the rest, starting where the last tick stopped (a rotating cursor), so
# discovery, websites, facts and careers each get their turn and never stop.
BUDGET_S = int(_T.get("budget_seconds", 2400))
_SHARE = _T.get("share") or {}
SHARE_LEADS = float(_SHARE.get("leads", 0.7))
SHARE_COMPANIES = float(_SHARE.get("companies", round(1 - SHARE_LEADS, 3)))
SEND_INTERVAL_S = int(_T.get("send_interval_seconds", 300))
SEND_LOCK = ROOT / "data" / "send.lock"
# 2026-10-14: the dedicated referral job — referral work only, India (Bengaluru first),
# every 15 min under its own agent and its OWN lock, in parallel with the tick
_RJ = CONFIG.get("referrals") or {}
REFER_LABEL = "com.zonuly.referrals"
REFER_LOCK = ROOT / "data" / "referrals.lock"
# 2026-10-23: Apify as its own job (com.zonuly.apify) — the People tab, bought inside
# actors.apify.daily_usd, a few companies a run, never overlapping itself
_AP = (CONFIG.get("actors") or {}).get("apify") or {}
APIFY_LABEL = "com.zonuly.apify"
APIFY_LOCK = ROOT / "data" / "apify.lock"
# 2026-10-23: the shortlist job (com.zonuly.shortlist) — one shortlisted company at a time, every
# detail, Bengaluru first (shortlist.py); runs whatever tick.scrapers_frozen says
SHORTLIST_LABEL = "com.zonuly.shortlist"
SHORTLIST_LOCK = ROOT / "data" / "shortlist.lock"
SHORTLIST_INTERVAL_S = int((CONFIG.get("shortlist") or {}).get("interval_seconds", 600))
APIFY_INTERVAL_S = int(_AP.get("interval_seconds", 1800))
APIFY_PER_RUN = int(_AP.get("companies_per_run", 3))
APIFY_LI_BACKFILL = int(_AP.get("linkedin_backfill_per_run", 30))
# 2026-10-23 (the user, after buying Apify Starter): the home-made scrapers are frozen —
# tick install leaves free, paid, referrals and audit unloaded while this is true
SCRAPERS_FROZEN = bool(_T.get("scrapers_frozen", False))
# 2026-10-23: referrals are Apify's job (com.zonuly.apify); the free people-search job stays off unless asked for
FREE_REFERRAL_JOB = bool(_T.get("free_referral_job", True))
REFER_INTERVAL_S = int(_RJ.get("interval_seconds", 900))
#: while the referral job is loaded, the tick's people step keeps only this share of leads
SHARE_PEOPLE_YIELD = float(_SHARE.get("people_when_referral_job", 0.3))
PAID_HOURLY = bool(_T.get("paid_hourly", True))
# the one-off cleanup's merge/quarantine pass, hourly. Off until the first
# `scripts/cleanup_companies.py --apply` has been reviewed and run by hand.
HYGIENE_HOURLY = bool(_T.get("company_hygiene", False))


# ------------------------------------------------------------------ bookkeeping

def _record(kind: str, summary: dict) -> None:
    from jobhunter.db import get_session, set_setting

    raw = json.dumps({"at": datetime.now().isoformat(timespec="seconds"), **summary}, default=str)
    if len(raw) > 4000:
        # 2026-10-16: slicing the JSON made it unparseable, so last("free") was None and the
        # audit's stall check never saw a tick run. Keep the small top-level fields instead.
        small = {k: v for k, v in summary.items() if len(json.dumps(v, default=str)) <= 300}
        raw = json.dumps({"at": datetime.now().isoformat(timespec="seconds"), **small, "truncated": True},
                         default=str)[:4000]
        if len(raw) >= 4000:
            raw = json.dumps({"at": datetime.now().isoformat(timespec="seconds"), "kind": summary.get("kind"),
                              "finished": summary.get("finished"), "truncated": True}, default=str)
    with get_session() as s:
        set_setting(s, f"tick:last:{kind}", raw)


def last(kind: str) -> dict | None:
    from jobhunter.db import get_session, get_setting

    with get_session() as s:
        raw = get_setting(s, f"tick:last:{kind}", "")
    try:
        return json.loads(raw) if raw else None
    except json.JSONDecodeError:
        return None


class _Lock:
    def __init__(self, path: Path = LOCK, what: str = "tick"):
        self.path = path
        self.what = what

    def __enter__(self):
        self.path.parent.mkdir(exist_ok=True)
        # "a" not "w": a start that finds the lock held must not blank the holder's pid line
        self.fh = open(self.path, "a")
        try:
            fcntl.flock(self.fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.fh.close()
            raise RuntimeError(f"another {self.what} is running")
        self.fh.truncate(0)
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
    # a thread silent for followup_after_days gets ONE follow-up drafted into the review
    # queue (model, or the fixed template when the free models are out) — never approved
    try:
        from jobhunter.outreach import followup

        f = followup.queue()
        if f.get("due"):
            out["followups"] = {k: v for k, v in f.items() if k != "ids"}
    except Exception as e:  # noqa: BLE001
        log.warning("follow-ups failed: %s", e)
        out["followups_error"] = str(e)[:200]
    return out


def run_send() -> dict:
    """The send agent's run (launchd every SEND_INTERVAL_S): send the approved mail today's
    warm-up slots say is due — one or two, minutes apart — and nothing else. Its own lock,
    so it never waits behind a 30-minute tick. Approved mail only; dry-run unless the user
    switched send_mode to live."""
    from jobhunter.outreach import sender

    from jobhunter import notify_bot

    paused = notify_bot.is_paused()   # a `pause` reply to a ZoNuLy notification mail
    if paused:
        return {"kind": "send", "mode": sender.SEND_MODE, "sent": 0, "skipped": f"sending {paused}"}
    out = sender.send_due()
    out.pop("results", None) if not out.get("sent") else None
    notice = plan_notice()
    return {"kind": "send", "mode": sender.SEND_MODE, **out, **({"plan_notice": notice} if notice else {})}


PLAN_NOTICE_HOUR = int((CONFIG.get("outreach") or {}).get("plan_notice_hour", 20))
PLAN_NOTICE_FILE = ROOT / "data" / "plan-notice.txt"


def plan_notice() -> dict | None:
    """Once an evening (outreach.plan_notice_hour): a Mac notification saying what goes out on
    the next send day and how many more approvals would fill it, so the next day is settled
    the night before (2026-10-25, Tejmul). Nothing is approved here; the review gate stays."""
    from jobhunter import notify
    from jobhunter.outreach import sender

    now = datetime.now()
    today = now.date().isoformat()
    if now.hour < PLAN_NOTICE_HOUR or (PLAN_NOTICE_FILE.exists() and PLAN_NOTICE_FILE.read_text().strip() == today):
        return None
    p = sender.plan()
    PLAN_NOTICE_FILE.write_text(today)
    planned, open_ = len(p["planned"]), p["open_slots"]
    detail = (f"{planned} of {p['cap']} planned for {p['weekday']}"
              + (f" — approve {min(open_, p['drafts_sendable'])} more to fill it ({p['drafts_sendable']} ready in the queue)"
                 if open_ else " — the day is full"))
    notify.send("ZoNuLy: tomorrow's mail", detail)
    return {"day": p["day"], "planned": planned, "open_slots": open_, "drafts_sendable": p["drafts_sendable"]}


def hygiene_step() -> dict:
    """The dedup gate, hourly: name keys for new rows always; the full merge/quarantine
    pass only once `tick.company_hygiene` is switched on."""
    from jobhunter import hygiene

    out: dict = {"name_keys": hygiene.backfill_keys()}
    if HYGIENE_HOURLY:
        res = hygiene.run(dry_run=False)
        out["applied"] = {k: v for k, v in (res.get("applied") or {}).items() if k != "regrade"}
    return out


def _complete() -> dict:
    from jobhunter import guardrails
    from jobhunter.db import get_session

    with get_session() as session:
        out = guardrails.refresh_complete(session)
    return {k: v for k, v in out.items() if k != "became_ids"}


def _mark_empty() -> dict:
    from jobhunter import hygiene

    return hygiene.mark_empty()


def _guard() -> dict:
    from jobhunter import guardrails

    return guardrails.sweep()


def _work(v) -> int:
    """How many companies a step actually took on, from its summary."""
    if isinstance(v, int):
        return v
    if not isinstance(v, dict) or v.get("error"):
        return 0
    for k in ("tried", "companies", "checked", "searched", "probed"):
        if isinstance(v.get(k), int):
            return v[k]
    return 0


def _saturated(s: dict) -> bool:
    """True when this tick's fill steps found (almost) nothing left to do: every gap is in
    backoff or filled, so the hour is better spent finding new companies."""
    steps = ("websites", "facts_keyless", "careers", "people", "verify", "fill")
    return sum(_work(s.get(k)) for k in steps) < L["saturated_below"]


CURSOR_KEY = "tick:company_cursor"


def _company_steps() -> list[tuple[str, object]]:
    """The company work, as (name, callable) — run in rotation inside the companies share."""
    from jobhunter import enrich, harvest, hiring_verify

    steps: list[tuple[str, object]] = [
        ("funding_rss", harvest.admit_funding_rss),
        ("websites", lambda: enrich.find_websites(limit=L["websites"], keyless=True)),
    ]
    if L["facts_keyless"]:
        steps.append(("facts_keyless", lambda: _count(enrich.facts_pending(limit=L["facts_keyless"], keyless=True))))
    steps.append(("boards", lambda: {"probe": harvest.probe_ats(), "roles": harvest.scrape_roles()}))
    if L["careers"]:
        def _careers():
            from jobhunter import careers

            return {k: v for k, v in careers.run(limit=L["careers"]).items() if k != "lines"}
        steps.append(("careers", _careers))
    steps.append(("revalidate", lambda: _ref().revalidate_companies(
        limit=L["revalidate"], budget_s=max(120, int(BUDGET_S * SHARE_COMPANIES) // 3))))
    steps.append(("verify", lambda: {"checked": len(hiring_verify.verify_pending(
        limit=L["verify"], tiers=("tier1", "tier2", "prospect", "unknown"),
        budget_s=max(120, int(BUDGET_S * SHARE_COMPANIES) // 3)))}))
    from jobhunter.db import FOCUS_CITIES

    if not FOCUS_CITIES:   # 2026-10-31: the YC directory is mostly US companies — not while BLR/HYD is the focus
        steps.append(("yc", lambda: {"yc": _as(harvest.admit_yc()), "probe": harvest.probe_ats(),
                                     "roles": harvest.scrape_roles()}))
    if L["india"]:
        def _india():
            from jobhunter import india

            return {k: v for k, v in india.discover(L["india"]).items() if k != "new_ids"}
        steps.append(("india_discovery", _india))

    def _india_jobs():
        from jobhunter import india_jobs

        return {k: v for k, v in india_jobs.run().items() if k not in ("new_companies", "top_new_by_stated_pay")}
    steps.append(("india_jobs", _india_jobs))
    return steps


def _ref():
    from jobhunter import referrals

    return referrals


def _as(v):
    return v if isinstance(v, (dict, int, str)) else (v.as_dict() if hasattr(v, "as_dict") else v)


def _cursor(n: int) -> int:
    from jobhunter.db import get_session, get_setting

    try:
        with get_session() as s:
            return int(get_setting(s, CURSOR_KEY, "0") or 0) % max(1, n)
    except (ValueError, Exception):  # noqa: BLE001
        return 0


def _set_cursor(i: int) -> None:
    from jobhunter.db import get_session, set_setting

    with get_session() as s:
        set_setting(s, CURSOR_KEY, str(i))


RECOMPUTE_KEY = "tick:recompute_at"


def _recompute_due() -> bool:
    from jobhunter.db import get_session, get_setting

    try:
        with get_session() as s:
            at = get_setting(s, RECOMPUTE_KEY, "")
        return not at or datetime.now() - datetime.fromisoformat(at) >= timedelta(minutes=RECOMPUTE_MINUTES)
    except Exception:  # noqa: BLE001 — when in doubt, recompute
        return True


def _recomputed() -> None:
    from jobhunter.db import get_session, set_setting

    with get_session() as s:
        set_setting(s, RECOMPUTE_KEY, datetime.now().isoformat(timespec="seconds"))


def _addresses_step(touched: list[int], limit: int = 400) -> dict:
    """Address resolution right after the people step: the pattern learned from every
    company's own confirmed addresses, applied to the ready referrers without one — the
    companies the people step just worked on, then every company with a ready referrer
    and no address (cheap: the database and an MX lookup, no search)."""
    from jobhunter import guardrails
    from jobhunter.db import get_session

    with get_session() as session:
        counts = guardrails.ready_counts(session)
    rest = [cid for cid, (r, e) in counts.items() if r > 0 and e == 0 and cid not in touched]
    ids = list(dict.fromkeys(touched + rest))[:limit]
    out = _ref().pattern_step(dry_run=False, company_ids=ids, sample=0)
    return {k: v for k, v in out.items() if k not in ("samples", "skipped")}


def run_free(budget_s: int | None = None) -> dict:
    """Keyless work, LEADS FIRST (2026-10-14): replies and follow-ups, the guard, then the
    people step + address resolution + re-verification inside `share.leads` of the budget,
    alongside (2026-10-29, a second thread) the company steps in rotation inside
    `share.companies`, then the cheap tail (pay, grade, the complete bar) and, at most every
    RECOMPUTE_MINUTES, the whole-table recomputes."""
    from jobhunter import harvest, targeting

    budget_s = budget_s or BUDGET_S
    leads_s = int(budget_s * SHARE_LEADS)
    companies_s = max(300, int(budget_s * SHARE_COMPANIES))
    s: dict = {"kind": "free", "started": datetime.now().isoformat(timespec="seconds"),
               "budget": {"total": budget_s, "leads": leads_s, "companies": companies_s}}
    t0 = time.time()
    # replies first: cheap, and a "yes" should never wait behind a scrape
    _step(s, "poll_replies", poll_replies)
    # the daily refresh of every board: the one thing that must happen today
    _step(s, "full_scrape", full_scrape_if_due)
    _step(s, "hygiene", hygiene_step)
    # the guard: stale hiring verdicts downgraded, completeness recomputed
    _step(s, "guard", _guard)
    _step(s, "scrape_first", _mark_empty)
    # the whole-table recomputes (priority, selection, the decision brain, the pay floor) take
    # ~2.5 min and change little between ticks: at most every RECOMPUTE_MINUTES (2026-10-29)
    recompute = _recompute_due()
    s["recompute"] = recompute
    if recompute:
        _step(s, "priority", targeting.refresh_priority)

    # ---- LEADS: the best bets, then the ★ list, then companies complete except for a referrer
    # with an address, then the rest by priority (referrals.people_queue) — GitHub inside its
    # hourly budget, team pages via Jina, public LinkedIn, podcasts / talks / bylines, the
    # confirmed pattern. They run in their own thread while the fill and the company steps run
    # here: different sites, so neither waits on the other (2026-10-29: one after the other, a
    # tick took ~40 min)
    def _leads() -> None:
        referrals = _ref()
        t_leads = time.time()
        reserve = min(240, leads_s // 6)          # address resolution + re-verification after people
        people_s, people_n = max(60, leads_s - reserve), L["people"]
        # the referral job (com.zonuly.referrals) owns India referrals while it is loaded: the
        # tick's people step yields — a smaller share, and never a company in the job's claimed
        # window (referrals.claim), so the two never search the same company at once
        try:
            yielding = referrals.job_active()
        except Exception:  # noqa: BLE001
            yielding = False
        if yielding:
            people_s = max(60, int(people_s * SHARE_PEOPLE_YIELD))
            people_n = max(5, int(people_n * SHARE_PEOPLE_YIELD))
            s["people_yield"] = {"to": REFER_LABEL, "budget_s": people_s, "limit": people_n}
        _step(s, "people", referrals.people_step, limit=people_n, budget_s=people_s, owner="tick")
        touched = []
        try:
            touched = [int(x) for x in ((s.get("people") or {}).pop("touched", None) or [])]
        except (TypeError, ValueError, AttributeError):
            touched = []
        _step(s, "addresses", _addresses_step, touched)
        # 2026-10-31: every address a scraper found is kept unproved until check-if-email-exists
        # says safe; this proves the newest few each tick (Google-hosted skipped without a proxy)
        from jobhunter.contacts import reacher

        _step(s, "prove_addresses", reacher.verify_pending, limit=L.get("prove", 30))
        # 2026-10-19: the month's Hunter lookups, two a tick, on the companies one lookup can finish
        _step(s, "hunter", referrals.hunter_step, limit=L["hunter"])
        # …and the People tab from Apify, inside the free-credit cap, for companies short of referrers
        _step(s, "apify", referrals.apify_step, limit=L["apify"])
        if time.time() - t_leads < leads_s:
            _step(s, "reverify", referrals.reverify_referrers, limit=L["reverify"])
        if L["affiliation"] and time.time() - t_leads < leads_s:
            from jobhunter.contacts import affiliation

            _step(s, "affiliation", lambda: {k: v for k, v in affiliation.run(
                L["affiliation"], reserve=L["affiliation_reserve"]).items() if k != "labels"})
        s["leads_seconds"] = round(time.time() - t_leads)

    leads = threading.Thread(target=_leads, name="tick-leads", daemon=True)
    leads.start()

    # ---- FILL (2026-10-14): every tick, before the rotation — the ★ list, then India, then
    # the remote targets, each company scraped completely (fill.py; referrers are the people
    # step's, above, which reads the same order)
    if L["fill"]:
        from jobhunter import fill

        _step(s, "fill", fill.run, limit=L["fill"], budget_s=L["fill_budget"],
              linkedin_reads=L["fill_linkedin"], people=False)

    # ---- COMPANIES: a fixed share, steps in rotation so each gets its turn
    steps = _company_steps()
    t_co = time.time()
    first = _cursor(len(steps))
    ran = 0
    for k in range(len(steps)):
        name, fn = steps[(first + k) % len(steps)]
        if k and time.time() - t_co > companies_s:
            break
        _step(s, name, fn)
        ran += 1
    _set_cursor((first + ran) % len(steps))
    s["companies_seconds"] = round(time.time() - t_co)
    s["company_steps_ran"] = ran
    # the existing companies are as complete as the channels allow: find new ones
    if _saturated(s) and time.time() - t_co < companies_s + 120:
        _step(s, "discover", lambda: harvest.admit_exa_keyless(L["discover_queries"]).as_dict())
        _step(s, "probe_new", harvest.probe_ats)
    leads.join()

    # ---- the cheap tail
    _step(s, "pay", targeting.extract_job_pay, only_missing=True)
    _step(s, "grade", targeting.grade_companies, regrade=False)
    from jobhunter import india as _india

    if recompute:
        _step(s, "pay_floor", lambda: {k: v for k, v in _india.apply_pay_floor().items()
                                       if k not in ("sample_hidden",)})
        _step(s, "priority_after", targeting.refresh_priority)

        # SELECTION CONFIDENCE (confidence.py, 2026-10-14): re-score roles whose inputs changed, then a few needs briefs within the model budget
        _step(s, "selection", lambda: __import__("jobhunter.confidence", fromlist=["tick"]).tick())
        # THE DECISION BRAIN (decide.py, 2026-10-23): P(offer) per Bengaluru / Hyderabad company from those role scores, the shortlists re-ranked
        _step(s, "decide", lambda: __import__("jobhunter.decide", fromlist=["tick"]).tick())
        _recomputed()

    _step(s, "complete", _complete)
    if PAID_HOURLY and time.time() - t0 < budget_s + 600:
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


def run_referrals(**kw) -> dict:
    """The referral job's entry point (launchd com.zonuly.referrals and `referrals run`):
    its own lock, data/referrals.lock — parallel to the tick, never overlapping itself."""
    LOGS.mkdir(exist_ok=True)
    _net_timeout()
    try:
        with _Lock(REFER_LOCK, "referral cycle"):
            summary = {"kind": "referrals", "started": datetime.now().isoformat(timespec="seconds"),
                       **_ref().run_cycle(**kw)}
    except RuntimeError as e:
        return {"kind": "referrals", "skipped": str(e)}
    summary["finished"] = datetime.now().isoformat(timespec="seconds")
    summary.pop("reached_ids", None)
    _record("referrals", summary)
    return summary


def run_shortlist() -> dict:
    """The shortlist job (launchd com.zonuly.shortlist): the next shortlisted company, completely."""
    LOGS.mkdir(exist_ok=True)
    _net_timeout()
    try:
        with _Lock(SHORTLIST_LOCK, "shortlist run"):
            from jobhunter import shortlist

            summary = {"kind": "shortlist", "started": datetime.now().isoformat(timespec="seconds"), **shortlist.run(1)}
    except RuntimeError as e:
        return {"kind": "shortlist", "skipped": str(e)}
    summary["finished"] = datetime.now().isoformat(timespec="seconds")
    _record("shortlist", summary)
    return summary


def run_apify() -> dict:
    """The Apify job (launchd com.zonuly.apify): People tabs for the next companies in
    referrals.apify_queue, inside the daily cap, then the complete bar refreshed."""
    LOGS.mkdir(exist_ok=True)
    _net_timeout()
    try:
        with _Lock(APIFY_LOCK, "apify run"):
            summary = {"kind": "apify", "started": datetime.now().isoformat(timespec="seconds")}
            # free and first (2026-10-23): read homepages for the LinkedIn page the People tab
            # needs — a company without linkedin_url can never have its referrers bought
            _step(summary, "linkedin_urls", lambda: __import__("jobhunter.fill", fromlist=["backfill_linkedin"])
                  .backfill_linkedin(limit=APIFY_LI_BACKFILL))
            summary.update(_ref().apify_step(limit=APIFY_PER_RUN))
            _step(summary, "complete", _complete)
    except RuntimeError as e:
        return {"kind": "apify", "skipped": str(e)}
    summary["finished"] = datetime.now().isoformat(timespec="seconds")
    _record("apify", summary)
    return summary


class _UseSendLock(Exception):
    """The send agent has its own lock: it must never wait behind a long free tick."""


def _net_timeout() -> None:
    """A safety net for any socket opened without a timeout (2026-10-15: a job hung for
    hours). httpx already defaults to 5 s; this covers raw sockets and libraries that block."""
    import socket

    if socket.getdefaulttimeout() is None:
        socket.setdefaulttimeout(float(_T.get("socket_timeout_seconds", 120)))


NOTIFY_LOCK = ROOT / "data" / "notify.lock"


def _notify_cycle() -> dict:
    """The notification bot (notify_bot.cycle) after each send run, under its own lock, so
    a stuck free tick or referral job cannot silence it. Never raises."""
    try:
        from jobhunter import notify_bot

        if not notify_bot.ENABLED:
            if not notify_bot.REPLIES:
                return {"skipped": "notify.email is off"}
            with _Lock(NOTIFY_LOCK, "notification run"):
                return {"replies": notify_bot.email_positive_replies()}   # replies only (notify.replies)
        with _Lock(NOTIFY_LOCK, "notification run"):
            return notify_bot.cycle()
    except Exception as e:  # noqa: BLE001
        log.warning("notification cycle failed: %s", e)
        return {"error": f"{type(e).__name__}: {str(e)[:200]}"}


def run(kind: str = "free") -> dict:
    """Entry point for launchd and the CLI. Takes the lock, runs one slice, records it."""
    LOGS.mkdir(exist_ok=True)
    _net_timeout()
    try:
        # 2026-10-16: the send agent used to take tick.lock first, so it skipped every run
        # while a free tick held it (288 of 323 runs) and overwrote tick.lock's pid line
        if kind == "send":
            raise _UseSendLock()
        if kind == "apify":
            return run_apify()
        if kind == "shortlist":
            return run_shortlist()
        if kind == "paid":
            with _Lock(PAID_LOCK, "paid tick"):
                summary = run_paid()
        else:
            with _Lock():
                summary = run_free() if kind == "free" else {**run_free(), "paid": run_paid()}
    except _UseSendLock:
        try:
            with _Lock(SEND_LOCK):
                summary = run_send()
        except RuntimeError as e:
            return {"kind": kind, "skipped": str(e), "notify": _notify_cycle()}
        summary["notify"] = _notify_cycle()
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
        # ~/.local/bin is where `uv tool install` puts the research CLIs (yt-dlp, agent-reach)
        "EnvironmentVariables": {"PATH": f"{Path.home()}/.local/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin",
                                 "HOME": str(Path.home())},
        "StandardOutPath": str(LOGS / f"tick-{kind}.log"),
        "StandardErrorPath": str(LOGS / f"tick-{kind}.log"),
        "RunAtLoad": True,          # a tick on login / when the agent is (re)loaded — i.e. when the laptop is open
        "ProcessType": "Background",
        "Nice": 5,
    }
    if kind == "free":
        base["StartInterval"] = FREE_INTERVAL_S      # every 10 min (config); a missed interval fires on wake
    elif kind == "send":
        base["StartInterval"] = SEND_INTERVAL_S      # every 5 min: sends only approved mail whose slot is due
        base["RunAtLoad"] = False
    else:
        base["StartCalendarInterval"] = {"Hour": PAID_HOUR, "Minute": PAID_MINUTE}   # daily, after the caps reset
    return base


def _refer_plist() -> dict:
    """com.zonuly.referrals: one referral cycle every REFER_INTERVAL_S (15 min) and at load,
    the same PATH / env as the tick's agents; data/referrals.lock keeps it from overlapping
    itself while it runs in parallel with the tick."""
    base = _plist("free")
    base.update({
        "Label": REFER_LABEL,
        "ProgramArguments": [PYTHON, str(ROOT / "scripts" / "run.py"), "referrals", "run"],
        "StandardOutPath": str(LOGS / "referrals.log"),
        "StandardErrorPath": str(LOGS / "referrals.log"),
        "StartInterval": REFER_INTERVAL_S,
        "RunAtLoad": True,
    })
    return base


def _apify_plist() -> dict:
    base = _plist("free")
    base.update({
        "Label": APIFY_LABEL,
        "ProgramArguments": [PYTHON, str(ROOT / "scripts" / "run.py"), "tick", "run", "--kind", "apify"],
        "StandardOutPath": str(LOGS / "apify.log"),
        "StandardErrorPath": str(LOGS / "apify.log"),
        "StartInterval": APIFY_INTERVAL_S,
        "RunAtLoad": True,
    })
    return base


def _shortlist_plist() -> dict:
    base = _plist("free")
    base.update({
        "Label": SHORTLIST_LABEL,
        "ProgramArguments": [PYTHON, str(ROOT / "scripts" / "run.py"), "tick", "run", "--kind", "shortlist"],
        "StandardOutPath": str(LOGS / "shortlist.log"),
        "StandardErrorPath": str(LOGS / "shortlist.log"),
        "StartInterval": SHORTLIST_INTERVAL_S,
        "RunAtLoad": True,
    })
    return base


def _unload(path: Path) -> dict:
    r = subprocess.run(["launchctl", "unload", str(path)], capture_output=True, text=True)
    return {"plist": str(path), "unloaded": r.returncode == 0}


def _load(path: Path, plist: dict) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["launchctl", "unload", str(path)], capture_output=True)
    path.write_bytes(plistlib.dumps(plist))
    r = subprocess.run(["launchctl", "load", str(path)], capture_output=True, text=True)
    return {"plist": str(path), "loaded": r.returncode == 0, "stderr": r.stderr.strip()[:200]}


def install() -> dict:
    """Write the LaunchAgents and load them — the tick (free, paid, send), the referral
    job, and the hourly audit beside them. Idempotent."""
    LOGS.mkdir(exist_ok=True)
    out = {}
    out["send"] = _load(_agents_dir() / f"{LABEL}.send.plist", _plist("send"))
    out["apify"] = _load(_agents_dir() / f"{APIFY_LABEL}.plist", _apify_plist())
    out["shortlist"] = _load(_agents_dir() / f"{SHORTLIST_LABEL}.plist", _shortlist_plist())
    if SCRAPERS_FROZEN:
        for kind in ("free", "paid"):
            out[kind] = _unload(_agents_dir() / f"{LABEL}.{kind}.plist")
        out["referrals"] = _unload(_agents_dir() / f"{REFER_LABEL}.plist")
        out["audit"] = _unload(_agents_dir() / "com.zonuly.audit.plist")
        out["frozen"] = "tick.scrapers_frozen is true: free, paid, referrals and audit stay unloaded"
        return out
    for kind in ("free", "paid"):
        out[kind] = _load(_agents_dir() / f"{LABEL}.{kind}.plist", _plist(kind))
    out["referrals"] = (_load(_agents_dir() / f"{REFER_LABEL}.plist", _refer_plist()) if FREE_REFERRAL_JOB
                        else _unload(_agents_dir() / f"{REFER_LABEL}.plist"))
    from jobhunter import audit

    out["audit"] = audit.install()
    return out


def uninstall() -> dict:
    out = {}
    for kind in ("free", "paid", "send"):
        path = _agents_dir() / f"{LABEL}.{kind}.plist"
        r = subprocess.run(["launchctl", "unload", str(path)], capture_output=True, text=True)
        if path.exists():
            path.unlink()
        out[kind] = {"unloaded": r.returncode == 0}
    path = _agents_dir() / f"{REFER_LABEL}.plist"
    r = subprocess.run(["launchctl", "unload", str(path)], capture_output=True, text=True)
    if path.exists():
        path.unlink()
    out["referrals"] = {"unloaded": r.returncode == 0}
    return out


def status() -> dict:
    out = {"lock": LOCK.read_text().strip() if LOCK.exists() else None, "python": PYTHON}
    r = subprocess.run(["launchctl", "list"], capture_output=True, text=True)
    loaded = {ln.split("\t")[-1]: ln.split("\t")[0] for ln in r.stdout.splitlines() if "com.zonuly" in ln}
    out["pacing"] = {"paid_hourly": PAID_HOURLY, "used_today": _used_today(),
                     "per_day": {k: L[k] for k in ("facts", "describe", "story", "levels")}}
    out["share"] = {"budget_seconds": BUDGET_S, "leads": SHARE_LEADS, "companies": SHARE_COMPANIES}
    for kind in ("free", "paid", "send"):
        label = f"{LABEL}.{kind}"
        out[kind] = {
            "loaded": label in loaded, "pid": loaded.get(label) if loaded.get(label, "-") != "-" else None,
            "schedule": f"every {FREE_INTERVAL_S // 60} min + on login/wake" if kind == "free" else
            f"every {SEND_INTERVAL_S // 60} min (approved mail whose slot is due)" if kind == "send" else
            f"daily {PAID_HOUR:02d}:{PAID_MINUTE:02d} + on login",
            "last": last(kind), "log": str(LOGS / f"tick-{kind}.log"),
        }
    out["referrals"] = {
        "loaded": REFER_LABEL in loaded,
        "pid": loaded.get(REFER_LABEL) if loaded.get(REFER_LABEL, "-") != "-" else None,
        "schedule": f"every {REFER_INTERVAL_S // 60} min + on login (referral work only, Bengaluru → Hyderabad → India → remote)",
        "lock": REFER_LOCK.read_text().strip() if REFER_LOCK.exists() else None,
        "last": last("referrals"), "log": str(LOGS / "referrals.log"),
    }
    return out


__all__ = ["run", "run_free", "run_paid", "run_send", "run_referrals", "install", "uninstall", "status", "last"]
