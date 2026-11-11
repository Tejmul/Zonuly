"""Is this a real person with a real inbox? Everything short of the code itself.

A signup has to clear, in order:

  1. a well-formed address — no quotes, no spaces, nothing a query could trip on;
  2. not a throwaway mailbox (mailinator and friends), which proves nothing about anyone;
  3. a domain that can receive mail at all — it publishes an MX record, or failing that an
     address — so a typo'd or invented domain is turned away before a code is wasted on it;
  4. optionally, a Cloudflare Turnstile check, when ZONULY_TURNSTILE_SECRET is set: a human
     at a browser, not a script hammering the endpoint;
  5. rate limits, per address and per network address, so the code endpoint cannot be used
     to flood someone's inbox or to grind through codes.

The code then proves they own the inbox. Google sign-in skips 1–3: Google has already
verified the address, and says so in the token (google.py checks `email_verified`).
"""

from __future__ import annotations

import re
import threading
import time
from collections import defaultdict, deque
from functools import lru_cache

import httpx

from jobhunter import secret

# Deliberately narrower than the RFC: the address goes into a ZCQL query on the deployed
# instance, and an address with a quote in it is not one a real person signs up with.
_EMAIL = re.compile(
    r"^(?=.{3,254}$)[a-z0-9](?:[a-z0-9._%+\-]{0,62}[a-z0-9])?"
    r"@(?:[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}$"
)

#: Disposable-mailbox services. Not exhaustive — nothing is — but these are the ones a
#: bot or a tyre-kicker reaches for first; the MX check and the rate limits do the rest.
DISPOSABLE = frozenset("""
10minutemail.com 10minutemail.net 20minutemail.com 33mail.com anonbox.net burnermail.io
discard.email disposablemail.com dispostable.com dropmail.me emailondeck.com
emailfake.com fakeinbox.com fakemail.net getairmail.com getnada.com guerrillamail.biz
guerrillamail.com guerrillamail.de guerrillamail.info guerrillamail.net guerrillamail.org
guerrillamailblock.com harakirimail.com inboxbear.com inboxkitten.com jetable.org
mail-temp.com mail.tm mailcatch.com maildrop.cc mailinator.com mailinator.net
mailinator.org mailnesia.com mailpoof.com mailsac.com mailtemp.net mintemail.com
moakt.com mohmal.com mytemp.email mytrashmail.com nada.email noclickemail.com
sharklasers.com spam4.me spamgourmet.com spambox.us temp-mail.io temp-mail.org
tempail.com tempinbox.com tempmail.com tempmail.dev tempmail.net tempmail.plus
tempmailo.com tempr.email throwawaymail.com tmail.ws tmpmail.net tmpmail.org trash-mail.com
trashmail.com trashmail.de trashmail.net trbvm.com tutanota.dev wegwerfmail.de
yopmail.com yopmail.fr yopmail.net emltmp.com tmpeml.com minuteinbox.com
""".split())


def normalise(raw: str | None) -> str:
    return (raw or "").strip().lower()


def well_formed(email: str) -> bool:
    return bool(_EMAIL.match(email)) and ".." not in email


def disposable(email: str) -> bool:
    domain = email.rpartition("@")[2]
    # a subdomain of a throwaway service is still that service
    return any(domain == d or domain.endswith("." + d) for d in DISPOSABLE)


@lru_cache(maxsize=4096)
def receives_mail(domain: str) -> bool:
    """True if the domain can take mail. Only a definite "no such domain / no records"
    turns someone away; a DNS timeout lets them through, because blocking a real person
    on our resolver's bad day is worse than one extra code going out."""
    try:
        import dns.exception
        import dns.resolver
    except ImportError:  # pragma: no cover - dnspython is a dependency
        return True
    resolver = dns.resolver.Resolver()
    resolver.lifetime = 4.0
    for rtype in ("MX", "A", "AAAA"):
        try:
            if resolver.resolve(domain, rtype):
                return True
        except (dns.resolver.NoAnswer, dns.resolver.NoNameservers):
            continue
        except dns.resolver.NXDOMAIN:
            return False
        except dns.exception.DNSException:
            return True
    return False


def problem_with(email: str) -> str | None:
    """Why this address cannot sign up, in words to show the person — or None."""
    if not well_formed(email):
        return "That doesn't look like an email address."
    if disposable(email):
        return "Please use your own email address — temporary inboxes can't be used here."
    if not receives_mail(email.rpartition("@")[2]):
        return "That email domain can't receive mail. Check for a typo."
    return None


# ------------------------------------------------------------------- Turnstile

def turnstile_site_key() -> str:
    return secret("ZONULY_TURNSTILE_SITE_KEY")


def turnstile_on() -> bool:
    return bool(secret("ZONULY_TURNSTILE_SECRET") and turnstile_site_key())


def human(token: str | None, ip: str | None) -> bool:
    """Cloudflare Turnstile verdict. Always True when Turnstile is not configured."""
    if not turnstile_on():
        return True
    if not token:
        return False
    try:
        r = httpx.post(
            "https://challenges.cloudflare.com/turnstile/v0/siteverify",
            data={"secret": secret("ZONULY_TURNSTILE_SECRET"), "response": token,
                  **({"remoteip": ip} if ip else {})},
            timeout=10,
        )
        return bool(r.json().get("success"))
    except (httpx.HTTPError, ValueError):
        return False


# ------------------------------------------------------------------- rate limits

class Window:
    """At most `limit` events per `seconds` for each key. In memory: on a sleeping
    instance it forgets — but an attacker who keeps hammering keeps it awake, which is
    exactly when it needs to remember."""

    def __init__(self, limit: int, seconds: float):
        self.limit, self.seconds = limit, seconds
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > self.seconds:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            return True

    def clear(self) -> None:
        with self._lock:
            self._hits.clear()


#: codes sent to one address — three in a quarter hour is a person retrying, more is not
codes_per_address = Window(3, 15 * 60)
#: codes requested from one network address in an hour
codes_per_ip = Window(10, 3600)
#: guesses against one challenge
guesses_per_challenge = Window(5, 15 * 60)
#: guesses from one network address — stops spreading guesses across many challenges
guesses_per_ip = Window(30, 3600)


def reset_limits() -> None:
    for w in (codes_per_address, codes_per_ip, guesses_per_challenge, guesses_per_ip):
        w.clear()
