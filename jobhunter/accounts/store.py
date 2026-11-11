"""Where accounts live.

Two places, one interface (`upsert_login`):

  * **CatalystStore** on the deployed instance — the Catalyst Data Store table
    `ZonulyUsers`. It has to be: the deployed database is a read-only snapshot replaced on
    every push, so an account written there would vanish at the next deploy.
  * **SqliteStore** everywhere else — `data/accounts.db`, a file of its own. Not
    jobhunter.db, because scripts/make_demo_db.py copies jobhunter.db into the public
    snapshot, and a list of the people who signed up is not something to publish.

Only logging in touches the store. Every other request is answered from the signed session
cookie alone (tokens.py), so a slow or sleeping Data Store never slows down browsing.

Why not the zcatalyst-sdk package: it pins typing-extensions below 4.13, and this project
needs 4.16 for pydantic, so it cannot be installed alongside. The handful of calls used
here are made the way the SDK makes them in admin scope (zcatalyst_sdk 1.5.0,
`initialize(scope='admin')`): the Catalyst gateway puts a short-lived admin credential on
every request it forwards to AppSail, and that is the credential used. No Zoho secret is
stored anywhere.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

import httpx

from jobhunter import ROOT

log = logging.getLogger(__name__)

TABLE = os.environ.get("ZONULY_USERS_TABLE", "ZonulyUsers")


class StoreError(RuntimeError):
    """The account store could not be reached or refused the write."""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _public_user(row: dict) -> dict:
    return {
        "id": str(row.get("id") or row.get("ROWID")),
        "email": row["email"],
        "name": row.get("name") or None,
        "provider": row.get("provider") or None,
    }


# ------------------------------------------------------------------- SQLite

class SqliteStore:
    name = "sqlite"

    def __init__(self, path: Path | None = None):
        self.path = Path(path or os.environ.get("ZONULY_ACCOUNTS_DB") or ROOT / "data" / "accounts.db")
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as con:
            con.execute(
                "create table if not exists users ("
                " id integer primary key autoincrement,"
                " email text not null unique,"
                " name text, provider text, google_sub text,"
                " created_at text not null, last_login_at text, login_count integer not null default 0)"
            )

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=10)
        con.row_factory = sqlite3.Row
        return con

    def upsert_login(self, email: str, *, provider: str, name: str | None = None,
                     google_sub: str | None = None, request=None) -> dict:
        now = _now()
        with self._lock, self._connect() as con:
            row = con.execute("select * from users where email = ?", (email,)).fetchone()
            if row is None:
                con.execute(
                    "insert into users (email, name, provider, google_sub, created_at, last_login_at, login_count)"
                    " values (?, ?, ?, ?, ?, ?, 1)",
                    (email, name, provider, google_sub, now, now),
                )
            else:
                con.execute(
                    "update users set last_login_at = ?, login_count = login_count + 1,"
                    " name = coalesce(name, ?), google_sub = coalesce(google_sub, ?) where email = ?",
                    (now, name, google_sub, email),
                )
            row = con.execute("select * from users where email = ?", (email,)).fetchone()
        return _public_user(dict(row))

    def count(self, request=None) -> int:
        with self._connect() as con:
            return con.execute("select count(*) from users").fetchone()[0]


# ------------------------------------------------------------------- Catalyst

def _header(headers, name: str) -> str:
    """Catalyst's gateway headers, read case-insensitively as the SDK does."""
    value = headers.get(name)
    if value is None:
        low = name.lower()
        value = next((v for k, v in headers.items() if k.lower() == low), None)
    return value or ""


class CatalystStore:
    name = "catalyst"

    def __init__(self):
        # In AppSail this is set by the runtime; the default is this project's data centre.
        self.base = os.environ.get("X_ZOHO_CATALYST_CONSOLE_URL", "https://api.catalyst.zoho.in").rstrip("/")

    # The request the SDK would build — headers and path — for a Data Store call in
    # admin scope. Any request that reached us through the Catalyst gateway carries them.
    def _call(self, request, method: str, path: str, *, accept: str = "application/vnd.catalyst.v2+json",
              **kwargs) -> dict:
        if request is None:
            raise StoreError("the Data Store is reached with the credential on the incoming request; none given")
        h = request.headers
        project_id = _header(h, "X-ZC-ProjectId")
        token = _header(h, "X-ZC-Admin-Cred-Token")
        kind = _header(h, "X-ZC-Admin-Cred-Type")
        if not (project_id and token):
            raise StoreError("no Catalyst credential on this request — is it running on AppSail?")
        env = _header(h, "X-ZC-Environment") or "Development"
        headers = {
            "Authorization": ("Zoho-ticket " if kind == "ticket" else "Zoho-oauthtoken ") + token,
            "PROJECT_ID": _header(h, "X-ZC-Project-Key"),
            "X-Catalyst-Environment": env,
            "environment": env,
            "X-CATALYST-USER": "admin",
            "Accept": accept,
            "User-Agent": "zonuly-accounts/1.0",
        }
        if secret_key := _header(h, "X-ZC-PROJECT-SECRET-KEY"):
            headers["X-ZC-PROJECT-SECRET-KEY"] = secret_key
        if org := os.environ.get("X_ZOHO_CATALYST_ORG_ID"):
            headers["CATALYST-ORG"] = org
        url = f"{self.base}/baas/v1/project/{project_id}{path}"
        try:
            r = httpx.request(method, url, headers=headers, timeout=15, **kwargs)
        except httpx.HTTPError as e:
            raise StoreError(f"Data Store unreachable: {e}") from e
        if r.status_code >= 400:
            raise StoreError(f"Data Store {method} {path} -> HTTP {r.status_code}: {r.text[:300]}")
        try:
            return r.json()
        except ValueError as e:
            raise StoreError(f"Data Store answered non-JSON: {r.text[:200]}") from e

    def _find(self, request, email: str) -> dict | None:
        # `email` has already passed realness.well_formed, which admits no quote; the
        # escape is a second lock on the same door, not the only one.
        safe = email.replace("'", "''")
        out = self._call(request, "POST", "/query", accept="application/vnd.catalyst.v2+zcql",
                         json={"query": f"SELECT * FROM {TABLE} WHERE email = '{safe}'"})
        rows = out.get("data") or []
        return rows[0].get(TABLE) if rows else None

    def upsert_login(self, email: str, *, provider: str, name: str | None = None,
                     google_sub: str | None = None, request=None) -> dict:
        now = _now()
        row = self._find(request, email)
        if row is None:
            body = [{"email": email, "name": name or "", "provider": provider,
                     "google_sub": google_sub or "", "last_login_at": now, "login_count": 1}]
            out = self._call(request, "POST", f"/table/{TABLE}/row", json=body)
            created = (out.get("data") or [{}])[0]
            return _public_user({**body[0], **created})
        patch = {"ROWID": row["ROWID"], "last_login_at": now,
                 "login_count": int(row.get("login_count") or 0) + 1}
        if name and not row.get("name"):
            patch["name"] = name
        if google_sub and not row.get("google_sub"):
            patch["google_sub"] = google_sub
        self._call(request, "PATCH", f"/table/{TABLE}/row", json=[patch])
        return _public_user({**row, **patch})

    def count(self, request=None) -> int:
        out = self._call(request, "POST", "/query", accept="application/vnd.catalyst.v2+zcql",
                         json={"query": f"SELECT COUNT(ROWID) FROM {TABLE}"})
        rows = out.get("data") or []
        if not rows:
            return 0
        cell = rows[0].get(TABLE) or {}
        return int(next(iter(cell.values()), 0) or 0)


# ------------------------------------------------------------------- choice

_store = None
_store_lock = threading.Lock()


def get_store():
    """ZONULY_ACCOUNTS=catalyst on AppSail (set by its start script); SQLite otherwise."""
    global _store
    with _store_lock:
        if _store is None:
            kind = os.environ.get("ZONULY_ACCOUNTS", "sqlite").strip().lower()
            _store = CatalystStore() if kind == "catalyst" else SqliteStore()
            log.info("accounts stored in %s", _store.name)
        return _store


def set_store(store) -> None:
    """For tests."""
    global _store
    with _store_lock:
        _store = store
