"""Signed tokens: the session cookie, the email-code challenge and the Google OAuth state.

All three are the same shape — a JSON payload and an HMAC over it — and are told apart by
a *purpose* that is part of what is signed. A challenge can therefore never be replayed as
a session, nor an OAuth state as either: the signature would not match.

Why stateless. The deployed backend (Catalyst AppSail) sleeps a minute after its last
request and forgets everything in memory, and someone reading a code out of their inbox
takes longer than a minute. So nothing about a half-finished login is kept on the server:
the browser carries the signed challenge and hands it back with the code.

What makes that safe for a 6-digit code is that the challenge holds an HMAC of the code,
not a hash of it. A plain hash of a million possible codes is broken offline in a blink by
anyone who reads the token; an HMAC cannot be checked without ZONULY_SESSION_SECRET, so the
only way to test a guess is to ask the server, which counts the asks (routes.py).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

from jobhunter import secret

#: how long a login lasts
SESSION_TTL = 30 * 24 * 3600
#: how long an emailed code is good for
CHALLENGE_TTL = 10 * 60
#: how long someone has to come back from Google
OAUTH_TTL = 10 * 60

_LOCAL_KEY = secrets.token_bytes(32)


def configured() -> bool:
    """True when a real signing secret is set. A public instance must not sign with a
    throwaway key: every login would end at the next restart, and AppSail restarts often."""
    return len(secret("ZONULY_SESSION_SECRET")) >= 32


def _key() -> bytes:
    raw = secret("ZONULY_SESSION_SECRET")
    # On a laptop, a per-process key: sessions end when the API restarts, which is fine
    # there and means nobody has to set a secret to try the login locally.
    return raw.encode() if raw else _LOCAL_KEY


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _mac(purpose: str, body: str) -> str:
    return _b64(hmac.new(_key(), f"{purpose}.{body}".encode(), hashlib.sha256).digest())


def sign(purpose: str, data: dict, ttl: int) -> str:
    body = _b64(json.dumps({**data, "exp": int(time.time()) + ttl}, separators=(",", ":")).encode())
    return f"{body}.{_mac(purpose, body)}"


def verify(purpose: str, token: str | None) -> dict | None:
    """The payload, or None when the token is malformed, forged, for another purpose, or old."""
    if not token or token.count(".") != 1:
        return None
    body, mac = token.split(".")
    if not hmac.compare_digest(mac, _mac(purpose, body)):
        return None
    try:
        data = json.loads(_unb64(body))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or int(data.get("exp", 0)) < time.time():
        return None
    return data


def code_mac(email: str, nonce: str, code: str) -> str:
    """What a challenge carries in place of its code (see the module docstring)."""
    return _mac("code", f"{email}|{nonce}|{code}")


def new_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def new_nonce() -> str:
    return secrets.token_urlsafe(12)


# ------------------------------------------------------------------- sessions

def session_for(user: dict) -> str:
    return sign("session", {"uid": str(user["id"]), "email": user["email"]}, SESSION_TTL)


def read_session(token: str | None) -> dict | None:
    return verify("session", token)


def internal_session() -> str:
    """A session for the API calling itself (the start-up prewarm and the post-deploy
    smoke test), so those requests clear the login gate like a signed-in visitor would —
    and are cached like one — without anyone holding the operator key."""
    return sign("session", {"uid": "internal", "email": "internal@zonuly"}, 3600)
