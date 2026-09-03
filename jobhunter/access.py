"""Who may do what once the API is on the open internet.

On your own machine there is one user and nothing to decide, so with `ZONULY_PUBLIC`
unset every function here is a no-op and the API behaves exactly as it always has.

Setting `ZONULY_PUBLIC=1` — which is what the Railway deployment does — turns the same
process into a read-only exhibit:

  * every write is refused, because the write endpoints on this API send mail from your
    Gmail account and spend your OpenRouter and Exa credit, and there is no login in
    front of them. CORS does not help: it constrains browsers, and the thing you are
    defending against is `curl`;
  * the endpoints carrying private correspondence or configuration are closed outright;
  * the contact rows that remain keep their name, role, company and evidence — the whole
    point of the demo — but their email address is masked, so a visitor can see that a
    real address was found without being handed 3,700 of them.

`ZONULY_API_KEY`, presented as an `X-API-Key` header, lifts all of it for the holder, so
you can still drive your own instance from anywhere.

This is deliberately one module and one middleware rather than a dependency on each
route: a route added next month is covered without anyone remembering to cover it.
"""

from __future__ import annotations

import hmac
import os
from contextvars import ContextVar

from jobhunter import secret

# Read once at import. These are deployment facts, not runtime settings — a public
# instance never becomes private while it is running.
PUBLIC = os.environ.get("ZONULY_PUBLIC", "").strip().lower() in {"1", "true", "yes", "on"}
_KEY = secret("ZONULY_API_KEY")

#: Methods a visitor may use. Everything else mutates something.
_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

#: Always reachable, whatever the mode — Railway health-checks this.
_ALWAYS = ("/api/health",)

#: Readable, but not by the public. Private correspondence, configuration, spend, and
#: the task registry (which reports on operator actions).
_OPERATOR_ONLY = (
    "/api/replies",   # real inbound mail from real people
    "/api/config",    # the whole config file, thresholds and caps included
    "/api/profile",   # the operator's own resume
    "/api/tasks",     # what the operator is running right now
    "/api/models",    # model choice and, at /costs, what has been spent
    "/api/gmail",     # mailbox status and the OAuth trigger
    "/api/research",  # live outbound research; every call costs money
)

#: Set by the middleware for the duration of one request. A serialiser deep inside a
#: route has no access to the request object, but it does inherit the context — and
#: Starlette carries the context into the threadpool that runs sync handlers, so this
#: is visible from ordinary `def` routes too.
_operator: ContextVar[bool] = ContextVar("zonuly_operator", default=False)


def set_operator(value: bool):
    """Returns the token to reset with, so one request cannot leak into the next."""
    return _operator.set(value)


def reset_operator(token) -> None:
    _operator.reset(token)


def _unrestricted() -> bool:
    return not PUBLIC or _operator.get()


def is_operator(headers) -> bool:
    """True when the caller presented the operator key.

    Compared with `compare_digest` so the check cannot be turned into an oracle by
    timing it. With no key configured nobody is an operator, which is the safe
    reading of a missing setting rather than the convenient one.
    """
    if not _KEY:
        return False
    offered = headers.get("x-api-key") or ""
    return bool(offered) and hmac.compare_digest(offered, _KEY)


def refusal(method: str, path: str, headers) -> tuple[int, str] | None:
    """`(status, detail)` if this request must be refused, else None."""
    if not PUBLIC or is_operator(headers):
        return None
    if path in _ALWAYS:
        return None
    if method not in _READ_METHODS:
        return (
            403,
            "This instance is a read-only demonstration. Sending mail, drafting and "
            "running the pipeline are disabled.",
        )
    if path.startswith(_OPERATOR_ONLY):
        return 403, "Not available on the public instance."
    return None


def mask_email(address: str | None) -> str | None:
    """`paul@example.com` -> `p•••@example.com`.

    The domain survives because it is the company's, which is public and is half the
    evidence — it shows the address was derived from where the person works. The local
    part does not, because that is the half that makes the row mailable.
    """
    if not address or "@" not in address:
        return address
    local, _, domain = address.partition("@")
    return f"{local[:1]}•••@{domain}"


def redact_contact(row: dict) -> dict:
    """Mask a serialised contact. No-op locally, and no-op for the key holder."""
    if _unrestricted():
        return row
    row["email"] = mask_email(row.get("email"))
    return row


def redact_email(row: dict) -> dict:
    """Same for a drafted message: the draft is the demo, the recipient is not."""
    if _unrestricted():
        return row
    row["to_email"] = mask_email(row.get("to_email"))
    return row


def status() -> dict:
    """Reported by /api/health so the dashboard can say so on screen."""
    return {"public": PUBLIC, "read_only": PUBLIC, "operator_key_set": bool(_KEY)}
