"""n8n webhook client — the bridge to the email automation that lives in n8n Cloud.

Zonuly never sends campaign mail itself; it tells n8n a campaign has started and
n8n does the rest. The webhook URL is a capability (anyone holding it can fire the
workflow), so it comes from the environment like every other credential, never
from config.yaml and never into a log line.
"""

from __future__ import annotations

import logging
import time
from urllib.parse import urlsplit

import httpx

from jobhunter import CONFIG, secret

log = logging.getLogger(__name__)

_N8N = CONFIG.get("n8n") or {}
URL_ENV = "N8N_CAMPAIGN_WEBHOOK_URL"
TIMEOUT = float(_N8N.get("timeout_seconds", 15))


class N8nError(RuntimeError):
    """n8n could not be reached, did not answer in time, or refused the call.

    `status` is the HTTP status n8n returned, or None when no response came back
    at all (DNS, connection, timeout).
    """

    def __init__(self, message: str, *, status: int | None = None):
        super().__init__(message)
        self.status = status


def webhook_url() -> str:
    return secret(URL_ENV)


def configured() -> bool:
    return bool(webhook_url())


def _host() -> str:
    """The safe part of the URL for logs and status: host only, no path."""
    return urlsplit(webhook_url()).netloc if configured() else ""


def status() -> dict:
    """Reported by /api/health and /api/campaigns/status so the dashboard can say so."""
    return {
        "configured": configured(),
        "host": _host(),
        "timeout_seconds": TIMEOUT,
        "hint": None if configured() else f"Set {URL_ENV} in .env to the workflow's webhook URL.",
    }


def trigger_campaign(campaign_id: str, campaign_name: str | None = None, *,
                     action: str = "start", extra: dict | None = None) -> dict:
    """POST the campaign event to n8n and return what it answered.

    Returns {"status": <http status>, "body": <parsed JSON or text>, "elapsed_ms": n}.
    Raises N8nError for anything short of a 2xx, so the route can turn it into one
    clean API error instead of leaking httpx exceptions to the dashboard.
    """
    if not configured():
        raise N8nError(f"n8n is not configured: set {URL_ENV} in .env")

    payload = {"campaign_id": campaign_id, "campaign_name": campaign_name, "action": action}
    if extra:
        # the caller's fields never override the three n8n relies on
        payload = {**extra, **payload}

    host = _host()
    started = time.monotonic()
    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            resp = client.post(webhook_url(), json=payload)
    except httpx.TimeoutException as e:
        log.error("n8n %s: campaign %s %s timed out after %.0fs", host, campaign_id, action, TIMEOUT)
        raise N8nError(f"n8n did not answer within {TIMEOUT:.0f}s") from e
    except httpx.HTTPError as e:  # connection refused, DNS, TLS — no response at all
        log.error("n8n %s: campaign %s %s unreachable: %s", host, campaign_id, action, e)
        raise N8nError(f"could not reach n8n: {e.__class__.__name__}") from e

    elapsed_ms = int((time.monotonic() - started) * 1000)
    try:
        body = resp.json()
    except ValueError:
        body = resp.text[:500]

    if not resp.is_success:
        log.error("n8n %s: campaign %s %s refused with HTTP %s in %dms: %s",
                  host, campaign_id, action, resp.status_code, elapsed_ms, str(body)[:200])
        raise N8nError(f"n8n answered HTTP {resp.status_code}", status=resp.status_code)

    log.info("n8n %s: campaign %s %s accepted (HTTP %s, %dms)",
             host, campaign_id, action, resp.status_code, elapsed_ms)
    return {"status": resp.status_code, "body": body, "elapsed_ms": elapsed_ms}
