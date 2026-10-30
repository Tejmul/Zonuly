"""Check an address before a cold mail goes to it — bounces are what get an inbox flagged.

2026-10-30: of the last 30 days' sends, scraped and pattern-guessed addresses bounced or were
refused 17 times; verified ones once. A fresh domain inbox that bounces 10% is in spam within
a week, so a guessed address is checked first:

1. free: the domain must have a mail server (MX, or an A record as the RFC fallback);
2. paid, when MILLIONVERIFIER_API_KEY is in .env: the mailbox itself (ok / catch_all /
   unknown / invalid / disposable), ~$0.004 an address, once per address.

The answer is kept on the contact (email_status "check:<result>") so an address is never paid
for twice. state() says what the sender may do: "verified" (any inbox takes it), "guessed"
(Gmail only, under the guessed cap and the bounce brake), or "dead" (nobody sends).
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache

import httpx

from jobhunter import secret

log = logging.getLogger(__name__)

KEY_ENV = "MILLIONVERIFIER_API_KEY"
_URL = "https://api.millionverifier.com/api/v3/"
PREFIX = "check:"


def configured() -> bool:
    return bool(secret(KEY_ENV) or os.environ.get(KEY_ENV))


@lru_cache(maxsize=4096)
def has_mail_server(domain: str) -> bool | None:
    """True / False, or None when DNS could not answer (not a verdict)."""
    import dns.exception
    import dns.resolver

    domain = (domain or "").strip().lower().rstrip(".")
    if not domain:
        return False
    try:
        if dns.resolver.resolve(domain, "MX", lifetime=8):
            return True
    except (dns.resolver.NXDOMAIN, dns.resolver.NoNameservers):
        return False
    except dns.resolver.NoAnswer:
        pass
    except dns.exception.DNSException:
        return None
    try:
        return bool(dns.resolver.resolve(domain, "A", lifetime=8))
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.resolver.NoNameservers):
        return False
    except dns.exception.DNSException:
        return None


def millionverifier(address: str) -> str | None:
    """ok | catch_all | unknown | invalid | disposable, or None on an error (not a verdict)."""
    key = secret(KEY_ENV) or os.environ.get(KEY_ENV)
    if not key:
        return None
    try:
        r = httpx.get(_URL, params={"api": key, "email": address, "timeout": 10}, timeout=25)
        data = r.json()
    except (httpx.HTTPError, ValueError) as e:
        log.warning("address check failed for %s: %s", address, e)
        return None
    if data.get("error"):
        log.warning("address check refused: %s", str(data.get("error"))[:200])
        return None
    return str(data.get("result") or "").lower() or None


_DEAD = {"invalid", "disposable", "no_mx"}


def check(contact, address: str) -> str | None:
    """The stored or fresh verdict for `address`: ok / catch_all / unknown / invalid /
    disposable / no_mx, or None when nothing could be learned. Writes it on the contact."""
    stored = getattr(contact, "email_status", None) or ""
    if stored.startswith(PREFIX):
        return stored[len(PREFIX):]
    domain = address.rsplit("@", 1)[-1] if "@" in address else ""
    mx = has_mail_server(domain)
    verdict = "no_mx" if mx is False else None
    if verdict is None and mx and configured():
        verdict = millionverifier(address)
    if verdict and contact is not None and stored in ("", "verified", "unverified", "guessed", "unknown", "risky"):
        contact.email_status = PREFIX + verdict
    return verdict


def state(contact, address: str, confidence: str | None) -> str:
    """verified | guessed | dead for a cold mail to `address`."""
    if (confidence or "verified") == "verified":
        return "verified"
    verdict = check(contact, address)
    if verdict in _DEAD:
        return "dead"
    return "verified" if verdict == "ok" else "guessed"


__all__ = ["check", "state", "configured", "has_mail_server", "millionverifier", "KEY_ENV"]
