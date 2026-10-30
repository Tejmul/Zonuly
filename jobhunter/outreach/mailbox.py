"""Plain SMTP + IMAP transport for the domain inboxes (Zoho on tejmul.in, or any provider).

The same message the Gmail sends (gmail.build_message, with the inbox's name and address),
and the same dicts back for the tracker (gmail.thread_messages / messages_from shape), but
with standard IMAP only: no X-GM-THRID, no X-GM-RAW. A thread is our mail's Message-ID; a
reply is found by its In-Reply-To / References, a bounce by a mailer-daemon message that
names the recipient. Errors raise gmail.GmailNotConfigured on a refused sign-in, so the
sender and the tracker handle both transports the same way.
"""

from __future__ import annotations

import email as email_lib
import email.policy
import imaplib
import logging
import re
import smtplib
from datetime import datetime
from email.utils import parseaddr

from jobhunter.outreach import gmail
from jobhunter.outreach.inboxes import Inbox

log = logging.getLogger(__name__)


def _imap(inbox: Inbox) -> imaplib.IMAP4_SSL:
    pw = inbox.password()
    if not pw:
        raise gmail.GmailNotConfigured(f"no password for {inbox.address}: set {inbox.password_env} in .env")
    try:
        conn = imaplib.IMAP4_SSL(inbox.imap_host, inbox.imap_port, timeout=30)
        conn.login(inbox.address, pw)
    except imaplib.IMAP4.error as e:
        raise gmail.GmailNotConfigured(f"{inbox.imap_host} refused the sign-in for {inbox.address}: {e}") from e
    return conn


def _smtp(inbox: Inbox) -> smtplib.SMTP:
    pw = inbox.password()
    if not pw:
        raise gmail.GmailNotConfigured(f"no password for {inbox.address}: set {inbox.password_env} in .env")
    if inbox.smtp_port == 465:
        conn: smtplib.SMTP = smtplib.SMTP_SSL(inbox.smtp_host, inbox.smtp_port, timeout=30)
    else:
        conn = smtplib.SMTP(inbox.smtp_host, inbox.smtp_port, timeout=30)
        conn.starttls()
    try:
        conn.login(inbox.address, pw)
    except smtplib.SMTPAuthenticationError as e:
        conn.close()
        raise gmail.GmailNotConfigured(f"{inbox.smtp_host} refused the sign-in for {inbox.address}: {e}") from e
    return conn


def verify(inbox: Inbox) -> dict:
    """Sign in over SMTP and IMAP once: {ok, address, error}."""
    try:
        with _smtp(inbox):
            pass
        conn = _imap(inbox)
        conn.select("INBOX", readonly=True)
        conn.logout()
        return {"ok": True, "address": inbox.address, "error": None}
    except (gmail.GmailNotConfigured, OSError, imaplib.IMAP4.error, smtplib.SMTPException) as e:
        return {"ok": False, "address": inbox.address, "error": str(e)[:300]}


def send(inbox: Inbox, to: str, subject: str, body: str, *, in_reply_to: str | None = None) -> dict:
    """Send one mail from this inbox. {"id": Message-ID, "threadId": Message-ID}: the thread is
    named by our first mail's Message-ID, which every reply carries in its References."""
    msg = gmail.build_message(to, subject, body, in_reply_to=in_reply_to, sender=(inbox.name, inbox.address))
    with _smtp(inbox) as conn:
        conn.send_message(msg)
    mid = msg["Message-ID"]
    return {"id": mid, "threadId": in_reply_to or mid}


def _date(since: datetime) -> str:
    return since.strftime("%d-%b-%Y")


def _search(conn, *criteria: str) -> list[bytes]:
    try:
        typ, data = conn.search(None, *criteria)
    except imaplib.IMAP4.error as e:
        log.debug("imap search %s failed: %s", criteria, e)
        return []
    return (data[0] or b"").split() if typ == "OK" and data else []


def _boxes(conn, names) -> list[str]:
    """The mailboxes in `names` this server has (Zoho calls it Spam, others Junk)."""
    out = []
    for name in names:
        try:
            typ, _ = conn.select(name, readonly=True)
        except imaplib.IMAP4.error:
            continue
        if typ == "OK":
            out.append(name)
    return out


def sent_already(inbox: Inbox, to: str, subject: str, since: datetime) -> dict | None:
    """Our mail to `to` with this subject in the Sent folder since `since` — a retry asks here
    first, so a mail the server took before the connection dropped never goes twice."""
    addr = re.sub(r"[^a-z0-9@._+-]", "", (to or "").lower())
    if not addr:
        return None
    conn = _imap(inbox)
    try:
        if not _boxes(conn, [inbox.sent_box]):
            return None
        conn.select(inbox.sent_box, readonly=True)
        for num in reversed(_search(conn, "TO", f'"{addr}"', "SINCE", _date(since))):
            _, fetched = conn.fetch(num, "(BODY.PEEK[HEADER.FIELDS (SUBJECT MESSAGE-ID)])")
            if not fetched or not isinstance(fetched[0], tuple):
                continue
            head = email_lib.message_from_bytes(fetched[0][1], policy=email.policy.default)
            if " ".join(str(head.get("Subject") or "").split()) == " ".join((subject or "").split()):
                mid = str(head.get("Message-ID") or "").strip()
                return {"id": mid, "threadId": mid}
    finally:
        conn.logout()
    return None


def _read(conn, num: bytes, inbox: Inbox, **extra) -> dict | None:
    _, fetched = conn.fetch(num, "(BODY.PEEK[])")
    if not fetched or not isinstance(fetched[0], tuple):
        return None
    msg = email_lib.message_from_bytes(fetched[0][1], policy=email.policy.default)
    sender = (msg.get("From") or "").strip()
    mid = str(msg.get("Message-ID") or "").strip() or f"imap:{num.decode()}"
    return {"id": f"{inbox.key}:{mid}", "from": sender, "date": msg.get("Date"), "text": gmail._plain_text(msg),
            "is_bounce": gmail._is_bounce(msg), "is_ours": parseaddr(sender)[1].lower() == inbox.address.lower(),
            "is_auto": gmail._is_auto(msg), "refs": f"{msg.get('In-Reply-To') or ''} {msg.get('References') or ''}",
            **extra}


def thread_messages(inbox: Inbox, message_id: str, to: str, since: datetime) -> list[dict]:
    """Replies to our mail (In-Reply-To or References name its Message-ID) and bounces of it
    (a mailer-daemon / postmaster message that names the address or the Message-ID), in the
    inbox and the spam folder, oldest first."""
    mid = (message_id or "").strip()
    if not mid:
        return []
    addr = (to or "").strip().lower()
    out: list[dict] = []
    conn = _imap(inbox)
    try:
        for box in _boxes(conn, ["INBOX", *inbox.spam_boxes]):
            conn.select(box, readonly=True)
            extra = {} if box == "INBOX" else {"spam": True}
            for num, kind in _candidates(conn, since, mid):
                m = _read(conn, num, inbox, **extra)
                if not m or any(o["id"] == m["id"] for o in out):
                    continue
                if kind == "reply" or (m["is_bounce"] and (
                        mid in m["text"] or mid in m["refs"] or (addr and addr in m["text"].lower()))):
                    out.append(m)
    finally:
        conn.logout()
    out.sort(key=gmail._when)
    return out


_HEADS = "(BODY.PEEK[HEADER.FIELDS (FROM IN-REPLY-TO REFERENCES CONTENT-TYPE X-FAILED-RECIPIENTS)])"


def _candidates(conn, since: datetime, mid: str) -> list[tuple[bytes, str]]:
    """(num, "reply" | "bounce?") for the mail since `since` that answers `mid` or may be a
    bounce. Matched here on the headers, not with an IMAP HEADER search: Zoho answers every
    HEADER search with nothing (2026-10-30, a real reply on movin@ was missed that way)."""
    nums = _search(conn, "SINCE", _date(since))
    out: list[tuple[bytes, str]] = []
    for i in range(0, len(nums), 200):
        _, fetched = conn.fetch(b",".join(nums[i:i + 200]), _HEADS)
        for item in fetched or []:
            if not isinstance(item, tuple):
                continue
            num = item[0].split()[0]
            head = email_lib.message_from_bytes(item[1], policy=email.policy.default)
            refs = f"{head.get('In-Reply-To') or ''} {head.get('References') or ''}"
            sender = parseaddr(str(head.get("From") or ""))[1].lower()
            if mid in refs:
                out.append((num, "reply"))
            elif (sender.startswith(("mailer-daemon@", "postmaster@")) or head.get("X-Failed-Recipients")
                  or "multipart/report" in str(head.get("Content-Type") or "").lower()):
                out.append((num, "bounce?"))
    return out


def messages_from(inbox: Inbox, who: str, since: datetime) -> list[dict]:
    """Mail from `who` (a domain, or one address) since `since`, in the inbox and spam."""
    who = re.sub(r"[^a-z0-9@._+-]", "", (who or "").lower())
    if not who:
        return []
    out: list[dict] = []
    conn = _imap(inbox)
    try:
        for box in _boxes(conn, ["INBOX", *inbox.spam_boxes]):
            conn.select(box, readonly=True)
            extra = {} if box == "INBOX" else {"spam": True}
            for num in _search(conn, "SINCE", _date(since), "FROM", f'"{who}"'):
                m = _read(conn, num, inbox, **extra)
                if m and not any(o["id"] == m["id"] for o in out):
                    out.append(m)
    finally:
        conn.logout()
    out.sort(key=gmail._when)
    return out


__all__ = ["send", "sent_already", "thread_messages", "messages_from", "verify"]
