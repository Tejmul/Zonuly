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


# ---------------------------------------------------------------- setup flow (Settings → Email)

def _client_json() -> dict | None:
    """The OAuth client file as parsed JSON, or None when missing or unreadable."""
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
        # A token minted by the old client is useless with a new one.
        TOKEN_FILE.unlink()
    CREDENTIALS_FILE.parent.mkdir(parents=True, exist_ok=True)
    CREDENTIALS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    CREDENTIALS_FILE.chmod(0o600)
    log.info("installed Gmail OAuth client at %s", CREDENTIALS_FILE)
    return setup_status()


def disconnect(*, remove_client: bool = False) -> dict:
    """Forget the authorization (and optionally the client) so a different account can be used."""
    if TOKEN_FILE.exists():
        TOKEN_FILE.unlink()
    if remove_client and CREDENTIALS_FILE.exists():
        CREDENTIALS_FILE.unlink()
    return setup_status()


def setup_status() -> dict:
    """Everything the Settings → Email panel shows: each step, what is done, what is next."""
    from jobhunter.outreach import sender

    outreach = CONFIG.get("outreach") or {}
    client = _client_json()
    client_problem = validate_client_json(client) if client is not None else None
    client_ok = client is not None and client_problem is None
    client_id = (client or {}).get("installed", {}).get("client_id", "") if client_ok else ""
    token_present = TOKEN_FILE.exists()
    address = my_address() if token_present else None

    steps = [
        {"id": "project", "done": client_ok, "label": "Google Cloud project with the Gmail and Calendar APIs enabled"},
        {"id": "consent", "done": client_ok, "label": "Consent screen, with the sending address added as a test user"},
        {"id": "client", "done": client_ok, "label": "Desktop-app OAuth client JSON installed",
         "problem": client_problem},
        {"id": "authorize", "done": bool(address), "label": "Signed in and consent granted"},
        {"id": "mode", "done": sender.SEND_MODE == "gmail", "label": "Send mode switched from dry-run to Gmail"},
    ]
    next_step = next((s["id"] for s in steps if not s["done"]), None)
    return {
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
        "reply_to": outreach.get("user_email"),
        "send_mode": sender.SEND_MODE,
        "calendar_enabled": bool((CONFIG.get("calendar") or {}).get("enabled")),
        "steps": steps,
        "next_step": next_step,
        "quota": sender.quota(),
    }
