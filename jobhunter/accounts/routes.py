"""/api/auth/* — sign up, sign in, sign out, and who am I.

Email: POST /email/start {email} sends a code and returns a signed challenge; POST
/email/verify {challenge, code} checks it, records the account and sets the session
cookie. Google: GET /google/start redirects to Google, GET /google/callback comes back,
records the account and sets the cookie. There is no separate "sign up" — the first
successful login creates the account, which is the whole of what signing up needs to be
when the only thing to prove is that you own the address.

The routes are plain `def`: the store and the mailer block, and FastAPI runs plain
functions in its threadpool rather than stalling every other request behind them.
"""

from __future__ import annotations

import logging
import secrets

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from jobhunter import access
from jobhunter.accounts import google, mail, realness, tokens
from jobhunter.accounts.store import StoreError, get_store

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["accounts"])

OAUTH_COOKIE = "zonuly_oauth"


def _ip(request: Request) -> str:
    real = request.headers.get("x-real-ip")
    if real:
        return real.strip()
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "?"


def _safe_next(raw: str | None) -> str:
    """Only a path on this site — never `//evil.com` or `https://…`, which would turn the
    login into a redirector anyone could send a link through."""
    path = (raw or "").strip()
    if not path.startswith("/") or path.startswith("//") or path.startswith("/\\"):
        return "/dashboard"
    return path


def _set_session(response: Response, user: dict) -> None:
    response.set_cookie(
        access.SESSION_COOKIE, tokens.session_for(user),
        max_age=tokens.SESSION_TTL, httponly=True, samesite="lax",
        secure=access.PUBLIC, path="/",
    )


def _needs_secret() -> None:
    if access.PUBLIC and not tokens.configured():
        raise HTTPException(503, "Sign-in is not set up on this instance yet.")


def _record(email: str, *, provider: str, request: Request, name: str | None = None,
            google_sub: str | None = None) -> dict:
    try:
        return get_store().upsert_login(email, provider=provider, name=name,
                                        google_sub=google_sub, request=request)
    except StoreError as e:
        log.error("accounts: could not record %s login: %s", provider, e)
        raise HTTPException(503, "We couldn't save your account just now. Please try again in a minute.") from e


# ------------------------------------------------------------------- who am I

@router.get("/me")
def me(request: Request):
    session = access.session_user(request.headers)
    user = None
    if session and session.get("uid") != "internal":
        user = {"id": session["uid"], "email": session["email"]}
    return {
        "auth_required": access.AUTH_REQUIRED,
        "user": user,
        "methods": {
            "email": mail.mode(request) != "none" and (tokens.configured() or not access.PUBLIC),
            "google": google.configured() and (tokens.configured() or not access.PUBLIC),
        },
        "turnstile_site_key": realness.turnstile_site_key() if realness.turnstile_on() else None,
    }


@router.post("/logout")
def logout():
    response = Response(status_code=204)
    response.delete_cookie(access.SESSION_COOKIE, path="/")
    return response


# ------------------------------------------------------------------- email code

class StartBody(BaseModel):
    email: str
    name: str | None = None
    turnstile: str | None = None


class VerifyBody(BaseModel):
    challenge: str
    code: str


@router.post("/email/start")
def email_start(body: StartBody, request: Request):
    _needs_secret()
    email = realness.normalise(body.email)
    problem = realness.problem_with(email)
    if problem:
        raise HTTPException(400, problem)
    ip = _ip(request)
    if not realness.human(body.turnstile, ip):
        raise HTTPException(400, "Please complete the 'I'm human' check and try again.")
    if not realness.codes_per_ip.allow(ip) or not realness.codes_per_address.allow(email):
        raise HTTPException(429, "Too many codes requested. Wait a few minutes and try again.")

    code, nonce = tokens.new_code(), tokens.new_nonce()
    name = (body.name or "").strip()[:80] or None
    challenge = tokens.sign("challenge", {
        "email": email, "nonce": nonce, "mac": tokens.code_mac(email, nonce, code), "name": name,
    }, tokens.CHALLENGE_TTL)
    try:
        how = mail.send_code(email, code, request)
    except mail.MailError as e:
        log.error("accounts: could not send code to %s: %s", email, e)
        raise HTTPException(503, "We couldn't send the code just now. Please try again in a minute.") from e
    out = {"challenge": challenge, "email": email, "expires_in": tokens.CHALLENGE_TTL}
    if how == "dev":  # mail.mode only answers "dev" off a public instance
        out["dev_code"] = code
    return out


@router.post("/email/verify")
def email_verify(body: VerifyBody, request: Request, response: Response):
    _needs_secret()
    data = tokens.verify("challenge", body.challenge)
    if data is None:
        raise HTTPException(400, "That code has expired. Ask for a new one.")
    ip = _ip(request)
    if not realness.guesses_per_challenge.allow(data["nonce"]) or not realness.guesses_per_ip.allow(ip):
        raise HTTPException(429, "Too many wrong codes. Ask for a new one.")
    code = "".join(ch for ch in body.code if ch.isdigit())
    if len(code) != 6 or not secrets.compare_digest(
        tokens.code_mac(data["email"], data["nonce"], code), data["mac"]
    ):
        raise HTTPException(400, "That code isn't right. Check the email and try again.")
    user = _record(data["email"], provider="email", request=request, name=data.get("name"))
    _set_session(response, user)
    return {"user": user}


# ------------------------------------------------------------------- Google

@router.get("/google/start")
def google_start(next: str | None = None):
    _needs_secret()
    if not google.configured():
        raise HTTPException(404, "Google sign-in is not set up on this instance.")
    nonce = tokens.new_nonce()
    state = tokens.sign("oauth", {"nonce": nonce, "next": _safe_next(next)}, tokens.OAUTH_TTL)
    response = RedirectResponse(google.authorise_url(state), status_code=302)
    # Binds the round trip to this browser: a state lifted from someone else's attempt
    # fails, because their nonce is not in this browser's cookie.
    response.set_cookie(OAUTH_COOKIE, nonce, max_age=tokens.OAUTH_TTL, httponly=True,
                        samesite="lax", secure=access.PUBLIC, path="/")
    return response


@router.get("/google/callback")
def google_callback(request: Request, code: str | None = None, state: str | None = None,
                    error: str | None = None):
    def back(problem: str) -> RedirectResponse:
        from urllib.parse import quote

        return RedirectResponse(f"{google.public_url()}/login?error={quote(problem)}", status_code=302)

    if error:
        return back("Google sign-in was cancelled.")
    data = tokens.verify("oauth", state)
    if data is None or not code:
        return back("That sign-in link expired. Please try again.")
    if not secrets.compare_digest(request.cookies.get(OAUTH_COOKIE) or "", data["nonce"]):
        return back("Please start the Google sign-in from this browser.")
    try:
        who = google.identify(code)
    except google.GoogleError as e:
        log.warning("accounts: google sign-in refused: %s", e)
        return back("Google couldn't confirm your account. Please try again.")
    try:
        user = get_store().upsert_login(who["email"], provider="google", name=who["name"],
                                        google_sub=who["sub"], request=request)
    except StoreError as e:
        log.error("accounts: could not record google login: %s", e)
        return back("We couldn't save your account just now. Please try again in a minute.")
    response = RedirectResponse(f"{google.public_url()}{data['next']}", status_code=302)
    _set_session(response, user)
    response.delete_cookie(OAUTH_COOKIE, path="/")
    return response
