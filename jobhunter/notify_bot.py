"""The notification bot — mail to Tejmul when it matters, and commands back by reply.

"You can send me through a mail … suppose you think this is an emergency situation, it
can send me a mail so that I can reply to that specific mail." (2026-10-16)

Three kinds of mail, all from the outreach Gmail account to `notify.email_to` (default: that
same address), subject prefixed "[ZoNuLy]" and tagged [ZN-xxxxxx]:

    POSITIVE REPLY  at once — who replied, where they work, what they wrote, the email they
                    answered, and what to do next.
    URGENT          at once, at most one per issue per `notify.urgent_dedup_hours` — the
                    watchdog's findings (a job stuck, the DB locked, a job not running, the
                    bounce brake, send failures, Gmail refusing the sign-in) and the hourly
                    audit's stall / search-down alerts.
    DAILY DIGEST    once a day after `notify.digest_hour` (local time, IST) — drafts waiting
                    (with ids), what went out yesterday, replies, referrers added, anything stuck.

Reply to any of them with commands, one per line: `approve 12 14`, `reject 15`, `pause`,
`resume`, `status`, `help`. A command is accepted only from `notify.email_to`, only on a
message that answers one of the bot's own mails (its In-Reply-To / References names a
Message-ID the bot made — a random token nobody else has seen), and never from a message the
bot sent itself. Approve and reject go through outreach.sender.approve / reject — the exact
functions the dashboard's buttons call. Pause sets `outreach:paused`, which the send tick
reads before it sends anything. Every command gets a short confirmation reply.

Delivery follows outreach.send_mode: in dryrun the mail is written to outbox/notify/*.eml
instead, unless `notify.send_in_dryrun` (default true — these go to himself). The automatic
cycle runs only when `notify.email` is true; `python scripts/run.py notify test` sends one
regardless, so the switch can be tested before it is turned on.

State (who was told what, which commands were done, the bot's Message-IDs) lives in
data/notify_state.json, not the DB: the DB being locked is one of the things to report.
"""

from __future__ import annotations

import email as email_lib
import email.policy
import fcntl
import imaplib
import json
import logging
import re
import secrets
import smtplib
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid, parseaddr

from sqlmodel import col, func, select

from jobhunter import CONFIG, ROOT
from jobhunter.db import Company, Contact, Email, Reply, get_session, get_setting, set_setting, utcnow

log = logging.getLogger(__name__)

_N = CONFIG.get("notify") or {}
_O = CONFIG.get("outreach") or {}
ENABLED = bool(_N.get("email", False))
EMAIL_TO = str(_N.get("email_to") or _O.get("user_email") or "").strip()
SEND_IN_DRYRUN = bool(_N.get("send_in_dryrun", True))
DIGEST_HOUR = int(_N.get("digest_hour", 8))
URGENT_DEDUP_H = float(_N.get("urgent_dedup_hours", 6))
REPLY_LOOKBACK_H = float(_N.get("reply_lookback_hours", 48))
COMMANDS = bool(_N.get("commands", True))
COMMAND_POLL_MIN = float(_N.get("command_poll_minutes", 5))
REPLY_POLL_FALLBACK_MIN = float(_N.get("reply_poll_fallback_minutes", 45))
PREFIX = "[ZoNuLy]"
OUTBOX = ROOT / "outbox" / "notify"
STATE_FILE = ROOT / "data" / "notify_state.json"
PAUSE_KEY = "outreach:paused"
BOT_HEADER = "X-ZoNuLy-Bot"
_KEEP = 400

HELP = (
    "Reply to this mail with commands, one per line:\n"
    "    approve 12 14     approve drafts #12 and #14 (the dashboard's Approve)\n"
    "    reject 15         reject draft #15\n"
    "    pause             stop the send agent sending anything\n"
    "    resume            let it send again\n"
    "    status            the queue, today's sends, the jobs\n"
)


# ================================================================== state

@contextmanager
def _state():
    """data/notify_state.json, read-modify-written under an fcntl lock."""
    STATE_FILE.parent.mkdir(exist_ok=True)
    with open(STATE_FILE.with_suffix(".lock"), "a") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            try:
                state = json.loads(STATE_FILE.read_text())
            except (OSError, ValueError):
                state = {}
            for k, v in (("threads", {}), ("urgent", {}), ("replies_emailed", []), ("commands_done", [])):
                state.setdefault(k, v)
            yield state
            tmp = STATE_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(state, default=str))
            tmp.replace(STATE_FILE)
        finally:
            fcntl.flock(lk, fcntl.LOCK_UN)


def _trim_list(xs: list, n: int = _KEEP) -> list:
    return xs[-n:]


# ================================================================== delivery

def _mode() -> str:
    from jobhunter.outreach import sender

    return sender.SEND_MODE


def _sender_address() -> str:
    from jobhunter.outreach import gmail

    return gmail.ADDRESS


def compose(kind: str, subject: str, body: str, *, in_reply_to: str | None = None,
            references: str | None = None, raw_subject: bool = False) -> EmailMessage:
    """One bot mail: [ZoNuLy] prefix, a [ZN-token] tag, a Message-ID that carries the token."""
    from jobhunter.outreach import gmail

    token = secrets.token_hex(3)
    msg = EmailMessage()
    msg["From"] = gmail.from_header()
    msg["To"] = EMAIL_TO
    msg["Subject"] = subject if raw_subject else f"{PREFIX} {subject} [ZN-{token}]"
    msg["Date"] = format_datetime(datetime.now().astimezone())
    msg["Message-ID"] = make_msgid(idstring=f"zn{token}", domain=(_sender_address().split("@")[-1] or "zonuly.local"))
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = f"{references} {in_reply_to}".strip() if references else in_reply_to
    msg[BOT_HEADER] = kind
    msg.set_content(body)
    return msg


def deliver(msg: EmailMessage, *, force_dry: bool = False) -> dict:
    """Send over SMTP — or, in dryrun without notify.send_in_dryrun, write outbox/notify/*.eml.
    Records the Message-ID as one of the bot's threads either way."""
    dry = force_dry or (_mode() != "gmail" and not SEND_IN_DRYRUN)
    if not EMAIL_TO:
        return {"sent": False, "error": "notify.email_to and outreach.user_email are both empty"}
    if dry:
        OUTBOX.mkdir(parents=True, exist_ok=True)
        path = OUTBOX / f"{datetime.now():%Y%m%d-%H%M%S}-{msg[BOT_HEADER]}-{secrets.token_hex(2)}.eml"
        msg["X-ZoNuLy-Mode"] = "dryrun"
        path.write_bytes(msg.as_bytes())
        result = {"sent": False, "dryrun": True, "path": str(path)}
    else:
        try:
            result = {"sent": True, **_smtp_send(msg)}
        except Exception as e:  # noqa: BLE001 — a notification failing must never break a job
            log.warning("notification mail failed: %s", e)
            return {"sent": False, "error": f"{type(e).__name__}: {str(e)[:200]}"}
    with _state() as st:
        st["threads"][msg["Message-ID"]] = {"kind": msg[BOT_HEADER], "at": datetime.now().isoformat(timespec="seconds"),
                                            "subject": str(msg["Subject"])[:160]}
        if len(st["threads"]) > _KEEP:
            for k in list(st["threads"])[: len(st["threads"]) - _KEEP]:
                st["threads"].pop(k, None)
    result["message_id"] = msg["Message-ID"]
    return result


def _smtp_send(msg: EmailMessage) -> dict:
    from jobhunter.outreach import gmail

    pw = gmail.app_password()
    if not gmail.ADDRESS or not pw:
        raise gmail.GmailNotConfigured("Gmail app password missing (GMAIL_APP_PASSWORD)")
    with smtplib.SMTP_SSL(gmail.SMTP_HOST, gmail.SMTP_PORT, timeout=30) as conn:
        conn.login(gmail.ADDRESS, pw)
        conn.send_message(msg)
    return {"to": EMAIL_TO}


def _delivered(r: dict, force_dry: bool) -> bool:
    """Counts as told: really sent, or written as .eml because send_mode is dryrun. A forced
    preview (--dry) is never recorded, so it cannot swallow the real mail."""
    return bool(r.get("sent") or (r.get("dryrun") and not force_dry))


def _macos(title: str, message: str, subtitle: str = "") -> None:
    try:
        from jobhunter import notify

        notify.send(title, message, subtitle)
    except Exception as e:  # noqa: BLE001
        log.debug("macOS notification failed: %s", e)


# ================================================================== URGENT

def urgent(key: str, title: str, detail: str, *, now: datetime | None = None, force_dry: bool = False) -> dict:
    """One URGENT mail per issue key per URGENT_DEDUP_H hours (and a macOS notification)."""
    now = now or datetime.now()
    with _state() as st:
        last = st["urgent"].get(key)
        try:
            if last and now - datetime.fromisoformat(last) < timedelta(hours=URGENT_DEDUP_H):
                return {"skipped": "already told", "key": key, "last": last}
        except ValueError:
            pass
    if not force_dry:
        _macos("ZoNuLy: urgent", title[:180], "check your mail")
    body = (f"URGENT — {title}\n\n{detail}\n\n"
            f"(Noticed {now:%Y-%m-%d %H:%M}. You will not hear about this same issue again for "
            f"{URGENT_DEDUP_H:g} hours.)\n\n{_status_line()}\n\n{HELP}")
    r = deliver(compose("urgent", f"URGENT: {title}", body), force_dry=force_dry)
    if _delivered(r, force_dry):
        with _state() as st:
            st["urgent"][key] = now.isoformat(timespec="seconds")
            for k in [k for k, v in st["urgent"].items() if v < (now - timedelta(days=3)).isoformat()]:
                st["urgent"].pop(k, None)
    return {"key": key, **r}


def alert_issues(issues: list[dict], **kw) -> list[dict]:
    return [urgent(i["key"], i["title"], i["detail"], **kw) for i in issues]


def audit_alerts(alerts: list[str], **kw) -> list[dict]:
    """The hourly audit's alerts that are emergencies: a stall, web search down, or several
    research channels failing at once. One channel resting is routine and stays a macOS note."""
    out = []
    channel = [a for a in alerts if a.startswith("CHANNEL")]
    for a in alerts:
        if a.startswith("STALL"):
            out.append(urgent("audit:stall", "scraping stalled: nothing new written in the last hour",
                              f"The hourly audit: {a}.\nThe tick ran but wrote nothing — look at logs/tick-free.log.", **kw))
        elif "web search failed" in a:
            out.append(urgent("audit:search-down", "web search is down",
                              f"The hourly audit: {a}.\nReferral search and discovery are running blind.", **kw))
    if len(channel) >= 2:
        out.append(urgent("audit:channels", f"{len(channel)} research channels failing",
                          "The hourly audit:\n" + "\n".join(f"  {a}" for a in channel)
                          + "\nIf every channel fails at once the network (or DNS) is the usual cause.", **kw))
    return out


# ================================================================== POSITIVE REPLY

def _excerpt(text: str, n: int = 1500) -> str:
    from jobhunter.outreach import tracker

    try:
        text = tracker._strip_quoted(text) or text
    except Exception:  # noqa: BLE001
        pass
    text = (text or "").strip()
    return text[:n] + ("…" if len(text) > n else "")


def _reply_mail(session, r: Reply) -> EmailMessage:
    em = session.get(Email, r.email_id)
    contact = session.get(Contact, em.contact_id) if em else None
    company = session.get(Company, em.company_id) if em else None
    who = (contact.name if contact and contact.name else None) or parseaddr(r.from_addr)[0] or r.from_addr
    role = f", {contact.role}" if contact and contact.role else ""
    cname = company.name if company else "their company"
    reason, _, action = (r.sentiment_reason or "").partition(" | next: ")
    quoted = "\n".join(f"> {ln}" for ln in _excerpt(r.body).splitlines())
    orig = ""
    if em:
        sent = em.sent_at.replace(tzinfo=timezone.utc).astimezone() if em.sent_at else None
        orig = (f"Your email (#{em.id}{', sent ' + f'{sent:%a %d %b %H:%M}' if sent else ''}, to {em.to_email}):\n"
                f"Subject: {em.subject}\n\n{em.body}")
    events = ""
    try:
        from jobhunter.db import Event

        evs = session.exec(select(Event).where(Event.reply_id == r.id)).all()
        if evs:
            events = "\nThey proposed:\n" + "\n".join(
                f"  - {e.kind}{' at ' + e.starts_at.replace(tzinfo=timezone.utc).astimezone().strftime('%a %d %b %H:%M') if e.starts_at else ''}"
                f"{' — ' + e.link if e.link else ''}{' — ' + e.needs_action if e.needs_action else ''}" for e in evs) + "\n"
    except Exception:  # noqa: BLE001
        pass
    body = (
        f"{who}{role} at {cname} replied — and it reads as a yes.\n"
        f"From: {r.from_addr}\n\n"
        f"What they wrote:\n{quoted}\n\n"
        f"Why it was marked positive: {reason.strip() or '-'}\n{events}\n"
        "What to do next:\n"
        f"  1. Answer them from Gmail today, in the same thread (search: {em.to_email if em else r.from_addr}).\n"
        f"  2. {action.strip() or 'Give them what they asked for — the role link, your resume, a time.'}\n"
        "  3. If they offered a call, confirm a time (Outreach → Events in the dashboard).\n"
        "  4. Once the referral is in, thank them — and note it in the dashboard.\n\n"
        f"---\n{orig}\n\n---\n{HELP}"
    )
    return compose("positive-reply", f"Positive reply: {who} at {cname}", body)


def email_positive_replies(*, now: datetime | None = None, force_dry: bool = False) -> dict:
    """Every positive reply from the last REPLY_LOOKBACK_H hours not yet mailed, one mail each."""
    since = utcnow() - timedelta(hours=REPLY_LOOKBACK_H)
    with _state() as st:
        done = set(st["replies_emailed"])
    out = {"mailed": 0, "errors": []}
    with get_session() as s:
        rows = s.exec(select(Reply).where(Reply.sentiment == "positive", col(Reply.received_at) >= since)
                      .order_by(col(Reply.received_at))).all()
        for r in rows:
            if r.id in done:
                continue
            res = deliver(_reply_mail(s, r), force_dry=force_dry)
            if _delivered(res, force_dry):
                out["mailed"] += 1
                with _state() as st:
                    st["replies_emailed"] = _trim_list(st["replies_emailed"] + [r.id], 2000)
                _macos("Positive reply", f"{r.from_addr[:60]} — details in your mail")
            else:
                out["errors"].append(res.get("error"))
    return out


# ================================================================== DAILY DIGEST / status

def is_paused() -> str | None:
    """Why sending is paused (by a `pause` reply), or None."""
    try:
        with get_session() as s:
            raw = get_setting(s, PAUSE_KEY, "")
    except Exception as e:  # noqa: BLE001 — unreadable means "not paused": the send gate has its own checks
        log.debug("pause flag unreadable: %s", e)
        return None
    if not raw:
        return None
    try:
        d = json.loads(raw)
        return f"paused {d.get('by', '')} at {d.get('at', '?')}".replace("  ", " ")
    except ValueError:
        return "paused"


def set_paused(paused: bool, *, by: str = "email") -> None:
    with get_session() as s:
        set_setting(s, PAUSE_KEY, json.dumps({"at": datetime.now().isoformat(timespec="minutes"), "by": f"by {by}"})
                    if paused else "")


def _local_midnight_utc(days_ago: int = 0) -> datetime:
    m = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days_ago)
    return m.astimezone(timezone.utc).replace(tzinfo=None)


def _status_line() -> str:
    try:
        with get_session() as s:
            drafts = s.exec(select(func.count()).select_from(Email).where(Email.status == "draft")).one()
            approved = s.exec(select(func.count()).select_from(Email).where(Email.status == "approved")).one()
            sent = s.exec(select(func.count()).select_from(Email).where(
                col(Email.status).in_(["sent", "replied"]), col(Email.sent_at) >= _local_midnight_utc())).one()
        paused = is_paused()
        return (f"Now: {drafts} drafts waiting, {approved} approved, {sent} sent today, mode {_mode()}"
                f"{', SENDING ' + paused.upper() if paused else ''}.")
    except Exception as e:  # noqa: BLE001
        return f"Now: the database could not be read ({str(e)[:100]})."


def _jobs_text() -> str:
    lines = []
    try:
        from jobhunter import tick

        for kind in ("free", "send", "referrals"):
            last = tick.last(kind) or {}
            lines.append(f"  {kind:<10} last finished {str(last.get('finished') or last.get('at') or 'never')[:16]}"
                         f"{'  — ' + str(last.get('skipped'))[:60] if last.get('skipped') else ''}")
    except Exception as e:  # noqa: BLE001
        lines.append(f"  (could not read the job records: {str(e)[:100]})")
    return "\n".join(lines)


def status_text(*, issues: list[dict] | None = None) -> str:
    from jobhunter import watchdog

    issues = watchdog.check() if issues is None else issues
    parts = [_status_line(), "", "Jobs:", _jobs_text(), ""]
    if issues:
        parts += ["Needs a look:"] + [f"  - {i['title']}" for i in issues]
    else:
        parts.append("Nothing stuck.")
    return "\n".join(parts)


def digest_text(now: datetime | None = None, *, issues: list[dict] | None = None) -> str:
    from jobhunter import guardrails, watchdog

    now = now or datetime.now()
    issues = watchdog.check(now) if issues is None else issues
    y0, t0 = _local_midnight_utc(1), _local_midnight_utc(0)
    day_ago = utcnow() - timedelta(hours=24)
    out: list[str] = [f"Good morning. ZoNuLy, {now:%A %d %B}.", ""]
    with get_session() as s:
        drafts = s.exec(select(Email).where(Email.status == "draft")
                        .order_by(col(Email.quality).desc(), col(Email.id))).all()
        companies = {c.id: c.name for c in s.exec(select(Company).where(
            col(Company.id).in_(list({e.company_id for e in drafts}) or [0]))).all()}
        out.append(f"DRAFTS WAITING FOR YOUR REVIEW: {len(drafts)}")
        for e in drafts[:25]:
            out.append(f"  #{e.id:<5} {companies.get(e.company_id, '?')[:28]:<28} {e.to_email[:32]:<32} "
                       f"{(e.subject or '')[:50]}{f'  (q{e.quality})' if e.quality is not None else ''}")
        if len(drafts) > 25:
            out.append(f"  … and {len(drafts) - 25} more in the dashboard")
        approved = s.exec(select(func.count()).select_from(Email).where(Email.status == "approved")).one()
        out += [f"Approved, waiting for the send window: {approved}", ""]

        sent = s.exec(select(Email).where(col(Email.status).in_(["sent", "replied"]),
                                          col(Email.sent_at) >= y0, col(Email.sent_at) < t0)).all()
        out.append(f"SENT YESTERDAY: {len(sent)}")
        for e in sent[:25]:
            out.append(f"  #{e.id:<5} {e.to_email[:36]:<36} {(e.subject or '')[:60]}")
        out.append("")

        replies = s.exec(select(Reply).where(col(Reply.received_at) >= day_ago)).all()
        by: dict[str, int] = {}
        for r in replies:
            by[r.sentiment or "neutral"] = by.get(r.sentiment or "neutral", 0) + 1
        out.append(f"REPLIES (last 24 h): {len(replies)}"
                   + (f"  ({', '.join(f'{v} {k}' for k, v in sorted(by.items()))})" if by else ""))
        for r in replies[:15]:
            out.append(f"  [{r.sentiment or 'neutral'}] {r.from_addr[:50]} — {(r.sentiment_reason or '').split(' | ')[0][:80]}")
        out.append("")

        new_people = s.exec(select(Contact).where(col(Contact.created_at) >= day_ago)).all()
        ready = [c for c in new_people if guardrails.is_ready(c)]
        with_email = [c for c in ready if c.email]
        out.append(f"REFERRERS ADDED (last 24 h): {len(ready)} confirmed-current ({len(with_email)} with an address), "
                   f"{len(new_people)} people found in all")
        names = {c.id: c.name for c in s.exec(select(Company).where(
            col(Company.id).in_(list({c.company_id for c in ready}) or [0]))).all()}
        per: dict[str, int] = {}
        for c in ready:
            per[names.get(c.company_id, "?")] = per.get(names.get(c.company_id, "?"), 0) + 1
        for name, n in sorted(per.items(), key=lambda kv: -kv[1])[:10]:
            out.append(f"  {n:>3}  {name}")
        out.append("")

    stuck = [f"  - {i['title']}" for i in issues]
    paused = is_paused()
    if paused:
        stuck.insert(0, f"  - sending is {paused} (reply `resume` to undo)")
    try:
        from jobhunter import audit

        last = (audit.latest() or {}).get("last") or {}
        stuck += [f"  - audit {last.get('at', '')[:16]}: {a}" for a in (last.get("alerts") or [])]
    except Exception:  # noqa: BLE001
        pass
    out.append("ANYTHING STUCK:" if stuck else "ANYTHING STUCK: no.")
    out += stuck
    out += ["", "Jobs:", _jobs_text(), "", HELP]
    return "\n".join(out)


def daily_digest(now: datetime | None = None, *, force: bool = False, force_dry: bool = False) -> dict:
    """Once a day, after DIGEST_HOUR local."""
    now = now or datetime.now()
    today = now.strftime("%Y-%m-%d")
    with _state() as st:
        if not force and (now.hour < DIGEST_HOUR or st.get("digest_day") == today):
            return {"skipped": "not due"}
    body = digest_text(now)
    r = deliver(compose("digest", f"Daily digest {now:%a %d %b}", body), force_dry=force_dry)
    if _delivered(r, force_dry) and not force:
        with _state() as st:
            st["digest_day"] = today
    return r


# ================================================================== reply-to-act

_CMD = re.compile(r"^(approve|reject)\s+((?:#?\d+[\s,]*)+)$|^(pause|resume|status|help)$", re.I)


def parse_commands(text: str) -> list[tuple[str, list[int]]]:
    """Commands from the new part of a reply: one per line, whole line, case-insensitive.
    Stops at the quoted original ("On … wrote:", "> …") or a signature ("-- ")."""
    lines = (text or "").replace("\r\n", "\n").split("\n")
    cmds: list[tuple[str, list[int]]] = []
    for i, raw in enumerate(lines):
        line = raw.strip().strip("*_").strip()
        nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
        if line.startswith(">") or raw.rstrip() in ("--", "-- ") or line.startswith("-----Original"):
            break
        if re.match(r"^On .+", line) and ("wrote:" in line or nxt.endswith("wrote:")):
            break
        if re.match(r"^From: .+", line) and cmds:
            break
        m = _CMD.match(line)
        if not m:
            continue
        if m.group(1):
            ids = [int(x) for x in re.findall(r"\d+", m.group(2))]
            cmds.append((m.group(1).lower(), list(dict.fromkeys(ids))))
        else:
            cmds.append((m.group(3).lower(), []))
    return cmds


def execute(cmds: list[tuple[str, list[int]]]) -> list[str]:
    """Run the commands; one result line each. Approve/reject are the dashboard's own functions."""
    from jobhunter.outreach import sender

    out: list[str] = []
    for verb, ids in cmds:
        try:
            if verb in ("approve", "reject"):
                fn = sender.approve if verb == "approve" else sender.reject
                for i in ids:
                    r = fn(i)
                    out.append(f"{verb} #{i}: {'done' if not r.get('error') else 'NOT done — ' + r['error']}")
            elif verb == "pause":
                set_paused(True)
                out.append("pause: sending is paused — the send agent will send nothing until you reply `resume`.")
            elif verb == "resume":
                set_paused(False)
                out.append("resume: sending is back on (inside the send window, within the daily cap).")
            elif verb == "status":
                out.append("status:\n" + status_text())
            elif verb == "help":
                out.append(HELP)
        except Exception as e:  # noqa: BLE001 — one command failing must not lose the others' results
            log.exception("command %s failed", verb)
            out.append(f"{verb} {' '.join(map(str, ids))}: failed — {type(e).__name__}: {str(e)[:160]}")
    return out


def _text_of(msg) -> str:
    parts = list(msg.walk()) if msg.is_multipart() else [msg]
    for want in ("text/plain", "text/html"):
        for p in parts:
            if p.get_content_type() == want and not p.get("Content-Disposition"):
                try:
                    t = str(p.get_content())
                except Exception:  # noqa: BLE001
                    continue
                if want == "text/html":
                    t = re.sub(r"<br\s*/?>|</p>|</div>", "\n", t, flags=re.I)
                    t = re.sub(r"<blockquote.*", "", t, flags=re.I | re.S)
                    t = re.sub(r"<[^>]+>", "", t)
                return t
    return ""


def accept(msg, bot_ids: set[str], done: set[str]) -> str | None:
    """Why this message is NOT a command for the bot — None when it is."""
    mid = (msg.get("Message-ID") or "").strip()
    if not mid or mid in done:
        return "already handled"
    if msg.get(BOT_HEADER) or mid in bot_ids:
        return "the bot's own mail"
    if parseaddr(msg.get("From") or "")[1].lower() != EMAIL_TO.lower():
        return "not from the configured address"
    refs = set(re.findall(r"<[^>]+>", f"{msg.get('In-Reply-To') or ''} {msg.get('References') or ''}"))
    if not refs & bot_ids:
        return "not a reply to a bot mail"
    auth = (msg.get("Authentication-Results") or "").lower()
    if re.search(r"\b(dmarc|spf|dkim)=(fail|softfail)", auth):
        return "failed authentication"
    return None


def _imap():
    from jobhunter.outreach import gmail

    pw = gmail.app_password()
    if not gmail.ADDRESS or not pw:
        raise gmail.GmailNotConfigured("Gmail app password missing (GMAIL_APP_PASSWORD)")
    conn = imaplib.IMAP4_SSL(gmail.IMAP_HOST, timeout=30)
    conn.login(gmail.ADDRESS, pw)
    return conn


def fetch_candidates(days: int = 3) -> list:
    """Messages from EMAIL_TO in the last few days (All Mail), parsed. Header-only first."""
    since = (datetime.now() - timedelta(days=days)).strftime("%d-%b-%Y")
    msgs = []
    with _imap() as conn:
        conn.select('"[Gmail]/All Mail"', readonly=True)
        try:   # Gmail's own search: his mail whose subject carries the bot's prefix
            _, data = conn.search(None, "X-GM-RAW", f'"from:{EMAIL_TO} subject:ZoNuLy newer_than:{days}d"')
        except imaplib.IMAP4.error:
            _, data = conn.search(None, "SINCE", since, "FROM", f'"{EMAIL_TO}"')
        for num in (data[0] or b"").split()[-200:]:
            _, fetched = conn.fetch(num, "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID IN-REPLY-TO REFERENCES X-ZONULY-BOT)])")
            if not fetched or not isinstance(fetched[0], tuple):
                continue
            head = email_lib.message_from_bytes(fetched[0][1], policy=email.policy.default)
            if head.get(BOT_HEADER) or not (head.get("In-Reply-To") or head.get("References")):
                continue
            _, full = conn.fetch(num, "(BODY.PEEK[])")
            if full and isinstance(full[0], tuple):
                msgs.append(email_lib.message_from_bytes(full[0][1], policy=email.policy.default))
    return msgs


def handle_commands(messages: list | None = None, *, force_dry: bool = False) -> dict:
    """Read replies to the bot, run their commands, confirm by reply."""
    out = {"checked": 0, "commands": 0, "ignored": {}}
    with _state() as st:
        bot_ids, done = set(st["threads"]), set(st["commands_done"])
    if messages is None:
        messages = fetch_candidates()
    for msg in messages:
        out["checked"] += 1
        why = accept(msg, bot_ids, done)
        if why:
            out["ignored"][why] = out["ignored"].get(why, 0) + 1
            continue
        mid = msg["Message-ID"].strip()
        with _state() as st:   # claimed before acting: a crash never runs a command twice
            st["commands_done"] = _trim_list(st["commands_done"] + [mid])
        done.add(mid)
        cmds = parse_commands(_text_of(msg))
        results = execute(cmds) if cmds else [
            "I could not find a command in your reply (a command is a whole line, e.g. `approve 12`).\n\n" + HELP]
        out["commands"] += len(cmds)
        subj = str(msg.get("Subject") or "ZoNuLy")
        subj = subj if subj.lower().startswith("re:") else f"Re: {subj}"
        body = "\n".join(results) + f"\n\n{_status_line()}\n\n-- \nZoNuLy bot. {HELP}"
        deliver(compose("confirm", subj, body, in_reply_to=mid, references=msg.get("References"), raw_subject=True),
                force_dry=force_dry)
        log.info("notify bot: ran %d command(s) from %s", len(cmds), mid)
    return out


# ================================================================== the cycle

def _fallback_poll(now: datetime) -> dict | None:
    """Replies are polled by the free tick. If it has not managed for a while (stuck, as on
    2026-10-15), poll here so a positive reply is not missed."""
    with get_session() as s:
        raw = get_setting(s, "outreach:last_poll_at", "")
    try:
        last = datetime.fromisoformat(raw) if raw else None
    except ValueError:
        last = None
    if last and now - last < timedelta(minutes=REPLY_POLL_FALLBACK_MIN):
        return None
    from jobhunter import tick

    return tick.poll_replies()


def cycle(now: datetime | None = None, *, force: bool = False, fallback_poll: bool = False,
          issues: list[dict] | None = None, force_dry: bool = False) -> dict:
    """One run: watchdog → URGENT, positive replies, the digest when due, commands.
    Runs from the send tick (every 5 min) and the hourly audit. Every step is guarded."""
    if not (ENABLED or force):
        return {"skipped": "notify.email is off"}
    now = now or datetime.now()
    out: dict = {}

    def step(name, fn):
        t = time.time()
        try:
            out[name] = fn()
        except Exception as e:  # noqa: BLE001 — one step failing must not stop the others
            log.warning("notify bot step %s failed: %s", name, e)
            out[name] = {"error": f"{type(e).__name__}: {str(e)[:200]}"}
        if time.time() - t > 20:
            out.setdefault("_slow", {})[name] = round(time.time() - t)

    if issues is None:
        from jobhunter import watchdog

        step("watchdog", lambda: watchdog.check(now))
        issues = out["watchdog"] if isinstance(out.get("watchdog"), list) else []
    step("urgent", lambda: [r for r in alert_issues(issues, now=now, force_dry=force_dry) if not r.get("skipped")])
    if fallback_poll:
        step("reply_poll", lambda: _fallback_poll(now))
    step("positive", lambda: email_positive_replies(now=now, force_dry=force_dry))
    step("digest", lambda: daily_digest(now, force_dry=force_dry))
    if COMMANDS and force_dry:
        out["commands"] = {"skipped": "a dry preview reads no mailbox and runs no command"}
    elif COMMANDS:
        def _cmds():
            with _state() as st:
                last = st.get("last_command_poll")
            if last and now - datetime.fromisoformat(last) < timedelta(minutes=COMMAND_POLL_MIN) and not force:
                return {"skipped": "polled recently"}
            with _state() as st:
                st["last_command_poll"] = now.isoformat(timespec="seconds")
            try:
                return handle_commands(force_dry=force_dry)
            except (imaplib.IMAP4.error, OSError) as e:
                if re.search(r"AUTHENTICATIONFAILED|Invalid credentials", str(e), re.I):
                    urgent("gmail-auth", "Gmail sign-in failing",
                           f"The notification bot could not sign in to read your replies: {e}", now=now)
                raise
        step("commands", _cmds)
    if isinstance(out.get("watchdog"), list):
        out["watchdog"] = [i["key"] for i in out["watchdog"]]
    return out


def test_mail(*, force_dry: bool = False) -> dict:
    """One real test notification (or a .eml with force_dry): proves SMTP and the reply loop."""
    body = ("This is a test from the ZoNuLy notification bot.\n\n"
            "If you can read this, positive replies, urgent alerts and the morning digest will reach you here.\n"
            "Try the reply loop: reply to this mail with the single line\n\n    status\n\n"
            "and within a few minutes you should get a confirmation back in this thread (with notify.email\n"
            "still off, run `python scripts/run.py notify commands` to read the reply now).\n\n"
            f"{_status_line()}\n\n{HELP}")
    return deliver(compose("test", "Test notification", body), force_dry=force_dry)


__all__ = ["cycle", "urgent", "audit_alerts", "email_positive_replies", "daily_digest", "digest_text",
           "status_text", "handle_commands", "parse_commands", "execute", "accept", "is_paused", "set_paused",
           "test_mail", "deliver", "compose", "ENABLED", "EMAIL_TO"]
