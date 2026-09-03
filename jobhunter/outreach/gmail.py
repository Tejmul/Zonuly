"""Gmail OAuth plumbing shared by the sender and the reply tracker."""

from __future__ import annotations

import logging
from pathlib import Path

from jobhunter import CONFIG, ROOT

log = logging.getLogger(__name__)

_G = CONFIG.get("gmail") or {}
CREDENTIALS_FILE = ROOT / _G.get("credentials_file", "secrets/gmail_credentials.json")
TOKEN_FILE = ROOT / _G.get("token_file", "secrets/gmail_token.json")

# send + read own mail (gmail.send alone can't poll threads for replies), and write
# events to our own calendar so a "yes" lands there. One consent screen for all three.
SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.events",
]


class GmailNotConfigured(RuntimeError):
    """No OAuth client JSON, or the user hasn't completed the consent flow yet."""


def configured() -> bool:
    return CREDENTIALS_FILE.exists()


def authorized() -> bool:
    return TOKEN_FILE.exists()


def status() -> dict:
    return {
        "configured": configured(),
        "authorized": authorized(),
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
    """Build the Gmail API client. Set interactive=True only from a terminal."""
    from googleapiclient.discovery import build

    creds = _load_credentials(interactive=interactive)
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def authorize() -> dict:
    """Run the one-time consent flow (opens a browser)."""
    svc = service(interactive=True)
    profile = svc.users().getProfile(userId="me").execute()
    return {"email": profile.get("emailAddress"), "messages_total": profile.get("messagesTotal")}


def my_address() -> str | None:
    try:
        return service().users().getProfile(userId="me").execute().get("emailAddress")
    except Exception as e:  # noqa: BLE001 — surfaced in the dashboard, not fatal
        log.debug("could not read Gmail profile: %s", e)
        return None
