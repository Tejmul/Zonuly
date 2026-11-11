"""Sending the 6-digit code.

Never through the outreach Gmail: that mailbox is the one Gmail blocked for bursts in
October, and a login code that lands late or in spam is a login that fails. Codes go from
the product's own address on tejmul.in (ZONULY_MAIL_FROM, default no-reply@tejmul.in),
whose domain already publishes SPF and DKIM for Zoho.

Three ways out, first that applies:

  * SMTP, when ZONULY_SMTP_HOST is set — Zoho Mail (smtp.zoho.in) or ZeptoMail
    (smtp.zeptomail.in), whichever the address is set up on;
  * Catalyst Mail, on the deployed instance — the sender must be added and confirmed under
    Cloud Scale → Mail in the Catalyst console; the call uses the gateway's credential on
    the request, the same way store.py does;
  * on a laptop with neither, the code is logged and handed back to the page, so the login
    can be tried locally. Never on a public instance — that would hand anyone a code for
    any address.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage

import httpx

from jobhunter import access, secret

log = logging.getLogger(__name__)


class MailError(RuntimeError):
    """The code could not be sent."""


def sender() -> str:
    return secret("ZONULY_MAIL_FROM") or "no-reply@tejmul.in"


def _subject(code: str) -> str:
    return f"{code} is your ZoNuLy code"


def _text(code: str) -> str:
    return (
        f"Your ZoNuLy sign-in code is {code}\n\n"
        "It works for 10 minutes. If you didn't ask for it, you can ignore this email — "
        "nobody can sign in without the code.\n\n— ZoNuLy · zonuly.tejmul.in\n"
    )


def _html(code: str) -> str:
    return (
        '<div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:440px;'
        'margin:0 auto;padding:32px 24px;color:#141a1f">'
        '<p style="font-size:13px;letter-spacing:.08em;text-transform:uppercase;color:#74848f;margin:0 0 16px">ZoNuLy</p>'
        '<p style="font-size:15px;margin:0 0 20px">Your sign-in code:</p>'
        f'<p style="font-size:34px;font-weight:700;letter-spacing:.18em;margin:0 0 20px;'
        f'font-family:SFMono-Regular,Menlo,monospace">{code}</p>'
        '<p style="font-size:13px;line-height:1.6;color:#41505c;margin:0">It works for 10 minutes. '
        "If you didn't ask for it, ignore this email — nobody can sign in without the code.</p>"
        "</div>"
    )


def mode(request=None) -> str:
    """Which way a code would go right now: smtp | catalyst | dev | none."""
    if secret("ZONULY_SMTP_HOST"):
        return "smtp"
    if request is not None and any(k.lower() == "x-zc-admin-cred-token" for k in request.headers.keys()):
        return "catalyst"
    if not access.PUBLIC:
        return "dev"
    return "none"


def _smtp(to: str, code: str) -> None:
    host = secret("ZONULY_SMTP_HOST")
    port = int(secret("ZONULY_SMTP_PORT") or 465)
    user = secret("ZONULY_SMTP_USER") or sender()
    password = secret("ZONULY_SMTP_PASSWORD")
    msg = EmailMessage()
    msg["From"] = f"ZoNuLy <{sender()}>"
    msg["To"] = to
    msg["Subject"] = _subject(code)
    msg.set_content(_text(code))
    msg.add_alternative(_html(code), subtype="html")
    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=20) as s:
                s.login(user, password)
                s.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=20) as s:
                s.starttls(context=ssl.create_default_context())
                s.login(user, password)
                s.send_message(msg)
    except (smtplib.SMTPException, OSError) as e:
        raise MailError(f"SMTP send failed: {e}") from e


def _catalyst(to: str, code: str, request) -> None:
    from jobhunter.accounts.store import CatalystStore, StoreError

    fields = [
        ("from_email", (None, sender())),
        ("to_email", (None, to)),
        ("subject", (None, _subject(code))),
        ("content", (None, _html(code))),
        ("html_mode", (None, "true")),
        ("display_name", (None, "ZoNuLy")),
    ]
    try:
        CatalystStore()._call(request, "POST", "/email/send", files=fields)
    except StoreError as e:
        raise MailError(f"Catalyst Mail refused: {e}") from e
    except httpx.HTTPError as e:  # pragma: no cover - wrapped above
        raise MailError(str(e)) from e


def send_code(to: str, code: str, request=None) -> str:
    """Send it; return the route taken. Raises MailError when it cannot go."""
    how = mode(request)
    if how == "smtp":
        _smtp(to, code)
    elif how == "catalyst":
        _catalyst(to, code, request)
    elif how == "dev":
        log.warning("accounts: no mail configured — code for %s is %s (local only)", to, code)
    else:
        raise MailError("No way to send mail is configured on this instance.")
    return how
