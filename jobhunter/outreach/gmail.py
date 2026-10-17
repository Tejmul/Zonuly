"""Gmail transport shared by the sender and the reply tracker.

Two ways in, chosen by `gmail.auth` in config.yaml:

    app_password   (default, 2026-09-22)  SMTP to send, IMAP to read the thread back.
                   Needs only 2-Step Verification on the account and a 16-character
                   app password in .env (GMAIL_APP_PASSWORD). No Google Cloud project,
                   no consent screen, no token that expires every seven days.
    oauth          the Gmail API through a desktop-app OAuth client — kept for the
                   Calendar push, which has no IMAP equivalent.

Mail leaves as "<outreach.user_name> <outreach.user_email>" — Tejmul's name and
address — whichever way in is used. The password is a secret: it comes from the
environment, is written only to the gitignored .env, and is never returned by a route.
"""

from __future__ import annotations

import email as email_lib
import email.policy
import imaplib
import logging
import re
import smtplib
import time
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid, parseaddr, parsedate_to_datetime
from pathlib import Path

from jobhunter import CONFIG, ROOT, secret

log = logging.getLogger(__name__)

_G = CONFIG.get("gmail") or {}
_O = CONFIG.get("outreach") or {}
AUTH = str(_G.get("auth", "app_password")).lower()
ADDRESS = str(_O.get("user_email") or "").strip()
DISPLAY_NAME = str(_O.get("user_name") or "").strip()
PASSWORD_ENV = "GMAIL_APP_PASSWORD"
SMTP_HOST, SMTP_PORT = "smtp.gmail.com", 465
IMAP_HOST = "imap.gmail.com"
ENV_FILE = ROOT / ".env"

CREDENTIALS_FILE = ROOT / _G.get("credentials_file", "secrets/gmail_credentials.json")
TOKEN_FILE = ROOT / _G.get("token_file", "secrets/gmail_token.json")

# OAuth only: send + read own mail, and write events to our own calendar.
SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.events",
]


class GmailNotConfigured(RuntimeError):
    """No credentials yet, or Google refused them."""


# ================================================================ app password

def app_password() -> str:
    return secret(PASSWORD_ENV).replace(" ", "")   # Google shows it in groups of four


def from_header() -> str:
    return formataddr((DISPLAY_NAME, ADDRESS)) if DISPLAY_NAME else ADDRESS


def _imap() -> imaplib.IMAP4_SSL:
    pw = app_password()
    if not ADDRESS or not pw:
        raise GmailNotConfigured(f"Gmail app password missing: set {PASSWORD_ENV} in .env (Settings → Email).")
    try:
        conn = imaplib.IMAP4_SSL(IMAP_HOST, timeout=30)
        conn.login(ADDRESS, pw)
    except imaplib.IMAP4.error as e:
        raise GmailNotConfigured(f"Google refused the sign-in for {ADDRESS}: {e}") from e
    return conn


def _smtp() -> smtplib.SMTP_SSL:
    pw = app_password()
    if not ADDRESS or not pw:
        raise GmailNotConfigured(f"Gmail app password missing: set {PASSWORD_ENV} in .env (Settings → Email).")
    conn = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30)
    try:
        conn.login(ADDRESS, pw)
    except smtplib.SMTPAuthenticationError as e:
        conn.close()
        raise GmailNotConfigured(f"Google refused the sign-in for {ADDRESS}: {e.smtp_error.decode(errors='replace') if isinstance(e.smtp_error, bytes) else e}") from e
    return conn


_verified: dict = {"at": 0.0, "ok": False, "error": None}


def verify(*, force: bool = False) -> dict:
    """Sign in over IMAP and SMTP once, and remember the answer for ten minutes so the
    Settings page and the sidebar do not log in on every render."""
    if not force and _verified["at"] and time.time() - _verified["at"] < 600:
        return {"ok": _verified["ok"], "address": ADDRESS if _verified["ok"] else None, "error": _verified["error"]}
    try:
        with _imap() as conn:
            conn.select('"[Gmail]/Sent Mail"', readonly=True)
        with _smtp():
            pass
        _verified.update(at=time.time(), ok=True, error=None)
        return {"ok": True, "address": ADDRESS, "error": None}
    except (GmailNotConfigured, OSError, imaplib.IMAP4.error, smtplib.SMTPException) as e:
        _verified.update(at=time.time(), ok=False, error=str(e)[:300])
        return {"ok": False, "address": None, "error": str(e)[:300]}


def install_app_password(password: str) -> dict:
    """Write GMAIL_APP_PASSWORD into the gitignored .env (mode 600) and into this process."""
    import os

    pw = re.sub(r"\s+", "", password or "")
    if len(pw) != 16 or not pw.isalpha():
        raise ValueError("A Gmail app password is 16 letters (Google shows it as four groups of four).")
    if not ADDRESS:
        raise ValueError("outreach.user_email is empty in config.yaml — set the sending address first.")
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    lines = [ln for ln in lines if not ln.startswith(f"{PASSWORD_ENV}=")]
    lines.append(f"{PASSWORD_ENV}={pw}")
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ENV_FILE.chmod(0o600)
    os.environ[PASSWORD_ENV] = pw
    _verified.update(at=0.0)
    log.info("installed a Gmail app password for %s", ADDRESS)
    return setup_status()


def _forget_app_password() -> None:
    import os

    if ENV_FILE.exists():
        lines = [ln for ln in ENV_FILE.read_text(encoding="utf-8").splitlines() if not ln.startswith(f"{PASSWORD_ENV}=")]
        ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.environ.pop(PASSWORD_ENV, None)
    _verified.update(at=0.0, ok=False, error=None)


def send(to: str, subject: str, body: str, *, in_reply_to: str | None = None) -> dict:
    """Send one plain-text mail as Tejmul and return where Gmail filed it.

    Returns {"id": <RFC Message-ID>, "threadId": <Gmail X-GM-THRID or the Message-ID>}.
    The thread id is read back from Sent Mail over IMAP so the tracker can poll the
    same conversation the way the API path did; when Gmail is slow to file the copy
    the Message-ID stands in and the poller resolves it later."""
    msg = EmailMessage()
    msg["From"] = from_header()
    msg["To"] = to
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid(domain=ADDRESS.split("@")[-1] or "gmail.com")
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    msg["Date"] = formatdate(localtime=True)
    # 2026-10-17: a plain-text-only message with bare URLs and no Date landed in Spam ("similar
    # to messages identified as spam"). Gmail's own compose sends text + its minimal HTML
    # (<div dir="ltr">, links on words); this mirrors it exactly so it reads person-to-person.
    msg.set_content(as_plain(body))
    msg.add_alternative(as_gmail_html(body), subtype="html")

    with _smtp() as conn:
        conn.send_message(msg)
    message_id = msg["Message-ID"]
    thread_id = _thread_of(message_id) or message_id
    return {"id": message_id, "threadId": thread_id}


def _thread_of(message_id: str, attempts: int = 4) -> str | None:
    """Gmail's thread id for a message we just sent, from the Sent Mail copy."""
    for i in range(attempts):
        try:
            with _imap() as conn:
                conn.select('"[Gmail]/Sent Mail"', readonly=True)
                _, data = conn.search(None, "HEADER", "Message-ID", message_id)
                nums = (data[0] or b"").split()
                if nums:
                    _, fetched = conn.fetch(nums[-1], "(X-GM-THRID)")
                    m = re.search(rb"X-GM-THRID (\d+)", fetched[0] if isinstance(fetched[0], bytes) else fetched[0][0])
                    if m:
                        return m.group(1).decode()
        except (GmailNotConfigured, OSError, imaplib.IMAP4.error) as e:
            log.debug("thread lookup failed: %s", e)
            return None
        time.sleep(1.5 * (i + 1))
    return None


def thread_messages(thread_id: str, *, message_id: str | None = None) -> list[dict]:
    """Every message Gmail files under a thread, oldest first, as
    {id, from, date, text, is_bounce, is_ours}. `id` is Gmail's X-GM-MSGID, stable
    across polls, so a reply is recorded once.

    With `message_id` (our mail's Message-ID), anything that references it is read too,
    whatever thread Gmail filed it under — a colleague answering from another address
    with a new subject, a bounce Gmail did not thread."""
    out: list[dict] = []
    with _imap() as conn:
        conn.select('"[Gmail]/All Mail"', readonly=True)
        if thread_id.isdigit():
            _, data = conn.search(None, "X-GM-THRID", thread_id)
        else:
            # not yet resolved to a Gmail thread: anything referencing our Message-ID
            _, data = conn.search(None, "OR", "HEADER", "In-Reply-To", thread_id, "HEADER", "References", thread_id)
        nums = list(dict.fromkeys((data[0] or b"").split()))
        ref = message_id if (message_id or "").startswith("<") else None
        if ref and ref != thread_id:
            try:
                _, more = conn.search(None, "OR", "HEADER", "In-Reply-To", ref, "HEADER", "References", ref)
                nums += [n for n in (more[0] or b"").split() if n not in nums]
            except imaplib.IMAP4.error as e:
                log.debug("reference search failed: %s", e)
        for num in nums:
            _, fetched = conn.fetch(num, "(X-GM-MSGID BODY.PEEK[])")
            if not fetched or not isinstance(fetched[0], tuple):
                continue
            meta, raw = fetched[0]
            gm = re.search(rb"X-GM-MSGID (\d+)", meta)
            msg = email_lib.message_from_bytes(raw, policy=email.policy.default)
            sender = (msg.get("From") or "").strip()
            out.append({
                "id": gm.group(1).decode() if gm else (msg.get("Message-ID") or f"imap:{num.decode()}"),
                "from": sender,
                "date": msg.get("Date"),
                "text": _plain_text(msg),
                "is_bounce": _is_bounce(msg),
                "is_ours": parseaddr(sender)[1].lower() == ADDRESS.lower(),
            })
        # All Mail holds every archived / filtered / labelled message, but not Spam: a reply
        # Gmail filed as spam is still a reply (2026-10-14). Only messages that reference
        # our own Message-ID are read there.
        if ref:
            try:
                typ, _ = conn.select('"[Gmail]/Spam"', readonly=True)
                if typ == "OK":
                    _, spam = conn.search(None, "OR", "HEADER", "In-Reply-To", ref, "HEADER", "References", ref)
                    for num in (spam[0] or b"").split():
                        _, fetched = conn.fetch(num, "(X-GM-MSGID BODY.PEEK[])")
                        if not fetched or not isinstance(fetched[0], tuple):
                            continue
                        meta, raw = fetched[0]
                        gm = re.search(rb"X-GM-MSGID (\d+)", meta)
                        msg = email_lib.message_from_bytes(raw, policy=email.policy.default)
                        sender = (msg.get("From") or "").strip()
                        mid = gm.group(1).decode() if gm else (msg.get("Message-ID") or f"spam:{num.decode()}")
                        if any(o["id"] == mid for o in out):
                            continue
                        out.append({"id": mid, "from": sender, "date": msg.get("Date"), "text": _plain_text(msg),
                                    "is_bounce": _is_bounce(msg),
                                    "is_ours": parseaddr(sender)[1].lower() == ADDRESS.lower(), "spam": True})
            except imaplib.IMAP4.error as e:
                log.debug("spam folder search failed: %s", e)
    out.sort(key=_when)
    return out


def _when(m: dict) -> float:
    try:
        return parsedate_to_datetime(m["date"]).timestamp() if m.get("date") else 0.0
    except (TypeError, ValueError):
        return 0.0


def _text_of(part) -> str:
    """The decoded text of one part, whichever email policy built it."""
    try:
        return str(part.get_content())
    except AttributeError:   # legacy compat32 Message
        payload = part.get_payload(decode=True)
        if payload is None:
            return ""
        return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
    except Exception:  # noqa: BLE001 — odd encodings
        return ""


def _plain_text(msg) -> str:
    """The text a person wrote: text/plain first, text/html as a fallback, never attachments."""
    parts = list(msg.walk()) if msg.is_multipart() else [msg]
    for part in parts:
        if part.get_content_type() == "text/plain" and not part.get("Content-Disposition"):
            return _text_of(part).strip()
    for part in parts:
        if part.get_content_type() == "text/html":
            from jobhunter.scrapers.base import html_to_text

            return html_to_text(_text_of(part)).strip()
    return ""


def _is_bounce(msg) -> bool:
    sender = parseaddr(msg.get("From") or "")[1].lower()
    return (
        msg.get_content_type() == "multipart/report"
        or sender.startswith("mailer-daemon@")
        or sender.startswith("postmaster@")
        or bool(msg.get("X-Failed-Recipients"))
    )


# ======================================================================= oauth

def configured() -> bool:
    if AUTH == "app_password":
        return bool(ADDRESS and app_password())
    return CREDENTIALS_FILE.exists()


def authorized() -> bool:
    if AUTH == "app_password":
        return configured() and verify()["ok"]
    return TOKEN_FILE.exists()


def status() -> dict:
    if AUTH == "app_password":
        v = verify() if configured() else {"ok": False, "error": None}
        return {
            "auth": AUTH, "configured": configured(), "authorized": v["ok"], "address": ADDRESS,
            "display_name": DISPLAY_NAME, "error": v.get("error"),
            "hint": None if v["ok"] else
            f"Turn on 2-Step Verification for {ADDRESS or 'the sending address'}, create an app password at "
            f"myaccount.google.com/apppasswords, and paste it in Settings → Email (or set {PASSWORD_ENV} in .env).",
        }
    return {
        "auth": AUTH,
        "configured": configured(),
        "authorized": authorized(),
        "address": ADDRESS,
        "display_name": DISPLAY_NAME,
        "credentials_file": str(CREDENTIALS_FILE),
        "token_file": str(TOKEN_FILE),
        "hint": (
            "Create an OAuth *desktop app* client in Google Cloud Console with the Gmail API "
            f"enabled, download the JSON to {CREDENTIALS_FILE}, then run "
            "`python scripts/run.py gmail-auth`."
        ),
    }


def _load_credentials(*, interactive: bool):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _save(creds)
            return creds
        except Exception as e:  # noqa: BLE001 — revoked/expired refresh token
            log.warning("token refresh failed, re-authorization needed: %s", e)

    if not interactive:
        raise GmailNotConfigured(
            "Gmail is not authorized. Run `python scripts/run.py gmail-auth` once to grant access."
        )
    if not CREDENTIALS_FILE.exists():
        raise GmailNotConfigured(f"Missing OAuth client JSON at {CREDENTIALS_FILE}. {status()['hint']}")

    flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent")
    _save(creds)
    return creds


def _save(creds) -> None:
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
    TOKEN_FILE.chmod(0o600)
    log.info("saved Gmail token to %s", TOKEN_FILE)


def service(*, interactive: bool = False):
    """The Gmail API client (OAuth mode only). Set interactive=True only from a terminal."""
    from googleapiclient.discovery import build

    creds = _load_credentials(interactive=interactive)
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def authorize() -> dict:
    """Run the one-time OAuth consent flow (opens a browser)."""
    svc = service(interactive=True)
    profile = svc.users().getProfile(userId="me").execute()
    return {"email": profile.get("emailAddress"), "messages_total": profile.get("messagesTotal")}


def my_address() -> str | None:
    if AUTH == "app_password":
        return ADDRESS if authorized() else None
    try:
        return service().users().getProfile(userId="me").execute().get("emailAddress")
    except Exception as e:  # noqa: BLE001 — surfaced in the dashboard, not fatal
        log.debug("could not read Gmail profile: %s", e)
        return None


# ---------------------------------------------------------------- setup flow (Settings → Email)

def _client_json() -> dict | None:
    import json

    if not CREDENTIALS_FILE.exists():
        return None
    try:
        return json.loads(CREDENTIALS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def validate_client_json(data: object) -> str | None:
    """Why this is not a usable *desktop app* OAuth client — None when it is."""
    if not isinstance(data, dict):
        return "Not a JSON object."
    if "web" in data and "installed" not in data:
        return "This is a *Web application* client. Create one with type *Desktop app* instead."
    installed = data.get("installed")
    if not isinstance(installed, dict):
        return "No `installed` section — not an OAuth client JSON downloaded from Google Cloud."
    for key in ("client_id", "client_secret"):
        if not installed.get(key):
            return f"`installed.{key}` is missing."
    return None


def install_credentials(data: dict) -> dict:
    """Write the uploaded OAuth client JSON to CREDENTIALS_FILE (mode 600)."""
    import json

    problem = validate_client_json(data)
    if problem:
        raise ValueError(problem)
    if TOKEN_FILE.exists():
        TOKEN_FILE.unlink()
    CREDENTIALS_FILE.parent.mkdir(parents=True, exist_ok=True)
    CREDENTIALS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    CREDENTIALS_FILE.chmod(0o600)
    log.info("installed Gmail OAuth client at %s", CREDENTIALS_FILE)
    return setup_status()


def disconnect(*, remove_client: bool = False) -> dict:
    """Forget the credentials so a different account can be used."""
    if AUTH == "app_password":
        _forget_app_password()
        return setup_status()
    if TOKEN_FILE.exists():
        TOKEN_FILE.unlink()
    if remove_client and CREDENTIALS_FILE.exists():
        CREDENTIALS_FILE.unlink()
    return setup_status()


def setup_status() -> dict:
    """Everything the Settings → Email panel shows: each step, what is done, what is next."""
    from jobhunter.outreach import sender

    common = {
        "auth": AUTH,
        "address": None,
        "address_configured": ADDRESS or None,
        "display_name": DISPLAY_NAME or None,
        "reply_to": ADDRESS or None,
        "send_mode": sender.SEND_MODE,
        "calendar_enabled": bool((CONFIG.get("calendar") or {}).get("enabled")) and AUTH == "oauth",
        "quota": sender.quota(),
    }

    if AUTH == "app_password":
        present = bool(app_password())
        v = verify() if present and ADDRESS else {"ok": False, "error": None}
        steps = [
            {"id": "address", "done": bool(ADDRESS and DISPLAY_NAME),
             "label": f"Sending as {DISPLAY_NAME or '?'} <{ADDRESS or '?'}> (outreach.user_name / user_email in config.yaml)"},
            {"id": "twostep", "done": present, "label": "2-Step Verification turned on for that Google account"},
            {"id": "password", "done": present, "label": "App password created and saved here"},
            {"id": "verify", "done": v["ok"], "label": "Google accepts the sign-in", "problem": v.get("error")},
            {"id": "mode", "done": sender.SEND_MODE == "gmail", "label": "Send mode switched from dry-run to Gmail"},
        ]
        next_step = next((s["id"] for s in steps if not s["done"]), None)
        return {
            **common,
            "configured": present,
            "authorized": v["ok"],
            "address": ADDRESS if v["ok"] else None,
            "password_present": present,
            "steps": steps,
            "next_step": next_step,
        }

    client = _client_json()
    client_problem = validate_client_json(client) if client is not None else None
    client_ok = client is not None and client_problem is None
    client_id = (client or {}).get("installed", {}).get("client_id", "") if client_ok else ""
    token_present = TOKEN_FILE.exists()
    address = my_address() if token_present else None
    steps = [
        {"id": "project", "done": client_ok, "label": "Google Cloud project with the Gmail and Calendar APIs enabled"},
        {"id": "consent", "done": client_ok, "label": "Consent screen, with the sending address added as a test user"},
        {"id": "client", "done": client_ok, "label": "Desktop-app OAuth client JSON installed", "problem": client_problem},
        {"id": "authorize", "done": bool(address), "label": "Signed in and consent granted"},
        {"id": "mode", "done": sender.SEND_MODE == "gmail", "label": "Send mode switched from dry-run to Gmail"},
    ]
    next_step = next((s["id"] for s in steps if not s["done"]), None)
    return {
        **common,
        "configured": client_ok,
        "authorized": bool(address),
        "credentials_file": str(CREDENTIALS_FILE),
        "token_file": str(TOKEN_FILE),
        "client_present": client is not None,
        "client_problem": client_problem,
        "client_id_hint": f"…{client_id[-24:]}" if client_id else None,
        "token_present": token_present,
        "token_stale": token_present and not address,
        "address": address,
        "steps": steps,
        "next_step": next_step,
    }


__all__ = [
    "AUTH", "ADDRESS", "DISPLAY_NAME", "GmailNotConfigured", "send", "thread_messages", "verify",
    "install_app_password", "configured", "authorized", "status", "service", "authorize", "my_address",
    "install_credentials", "validate_client_json", "disconnect", "setup_status", "from_header",
]

_MD_LINK = re.compile(r"\[([^\]\n]{1,80})\]\((https?://[^)\s]+)\)")
_BARE_URL = re.compile(r"(?<![\"'>(])(https?://[^\s<]+)")


def as_plain(body: str) -> str:
    """The text part: "[my resume](url)" becomes "my resume (url)"."""
    return _MD_LINK.sub(lambda m: f"{m.group(1)} ({m.group(2)})", body or "")


def as_gmail_html(body: str) -> str:
    """The HTML part the way Gmail's compose writes it: one <div> per line, an empty
    <div><br></div> between paragraphs, links on their words, nothing else — no styles,
    no images, no tracking."""
    import html as _h

    def line(t: str) -> str:
        t = _h.escape(t, quote=False)
        t = re.sub(r"\[([^\]\n]{1,80})\]\((https?://[^)\s]+)\)",
                   lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>', t)
        t = re.sub(r'(?<!href=")(?<!">)(https?://[^\s<]+)', lambda m: f'<a href="{m.group(1)}">{m.group(1)}</a>', t)
        return f"<div>{t}</div>" if t.strip() else "<div><br></div>"

    lines = (body or "").replace("\r\n", "\n").split("\n")
    return '<div dir="ltr">' + "".join(line(x) for x in lines) + "</div>\n"

