"""Launch preflight — is everything in place for live sending, and if not, the exact fix.

`python scripts/run.py outreach preflight`, GET /api/outreach/preflight, and the Launch
checklist on the Outreach page all read `run()`. Each check is

    {id, label, status: green | amber | red, detail, fix}

green = ready; amber = works, but look (or: something you do on launch morning); red =
mail cannot go out (or would go out wrongly) until it is fixed. Read-only, except that
the Gmail check signs in to SMTP and IMAP once (cached ten minutes by gmail.verify).

The caps are checked AGAINST the rules, not just reported: a daily cap above 25 or a
guessed cap above 5 is red — those numbers are never raised to make a launch easier.
"""

from __future__ import annotations

import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path

from sqlmodel import col, func, select

from jobhunter import CONFIG, ROOT
from jobhunter.db import Company, Email, get_session, get_setting, init_db, utcnow

LAUNCH_DAY = "2026-10-19"
MAX_DAILY = 25
MAX_GUESSED = 5


def _c(id_: str, label: str, status: str, detail: str, fix: str | None = None) -> dict:
    return {"id": id_, "label": label, "status": status, "detail": detail, "fix": fix}


def _launchd() -> set[str]:
    try:
        r = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=10)
        return {ln.split("\t")[-1] for ln in r.stdout.splitlines() if "zonuly" in ln}
    except Exception:  # noqa: BLE001 — not macOS / no launchctl: reported as not installed
        return set()


def _gmail(o: dict, *, sign_in: bool) -> list[dict]:
    from jobhunter.outreach import gmail

    out = []
    addr = gmail.ADDRESS
    out.append(_c("sender", "Sending identity", "green" if addr and gmail.DISPLAY_NAME else "red",
                  f"{gmail.from_header() or '(none)'} — replies come back to the same address",
                  None if addr else "Set outreach.user_name and outreach.user_email in config.yaml."))
    if gmail.AUTH != "app_password":
        out.append(_c("gmail_password", "Gmail sign-in", "amber", f"gmail.auth is {gmail.AUTH}",
                      "Use gmail.auth: app_password (SMTP + IMAP, no Google Cloud project)."))
        return out
    present = bool(gmail.app_password())
    out.append(_c("gmail_password", "Gmail app password saved", "green" if present else "red",
                  "GMAIL_APP_PASSWORD is in .env" if present else f"no app password for {addr} yet",
                  None if present else
                  f"Turn on 2-Step Verification for {addr}, create an app password at "
                  "myaccount.google.com/apppasswords (name it ZoNuLy), paste it in Settings → Email."))
    if present and sign_in:
        v = gmail.verify(force=False)
        out.append(_c("gmail_login", "Google accepts the sign-in (SMTP send + IMAP read)",
                      "green" if v["ok"] else "red", "SMTP and IMAP both signed in" if v["ok"] else (v.get("error") or "refused"),
                      None if v["ok"] else "Create a fresh app password and paste it again; check 2-Step Verification is on. "
                      "Then run `python scripts/run.py gmail-verify`."))
    elif present:
        out.append(_c("gmail_login", "Google accepts the sign-in", "amber", "not checked this time",
                      "Run `python scripts/run.py gmail-verify`."))
    else:
        out.append(_c("gmail_login", "Google accepts the sign-in (SMTP send + IMAP read)", "red",
                      "cannot sign in without the app password", "Save the app password first (above)."))
    return out


def run(*, sign_in: bool = True, today: datetime | None = None) -> dict:
    from jobhunter.outreach import ledger, sender, suppress, warmup

    init_db()
    now = today or datetime.now()
    o = CONFIG.get("outreach") or {}
    checks: list[dict] = []
    checks += _gmail(o, sign_in=sign_in)

    # ---- the mode
    mode = sender.SEND_MODE
    launch = datetime.strptime(str((o.get("warmup") or {}).get("start") or LAUNCH_DAY), "%Y-%m-%d")
    before_launch = now.date() < launch.date()
    if mode == "gmail":
        checks.append(_c("send_mode", "Send mode", "green", "live — approved mail leaves through Gmail"))
    else:
        checks.append(_c("send_mode", "Send mode", "amber" if before_launch else "red",
                         f"{mode}: approved mail is written to outbox/ and never leaves the machine",
                         f"On {launch:%a %d %b} morning: Settings → Email → switch to live (or set "
                         "outreach.send_mode: gmail in config.yaml)."))

    # ---- caps (never above the rules)
    cap, gcap = int(o.get("daily_send_cap", 25)), int(o.get("guessed_daily_cap", 5))
    ok = cap <= MAX_DAILY and gcap <= MAX_GUESSED and gcap <= cap
    checks.append(_c("caps", "Daily caps", "green" if ok else "red",
                     f"{cap}/day, of them at most {gcap} to guessed addresses",
                     None if ok else f"Put outreach.daily_send_cap ≤ {MAX_DAILY} and guessed_daily_cap ≤ {MAX_GUESSED} back."))
    wu = o.get("warmup") or {}
    sched = warmup.schedule(14, from_day=launch.date())
    ramp = ", ".join(f"{d['weekday']} {d['quota']}" for d in sched if d["send_day"])
    checks.append(_c("warmup", "Warm-up ramp", "green" if wu else "amber",
                     f"from {launch:%a %d %b}: {ramp}" if wu else "no warm-up: the full cap from day one",
                     None if wu else "Add outreach.warmup {start, first, step, every} to config.yaml."))
    brake = None
    with get_session() as session:
        brake = suppress.brake(session)
    checks.append(_c("bounce_brake", "Bounce brake", "amber" if brake else "green",
                     brake or f"armed: {suppress.BRAKE_MAX} bounces in {suppress.BRAKE_DAYS} days pause guessed addresses"))

    # ---- window / timezone
    lo, hi = int(sender.SEND_WINDOW[0]), int(sender.SEND_WINDOW[1])
    tz = time.strftime("%Z")
    offset = datetime.now().astimezone().utcoffset() or timedelta(0)
    def _at(h: int, delta_h: float) -> str:
        t = datetime(2026, 10, 19, h) - offset + timedelta(hours=delta_h)
        return t.strftime("%H:%M")
    checks.append(_c("window", "Send window and timezone", "green",
                     f"{lo:02d}:00–{hi:02d}:00 {tz} on {', '.join(d.capitalize() for d in warmup.SEND_DAYS)} "
                     f"(= {_at(lo, 1)}–{_at(hi, 1)} London, {_at(lo, -4)}–{_at(hi, -4)} New York); slots jittered "
                     f"across the window, ≤{warmup.MAX_PER_RUN} per run"))
    checks.append(_c("ledger", "Ledger resets at local midnight", "green",
                     f"the day key is the local date ({ledger.status()['day']}); today's ceiling "
                     f"{ledger.status()['cap']} (warm-up), {ledger.status()['used']} used"))

    # ---- what the mail says
    from jobhunter.outreach import drafter

    sig = drafter.signature()
    links = [str(x) for x in (o.get("user_links") or [])]
    resume = [x for x in links if any(k in x.lower() for k in ("resume", "cv", "drive.google", ".pdf", "docs.google"))]
    checks.append(_c("signature", "Signature", "green" if o.get("user_name") and links else "red",
                     sig.replace("\n", " · ")[:220], None if links else "Set outreach.user_links in config.yaml."))
    checks.append(_c("resume_link", "Resume link in the signature", "green" if resume else "amber",
                     f"{resume[0]}" if resume else "no resume link — the prompt promises one in the signature, and mail never carries attachments",
                     None if resume else "Upload the resume PDF (Google Drive, anyone-with-the-link can view) and add its URL to outreach.user_links in config.yaml."))
    line = suppress.OPT_OUT_LINE
    checks.append(_c("opt_out", "Opt-out line on every cold mail", "green" if line else "red",
                     f"“{line}” — added on approval; replies like “stop” / “unsubscribe” suppress the person and company forever" if line
                     else "no opt-out line", None if line else "Set outreach.opt_out_line in config.yaml."))
    dnc = o.get("do_not_contact") or {}
    n_dnc = sum(len(dnc.get(k) or []) for k in ("emails", "domains", "companies"))
    with get_session() as session:
        sup_c = session.exec(select(func.count()).select_from(Company)
                             .where(col(Company.do_not_contact) == True)).one()  # noqa: E712
    checks.append(_c("dnc", "Do-not-contact list", "green",
                     f"{n_dnc} entries in outreach.do_not_contact; {sup_c} companies suppressed by opt-outs"))

    # ---- the profile and resume
    prof = Path(CONFIG.get("profile_path") and ROOT / CONFIG["profile_path"] or ROOT / "profile/profile.json")
    res = ROOT / (CONFIG.get("resume_path") or "profile/resume.pdf")
    if prof.exists():
        age = (time.time() - prof.stat().st_mtime) / 86400
        stale = res.exists() and res.stat().st_mtime > prof.stat().st_mtime + 60
        st = "red" if stale else "amber" if age > 45 else "green"
        checks.append(_c("profile", "Profile up to date", st,
                         f"profile.json is {age:.0f} days old" + ("; resume.pdf is newer than it" if stale else ""),
                         None if st == "green" else "Re-read the resume: `python scripts/run.py profile`."))
    else:
        checks.append(_c("profile", "Profile up to date", "red", "no profile.json", "Run `python scripts/run.py profile`."))

    # ---- the inbox, follow-ups, calendar
    with get_session() as session:
        last_poll = get_setting(session, "outreach:last_poll_at") or None
        last_err = get_setting(session, "outreach:last_poll_error") or None
    if last_err:
        checks.append(_c("polling", "Reply + bounce polling (IMAP, every tick)", "red",
                         f"last check {last_poll or 'never'}: {last_err[:160]}",
                         "Starts working as soon as the app password is saved; nothing else to do."))
    else:
        checks.append(_c("polling", "Reply + bounce polling (IMAP, every tick)", "green" if last_poll else "amber",
                         f"last check {last_poll or 'never'}; a bounce takes the address off the person, an opt-out suppresses the company"))
    try:
        from jobhunter import openrouter

        ceiling = openrouter._ceiling_today()
    except Exception:  # noqa: BLE001
        ceiling = False
    checks.append(_c("followups", "Follow-ups (one, after 5 silent days, same thread)",
                     "amber" if ceiling else "green",
                     ("the free model ceiling is hit today — follow-ups fall back to the fixed template; "
                      if ceiling else "the model writes them; the fixed template covers a model outage; ")
                     + "every follow-up waits in the review queue",
                     None))
    cal = CONFIG.get("calendar") or {}
    from jobhunter.outreach import gmail

    checks.append(_c("calendar", "A “yes” becomes an event",
                     "green" if gmail.AUTH == "oauth" else "amber",
                     "events are pushed to Google Calendar" if gmail.AUTH == "oauth" else
                     f"a reply with a time is read into Calendar in the app ({cal.get('timezone', 'local')}) with a "
                     "macOS notification; pushing to Google Calendar needs the optional OAuth client",
                     None if gmail.AUTH == "oauth" else "Optional: check /calendar after each positive reply."))

    # ---- the queue for the first day
    from jobhunter.outreach import batch

    wk = batch.week(launch.date() if before_launch else now.date())
    need = max(int(o.get("min_approved_for_day", 5)), wk["first_day_quota"])
    with get_session() as session:
        approved = session.exec(select(func.count()).select_from(Email).where(Email.status == "approved")).one()
        stuck = session.exec(select(func.count()).select_from(Email).where(
            Email.status == "sending", col(Email.created_at) < utcnow() - timedelta(hours=1))).one()
    st = "green" if approved >= need else ("amber" if before_launch else "red")
    checks.append(_c("queue", f"Approved drafts for {wk['first_day'] or 'the next sending day'}", st,
                     f"{approved} approved, {wk['in_review']} waiting in review; the first day sends {wk['first_day_quota']}, "
                     f"the week {wk['week_total']}",
                     None if st == "green" else
                     "Build the week's drafts (`python scripts/run.py outreach batch`), then read and approve them in the Queue."))
    if stuck:
        checks.append(_c("stuck", "Mail stuck mid-send", "red", f"{stuck} email(s) in 'sending' for over an hour",
                         "Look in Gmail → Sent: if it went, mark it sent; if not, set it back to approved."))

    # ---- the machinery
    agents = _launchd()
    for label, what, fix in (
        ("com.zonuly.tick.free", "The 10-minute tick (people, replies, follow-ups)", "python scripts/run.py tick install"),
        ("com.zonuly.tick.send", "The send agent (paced sends, every 5 min)", "python scripts/run.py tick install"),
        ("com.zonuly.audit", "The hourly audit", "python scripts/run.py audit install"),
    ):
        checks.append(_c(label.split(".")[-1] if "tick" in label else "audit", what,
                         "green" if label in agents else "red",
                         "loaded in launchd" if label in agents else "not loaded",
                         None if label in agents else f"Run `{fix}`."))

    order = {"red": 0, "amber": 1, "green": 2}
    worst = min((c["status"] for c in checks), key=lambda s: order[s])
    return {"at": now.isoformat(timespec="seconds"), "launch_day": launch.date().isoformat(),
            "status": worst, "counts": {k: sum(1 for c in checks if c["status"] == k) for k in ("red", "amber", "green")},
            "checks": checks, "week": wk, "user_checklist": USER_CHECKLIST}


USER_CHECKLIST = [
    "Turn on 2-Step Verification for tejmulmovin9@gmail.com, then create an app password at "
    "myaccount.google.com/apppasswords (name it ZoNuLy).",
    "Paste it in Settings → Email and press Verify (or run `python scripts/run.py gmail-verify`) — it must say SMTP and IMAP OK.",
    "Add a resume link (a Drive PDF anyone with the link can view) to outreach.user_links, so the signature carries it.",
    "Run `python scripts/run.py tick install` once, so the send agent is loaded next to the tick.",
    "By Sunday night: open the Queue, read every draft, edit what is off, approve at least 5 for Monday (45 for the week). Nothing is sent that you did not approve.",
    "Monday morning, before 10:00: run `python scripts/run.py outreach preflight` — everything green except send mode — then switch "
    "Settings → Email to live.",
    "Each evening: read replies, approve the follow-ups the app drafted, and approve the next day's asks.",
]


def text(report: dict) -> str:
    mark = {"green": "ok  ", "amber": "look", "red": "FIX "}
    lines = [f"Launch preflight — {report['at']} (launch {report['launch_day']}): {report['status'].upper()} "
             f"({report['counts']['red']} red, {report['counts']['amber']} amber, {report['counts']['green']} green)", ""]
    for c in report["checks"]:
        lines.append(f"[{mark[c['status']]}] {c['label']}: {c['detail']}")
        if c.get("fix"):
            lines.append(f"        fix: {c['fix']}")
    return "\n".join(lines)


__all__ = ["run", "text", "USER_CHECKLIST"]
