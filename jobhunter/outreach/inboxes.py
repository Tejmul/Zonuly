"""The sending inboxes — the personal Gmail plus any number of domain inboxes, each with its
own daily cap, its own warm-up ramp and its own gap between sends.

2026-10-30, Tejmul: "at least 100 to 300 emails per day without getting failed or marked as
spam". One inbox cannot do that: a personal Gmail was refused (5.4.5) after 24 on 10-19, and
providers trust an inbox that sends ~30 personal mails a day, not one that sends 300. So the
volume comes from many inboxes, each kept at a person's pace (config `outreach.inboxes`):

    - key: zoho-tejmul
      address: tejmul@tejmul.in
      smtp: smtp.zoho.in:465
      imap: imap.zoho.in:993
      password_env: ZOHO_PASS_TEJMUL
      cap: 30                       # this inbox's ceiling once warmed
      warmup: {first: 5, step: 5, every: 2}

An inbox whose password is not in .env is not ready and sends nothing, so an entry can sit in
the config before the inbox exists. A new inbox's ramp starts on the day of its first send
unless `warmup.start` says otherwise. The Gmail inbox ("gmail") is always the first, with the
caps it always had (outreach.daily_send_cap, outreach.warmup).

A domain inbox takes only addresses that are verified (or checked by verify_address): a fresh
domain's reputation is built on a bounce rate near zero, and guessed addresses bounce.
Nothing here can push the day past outreach.total_daily_cap.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from sqlmodel import col, func, select

from jobhunter import CONFIG, secret

_O = CONFIG.get("outreach") or {}
GMAIL = "gmail"
TOTAL_CAP = int(_O.get("total_daily_cap", _O.get("daily_send_cap", 25)))
MIN_GAP_MINUTES = float(_O.get("min_gap_minutes", 15))


@dataclass(frozen=True)
class Inbox:
    key: str
    address: str
    name: str = ""
    kind: str = "imap"                  # gmail | imap
    smtp_host: str = ""
    smtp_port: int = 465
    imap_host: str = ""
    imap_port: int = 993
    password_env: str = ""
    cap: int = 30
    warmup: dict = field(default_factory=dict)
    verified_only: bool = True
    sent_box: str = "Sent"
    spam_boxes: tuple[str, ...] = ("Spam", "Junk")

    @property
    def is_gmail(self) -> bool:
        return self.kind == GMAIL

    def password(self) -> str:
        if self.is_gmail:
            from jobhunter.outreach import gmail

            return gmail.app_password()
        return (secret(self.password_env) or os.environ.get(self.password_env) or "").replace(" ", "")

    def ready(self) -> bool:
        if self.is_gmail:
            from jobhunter.outreach import gmail

            return gmail.configured()
        return bool(self.address and self.smtp_host and self.password_env and self.password())


def _hostport(raw: str | None, default_port: int) -> tuple[str, int]:
    host, _, port = str(raw or "").partition(":")
    return host.strip(), int(port) if port.strip().isdigit() else default_port


def _gmail() -> Inbox:
    from jobhunter.outreach import gmail

    return Inbox(key=GMAIL, address=gmail.ADDRESS, name=gmail.DISPLAY_NAME, kind=GMAIL,
                 smtp_host=gmail.SMTP_HOST, smtp_port=gmail.SMTP_PORT, imap_host=gmail.IMAP_HOST,
                 password_env=gmail.PASSWORD_ENV, cap=int(_O.get("daily_send_cap", 25)),
                 warmup=dict(_O.get("warmup") or {}), verified_only=bool(_O.get("gmail_verified_only", False)),
                 sent_box='"[Gmail]/Sent Mail"', spam_boxes=('"[Gmail]/Spam"',))


def all_inboxes() -> list[Inbox]:
    """Every configured inbox, Gmail first, ready or not."""
    out = [_gmail()]
    for row in _O.get("inboxes") or []:
        if not isinstance(row, dict) or not row.get("address") or row.get("enabled") is False:
            continue
        smtp, sport = _hostport(row.get("smtp"), 465)
        imap, iport = _hostport(row.get("imap"), 993)
        key = str(row.get("key") or row["address"].split("@")[0] + "@" + row["address"].split("@")[-1])
        if key == GMAIL or any(i.key == key for i in out):
            continue
        out.append(Inbox(key=key, address=str(row["address"]).strip(),
                         name=str(row.get("name") or _O.get("user_name") or "").strip(), kind="imap",
                         smtp_host=smtp, smtp_port=sport, imap_host=imap, imap_port=iport,
                         password_env=str(row.get("password_env") or ""), cap=int(row.get("cap", 30)),
                         warmup=dict(row.get("warmup") or {"first": 5, "step": 5, "every": 2}),
                         verified_only=bool(row.get("verified_only", True)),
                         sent_box=str(row.get("sent_box") or "Sent"),
                         spam_boxes=tuple(row.get("spam_boxes") or ("Spam", "Junk"))))
    return out


def ready() -> list[Inbox]:
    return [i for i in all_inboxes() if i.ready()]


def sending(live: bool) -> list[Inbox]:
    """The inboxes a send run uses: the ready ones; in a dry run the Gmail as well, so the
    rehearsal runs without a password."""
    boxes = ready()
    if not live and not any(b.is_gmail for b in boxes):
        boxes.insert(0, _gmail())
    return boxes


def get(key: str | None) -> Inbox | None:
    key = key or GMAIL
    return next((i for i in all_inboxes() if i.key == key), None)


# ------------------------------------------------------------------------ the ramp

def _first_send(session, inbox: Inbox) -> date | None:
    from jobhunter.db import Email

    first = session.exec(select(func.min(Email.sent_at)).where(Email.sender_inbox == inbox.key)).one()
    return first.date() if first else None


def start(session, inbox: Inbox) -> date | None:
    from jobhunter.outreach import warmup

    raw = inbox.warmup.get("start")
    if raw:
        return warmup._as_date(raw)
    if inbox.is_gmail:
        return warmup.start()
    return _first_send(session, inbox)


def quota(session, inbox: Inbox, day: date | datetime | str | None = None) -> int:
    """This inbox's mail for `day`: the ramp first + step * (sending day // every), never above
    its cap. A new inbox with no start and no sends is on its first step."""
    from jobhunter.outreach import warmup

    d = warmup._as_date(day)
    if inbox.is_gmail:
        return min(inbox.cap, warmup.quota(d, cap=inbox.cap))
    w = inbox.warmup
    first, step, every = int(w.get("first", 5)), int(w.get("step", 5)), max(1, int(w.get("every", 2)))
    s = start(session, inbox)
    n = 0
    if s is not None and d > s:
        n = sum(1 for i in range((d - s).days) if warmup.is_send_day(s + timedelta(days=i)))
    return max(0, min(inbox.cap, first + step * (n // every)))


def total_quota(session, day: date | datetime | str | None = None) -> int:
    """The day's mail across the ready inboxes, never above outreach.total_daily_cap. Gmail
    always counts (as it always has), so a copy without passwords shows the same cap."""
    boxes = {i.key: i for i in ready()}
    boxes.setdefault(GMAIL, _gmail())
    return min(TOTAL_CAP, sum(quota(session, i, day) for i in boxes.values()))


def _midnight_utc(now: datetime | None = None) -> datetime:
    local = (now or datetime.now()).astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    return local.astimezone(timezone.utc).replace(tzinfo=None)


def used_today(session, inbox: Inbox, now: datetime | None = None) -> int:
    """Mail this inbox sent since local midnight (a bounce still went out). Mail sent before
    inboxes existed has no sender_inbox and was Gmail's."""
    from jobhunter.db import Email

    who = Email.sender_inbox == inbox.key
    if inbox.is_gmail:
        who = (Email.sender_inbox == inbox.key) | col(Email.sender_inbox).is_(None)
    return session.exec(select(func.count()).select_from(Email).where(
        who, col(Email.sent_at) >= _midnight_utc(now))).one()


def left_today(session, inbox: Inbox, now: datetime | None = None) -> int:
    return max(0, quota(session, inbox, now) - used_today(session, inbox, now))


def due(session, inbox: Inbox, now: datetime | None = None) -> int:
    """How many of this inbox's jittered slots in the India window have come due by `now`.
    Each inbox has its own slots (seeded by its key), so the inboxes do not fire together."""
    from jobhunter.outreach import warmup

    now = now or datetime.now()
    if inbox.is_gmail:
        return warmup.due(now, cap=inbox.cap)     # the slots the Gmail always had
    if not warmup.is_send_day(now):
        return 0
    return sum(1 for t in warmup.slots(now, quota(session, inbox, now), seed=inbox.key) if t <= now)


def gap_ok(session, inbox: Inbox, now: datetime | None = None) -> bool:
    """At least MIN_GAP_MINUTES (plus up to half again, fixed per last mail) since this inbox's
    last send. The gap is per inbox: one person writing, not the whole pool."""
    from jobhunter.db import Email

    who = Email.sender_inbox == inbox.key
    if inbox.is_gmail:
        who = (Email.sender_inbox == inbox.key) | col(Email.sender_inbox).is_(None)
    last = session.exec(select(Email).where(who, col(Email.sent_at).is_not(None))
                        .order_by(col(Email.sent_at).desc())).first()
    if last is None or last.sent_at is None:
        return True
    gap = MIN_GAP_MINUTES * (1 + 0.5 * ((last.id * 2654435761) % 1000) / 1000)
    now_utc = (now or datetime.now()).astimezone(timezone.utc).replace(tzinfo=None)
    return (now_utc - last.sent_at).total_seconds() >= gap * 60


BRAKE_HOURS = float(_O.get("inbox_brake_hours", 48))
_REFUSAL = re.compile(r"\b5\.\d\.\d|\b55\d\b|limit exceeded|spam|blocked|rejected", re.I)


def brake(session, inbox: Inbox, now: datetime | None = None) -> str | None:
    """Why this inbox must rest, or None. 2026-10-30, Tejmul: "nothing should go in spam and
    nothing should go blocked". One bounce or one refusal from an inbox in the last
    BRAKE_HOURS stops that inbox (the others go on): a provider that refused once is watching,
    and a second strike is what turns a pause into a block."""
    from jobhunter.db import Email

    if BRAKE_HOURS <= 0:
        return None
    since = (now or datetime.now()).astimezone(timezone.utc).replace(tzinfo=None) - timedelta(hours=BRAKE_HOURS)
    who = Email.sender_inbox == inbox.key
    if inbox.is_gmail:
        who = (Email.sender_inbox == inbox.key) | col(Email.sender_inbox).is_(None)
    bad = session.exec(select(Email).where(who, Email.status == "failed", col(Email.error).is_not(None),
                                           func.coalesce(Email.sent_at, Email.approved_at) >= since)).all()
    # a bounce, or a provider's refusal (5xx, blocked, spam, a sending limit) — not a dropped
    # network, a sign-in problem or a draft the address check stopped before it left
    bad = [e for e in bad if (e.error or "").startswith("bounced") or _REFUSAL.search(e.error or "")]
    cleared = _cleared(session, inbox)
    if cleared:   # Tejmul lifted the rest: only what failed after that counts
        bad = [e for e in bad if (e.sent_at or e.approved_at) and (e.sent_at or e.approved_at) > cleared]
    if not bad:
        return None
    last = bad[-1]
    return (f"{inbox.address} rests {BRAKE_HOURS:g}h: mail to {last.to_email} failed "
            f"({(last.error or '')[:80]})")


def _cleared(session, inbox: Inbox) -> datetime | None:
    from jobhunter.db import get_setting

    raw = get_setting(session, f"inbox:brake_cleared:{inbox.key}", "")
    try:
        return datetime.fromisoformat(raw) if raw else None
    except ValueError:
        return None


def lift(session, key: str) -> dict:
    """Lift an inbox's rest by hand (2026-10-30, Tejmul: "at least 25+ should be sent today"):
    the failures so far stop counting; a new bounce or refusal rests it again. The caller commits."""
    from jobhunter.db import set_setting, utcnow

    box = get(key)
    if box is None:
        return {"error": f"no inbox {key}"}
    was = brake(session, box)
    set_setting(session, f"inbox:brake_cleared:{box.key}", utcnow().isoformat())
    return {"inbox": box.key, "was": was, "lifted": True}


def takes(inbox: Inbox, *, guessed: bool) -> bool:
    """A domain inbox writes to verified addresses only (verified_only, the default)."""
    return not (guessed and inbox.verified_only)


def describe(session, now: datetime | None = None) -> list[dict]:
    out = []
    for i in all_inboxes():
        s = start(session, i)
        out.append({"key": i.key, "address": i.address, "ready": i.ready(), "cap": i.cap,
                    "resting": brake(session, i, now),
                    "quota_today": quota(session, i, now), "used_today": used_today(session, i, now),
                    "warmup_start": s.isoformat() if s else None, "verified_only": i.verified_only,
                    "password_env": None if i.is_gmail else i.password_env})
    return out


__all__ = ["Inbox", "GMAIL", "TOTAL_CAP", "all_inboxes", "ready", "sending", "get", "quota", "total_quota", "used_today",
           "left_today", "due", "gap_ok", "takes", "brake", "lift", "describe", "start"]
