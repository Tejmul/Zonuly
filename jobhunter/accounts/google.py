"""Sign in with Google — the OAuth 2.0 authorisation-code flow, done with httpx.

Not google-auth: the deployed image leaves Google's client libraries out for size
(scripts/build_catalyst.py), and three HTTPS calls do not need them.

The ID token is checked by Google's own tokeninfo endpoint rather than by verifying the
JWT signature here; at a few logins a day that costs nothing and removes key rotation
from our problems. What is checked, and why each matters:

  * `aud` is our client ID — a token minted for some other app is refused;
  * `iss` is Google;
  * `email_verified` is true — this is the "real person" proof for this route: Google has
    confirmed the person controls the address.

Redirect URI: {ZONULY_PUBLIC_URL}/zapi/api/auth/google/callback, which must be listed
exactly under "Authorised redirect URIs" in the Google Cloud console.
"""

from __future__ import annotations

from urllib.parse import urlencode

import httpx

from jobhunter import secret

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"
_ISSUERS = {"accounts.google.com", "https://accounts.google.com"}


class GoogleError(RuntimeError):
    """Google refused, or what came back does not prove who this is."""


def client_id() -> str:
    return secret("ZONULY_GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_ID")


def client_secret() -> str:
    return secret("ZONULY_GOOGLE_CLIENT_SECRET", "GOOGLE_CLIENT_SECRET")


def public_url() -> str:
    return (secret("ZONULY_PUBLIC_URL") or "http://localhost:3001").rstrip("/")


def callback_path() -> str:
    """Where the API is reached from the browser: through the dashboard's /zapi proxy when
    deployed, directly on a laptop."""
    return "/zapi/api/auth/google/callback" if secret("ZONULY_PUBLIC_URL") else "/api/auth/google/callback"


def redirect_uri() -> str:
    if secret("ZONULY_PUBLIC_URL"):
        return public_url() + callback_path()
    return "http://127.0.0.1:8000/api/auth/google/callback"


def configured() -> bool:
    return bool(client_id() and client_secret())


def authorise_url(state: str) -> str:
    return AUTH_URL + "?" + urlencode({
        "client_id": client_id(),
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "prompt": "select_account",
    })


def identify(code: str) -> dict:
    """Exchange the code; return {email, name, sub} for a verified Google account."""
    try:
        r = httpx.post(TOKEN_URL, data={
            "code": code,
            "client_id": client_id(),
            "client_secret": client_secret(),
            "redirect_uri": redirect_uri(),
            "grant_type": "authorization_code",
        }, timeout=15)
    except httpx.HTTPError as e:
        raise GoogleError(f"could not reach Google: {e}") from e
    if r.status_code != 200:
        raise GoogleError(f"Google refused the sign-in code (HTTP {r.status_code})")
    id_token = r.json().get("id_token")
    if not id_token:
        raise GoogleError("Google returned no identity")
    try:
        info = httpx.get(TOKENINFO_URL, params={"id_token": id_token}, timeout=15)
    except httpx.HTTPError as e:
        raise GoogleError(f"could not verify the identity with Google: {e}") from e
    if info.status_code != 200:
        raise GoogleError("Google did not vouch for this identity")
    claims = info.json()
    if claims.get("aud") != client_id():
        raise GoogleError("identity was issued for a different app")
    if claims.get("iss") not in _ISSUERS:
        raise GoogleError("identity was not issued by Google")
    if str(claims.get("email_verified")).lower() != "true":
        raise GoogleError("Google has not verified this email address")
    email = (claims.get("email") or "").strip().lower()
    if not email:
        raise GoogleError("Google returned no email address")
    return {"email": email, "name": claims.get("name") or None, "sub": claims.get("sub")}
