"""Accounts: the login gate, the email code, Google's round trip, and the Data Store calls.

Most of these are attacks written down: a forged cookie, a challenge replayed as a session,
a code ground through, a redirect pointed off-site, a cached page handed to a stranger.
Each one is a way the gate could be open while looking shut.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from jobhunter import access, snapshot_cache
from jobhunter.accounts import mail, realness, routes, store, tokens


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    realness.reset_limits()
    realness.receives_mail.cache_clear()
    monkeypatch.setattr(realness, "receives_mail", lambda domain: domain != "nowhere.invalid")
    monkeypatch.setenv("ZONULY_SESSION_SECRET", "s" * 40)
    for name in ("ZONULY_SMTP_HOST", "ZONULY_TURNSTILE_SECRET", "ZONULY_TURNSTILE_SITE_KEY",
                 "ZONULY_GOOGLE_CLIENT_ID", "ZONULY_GOOGLE_CLIENT_SECRET", "GOOGLE_CLIENT_ID",
                 "GOOGLE_CLIENT_SECRET", "ZONULY_PUBLIC_URL"):
        monkeypatch.delenv(name, raising=False)
    store.set_store(store.SqliteStore(tmp_path / "accounts.db"))
    yield
    store.set_store(None)


def _client():
    from jobhunter.api import app

    return TestClient(app)


def _cookie(token: str) -> dict:
    return {"cookie": f"{access.SESSION_COOKIE}={token}"}


# ------------------------------------------------------------------- tokens

def test_a_token_is_bound_to_its_purpose_and_cannot_be_altered_or_outlive_itself():
    t = tokens.sign("session", {"uid": "1", "email": "a@b.co"}, 60)
    assert tokens.verify("session", t)["uid"] == "1"
    assert tokens.verify("challenge", t) is None          # a session is not a challenge
    body, mac = t.split(".")
    assert tokens.verify("session", body + "x." + mac) is None
    assert tokens.verify("session", tokens.sign("session", {"uid": "1"}, -1)) is None
    assert tokens.verify("session", "garbage") is None and tokens.verify("session", None) is None


def test_the_challenge_carries_a_keyed_mac_so_the_code_cannot_be_recovered_offline(monkeypatch):
    mac = tokens.code_mac("a@b.co", "n", "123456")
    assert "123456" not in mac
    monkeypatch.setenv("ZONULY_SESSION_SECRET", "t" * 40)
    assert tokens.code_mac("a@b.co", "n", "123456") != mac   # needs the server's secret


# ------------------------------------------------------------------- real person

@pytest.mark.parametrize("email, ok", [
    ("tejmul@tejmul.in", True),
    ("a.b+jobs@gmail.com", True),
    ("x@y.co", True),
    ("no-at-sign.com", False),
    ("a@b", False),
    ("a..b@gmail.com", False),
    ("o'brien@gmail.com", False),       # a quote never reaches a ZCQL query
    ("a@b.com' OR '1'='1", False),
    (".lead@gmail.com", False),
])
def test_only_plain_addresses_are_well_formed(email, ok):
    assert realness.well_formed(email) is ok


def test_throwaway_mailboxes_and_dead_domains_are_turned_away():
    assert realness.problem_with("bot@mailinator.com")
    assert realness.problem_with("bot@sub.yopmail.com")
    assert realness.problem_with("x@nowhere.invalid")
    assert realness.problem_with("tejmul@gmail.com") is None


def test_a_rate_window_forgets_only_when_time_has_passed():
    w = realness.Window(2, 60)
    assert w.allow("k") and w.allow("k") and not w.allow("k")
    assert w.allow("other")


# ------------------------------------------------------------------- the gate

@pytest.fixture
def gated(monkeypatch):
    monkeypatch.setattr(access, "PUBLIC", True)
    monkeypatch.setattr(access, "AUTH_REQUIRED", True)
    monkeypatch.setattr(access, "_KEY", "op-key")


def test_behind_the_gate_data_needs_a_session_and_the_landing_counters_do_not(gated):
    assert access.refusal("GET", "/api/companies", {}) == (401, "Sign in to see this.")
    assert access.refusal("GET", "/api/leads", {}) == (401, "Sign in to see this.")
    assert access.refusal("GET", "/api/overview", {}) is None      # the landing page
    assert access.refusal("GET", "/api/health", {}) is None
    assert access.refusal("GET", "/api/auth/me", {}) is None


def test_a_signed_in_visitor_reads_but_still_cannot_write_or_read_private(gated):
    me = _cookie(tokens.session_for({"id": 7, "email": "a@b.co"}))
    assert access.refusal("GET", "/api/companies", me) is None
    assert access.refusal("POST", "/api/outreach/send", me)[0] == 403
    assert access.refusal("GET", "/api/replies", me)[0] == 403


def test_signing_in_is_a_post_that_gets_past_the_read_only_rule(gated):
    assert access.refusal("POST", "/api/auth/email/start", {}) is None
    assert access.refusal("POST", "/api/auth/email/verify", {}) is None


def test_forged_or_replayed_cookies_do_not_open_the_gate(gated):
    challenge = tokens.sign("challenge", {"email": "a@b.co", "nonce": "n", "mac": "m"}, 60)
    oauth = tokens.sign("oauth", {"nonce": "n", "next": "/"}, 60)
    for bad in ("nonsense", challenge, oauth, tokens.session_for({"id": 1, "email": "a@b.co"}) + "x"):
        assert access.refusal("GET", "/api/companies", _cookie(bad))[0] == 401


def test_the_operator_key_still_lifts_everything(gated):
    assert access.refusal("GET", "/api/companies", {"x-api-key": "op-key"}) is None


def test_with_the_gate_down_nothing_changes(monkeypatch):
    monkeypatch.setattr(access, "PUBLIC", True)
    monkeypatch.setattr(access, "AUTH_REQUIRED", False)
    assert access.refusal("GET", "/api/companies", {}) is None


def test_gated_answers_are_never_marked_cacheable_by_the_cdn(gated):
    assert snapshot_cache.cache_control_for("/api/companies") == snapshot_cache.PRIVATE_CACHE_CONTROL
    assert snapshot_cache.cache_control_for("/api/overview") == snapshot_cache.CACHE_CONTROL


def test_the_gate_answers_401_over_http_without_touching_a_route(gated):
    r = _client().get("/api/lists")
    assert r.status_code == 401 and r.json()["detail"] == "Sign in to see this."


# ------------------------------------------------------------------- email code, end to end

def _start(client, email="tejmul@gmail.com", **extra):
    return client.post("/api/auth/email/start", json={"email": email, **extra})


def test_email_code_signs_in_creates_the_account_once_and_sets_a_session():
    client = _client()
    r = _start(client, name="Tejmul")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dev_code"] and len(body["dev_code"]) == 6        # laptop only
    v = client.post("/api/auth/email/verify", json={"challenge": body["challenge"], "code": body["dev_code"]})
    assert v.status_code == 200, v.text
    assert v.json()["user"]["email"] == "tejmul@gmail.com"
    assert v.json()["user"]["name"] == "Tejmul"
    assert access.SESSION_COOKIE in v.cookies
    session = tokens.read_session(v.cookies[access.SESSION_COOKIE])
    assert session["email"] == "tejmul@gmail.com"

    # second login: same account, counted
    again = _start(client).json()
    client.post("/api/auth/email/verify", json={"challenge": again["challenge"], "code": again["dev_code"]})
    assert store.get_store().count() == 1


def test_a_wrong_code_is_refused_and_guessing_stops_after_five():
    client = _client()
    body = _start(client).json()
    wrong = "000000" if body["dev_code"] != "000000" else "111111"
    for _ in range(5):
        assert client.post("/api/auth/email/verify",
                           json={"challenge": body["challenge"], "code": wrong}).status_code == 400
    r = client.post("/api/auth/email/verify", json={"challenge": body["challenge"], "code": body["dev_code"]})
    assert r.status_code == 429          # even the right code, once the challenge is burnt


def test_one_inbox_cannot_be_flooded_with_codes():
    client = _client()
    assert all(_start(client).status_code == 200 for _ in range(3))
    assert _start(client).status_code == 429


def test_throwaway_addresses_never_get_a_code():
    r = _start(_client(), email="bot@mailinator.com")
    assert r.status_code == 400 and "temporary" in r.json()["detail"]


def test_a_public_instance_never_hands_back_the_code(monkeypatch):
    monkeypatch.setattr(access, "PUBLIC", True)
    monkeypatch.setattr(access, "AUTH_REQUIRED", True)
    # no SMTP, no Catalyst headers: nowhere to send, so refuse — never fall back to "dev"
    assert mail.mode() == "none"
    r = _start(_client())
    assert r.status_code == 503 and "dev_code" not in r.json()


def test_a_public_instance_without_a_real_secret_refuses_to_sign_anyone_in(monkeypatch):
    monkeypatch.setattr(access, "PUBLIC", True)
    monkeypatch.setenv("ZONULY_SESSION_SECRET", "short")
    assert _start(_client()).status_code == 503


def test_turnstile_when_configured_is_required(monkeypatch):
    monkeypatch.setenv("ZONULY_TURNSTILE_SECRET", "sec")
    monkeypatch.setenv("ZONULY_TURNSTILE_SITE_KEY", "site")
    assert _start(_client()).status_code == 400          # no token
    monkeypatch.setattr(realness, "human", lambda token, ip: token == "ok")
    assert _start(_client(), turnstile="ok").status_code == 200


# ------------------------------------------------------------------- Google

def test_next_can_only_point_at_this_site():
    assert routes._safe_next("/companies/5") == "/companies/5"
    for bad in ("//evil.com", "https://evil.com", "/\\evil.com", "", None, "evil"):
        assert routes._safe_next(bad) == "/dashboard"


def test_google_callback_refuses_a_state_from_another_browser(monkeypatch):
    monkeypatch.setenv("ZONULY_GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setenv("ZONULY_GOOGLE_CLIENT_SECRET", "csec")
    called = []
    monkeypatch.setattr(routes.google, "identify", lambda code: called.append(code))
    state = tokens.sign("oauth", {"nonce": "theirs", "next": "/dashboard"}, 60)
    client = _client()
    client.cookies.set(routes.OAUTH_COOKIE, "mine")
    r = client.get("/api/auth/google/callback", params={"code": "c", "state": state}, follow_redirects=False)
    assert r.status_code == 302 and "/login?error=" in r.headers["location"]
    assert not called                                     # never even asked Google


def test_google_callback_signs_in_a_verified_account(monkeypatch):
    monkeypatch.setenv("ZONULY_GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setenv("ZONULY_GOOGLE_CLIENT_SECRET", "csec")
    monkeypatch.setattr(routes.google, "identify",
                        lambda code: {"email": "ana@gmail.com", "name": "Ana", "sub": "g-1"})
    client = _client()
    start = client.get("/api/auth/google/start", params={"next": "/leads"}, follow_redirects=False)
    assert start.status_code == 302 and start.headers["location"].startswith("https://accounts.google.com/")
    from urllib.parse import parse_qs, urlparse

    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    r = client.get("/api/auth/google/callback", params={"code": "c", "state": state}, follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"].endswith("/leads")
    assert tokens.read_session(r.cookies[access.SESSION_COOKIE])["email"] == "ana@gmail.com"


def test_google_identity_must_be_verified_and_ours(monkeypatch):
    monkeypatch.setenv("ZONULY_GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setenv("ZONULY_GOOGLE_CLIENT_SECRET", "csec")

    class R:
        def __init__(self, status, data):
            self.status_code, self._d = status, data

        def json(self):
            return self._d

    def run(claims):
        monkeypatch.setattr(routes.google.httpx, "post", lambda *a, **k: R(200, {"id_token": "t"}))
        monkeypatch.setattr(routes.google.httpx, "get", lambda *a, **k: R(200, claims))
        return routes.google.identify("code")

    good = {"aud": "cid", "iss": "https://accounts.google.com", "email_verified": "true",
            "email": "Ana@Gmail.com", "name": "Ana", "sub": "1"}
    assert run(good)["email"] == "ana@gmail.com"
    for bad in ({**good, "aud": "someone-else"}, {**good, "email_verified": "false"},
                {**good, "iss": "evil.com"}):
        with pytest.raises(routes.google.GoogleError):
            run(bad)


# ------------------------------------------------------------------- Catalyst Data Store

class _Req:
    def __init__(self, headers):
        self.headers = headers


def test_catalyst_calls_are_built_the_way_the_sdk_builds_them(monkeypatch):
    sent = []

    class R:
        status_code = 200
        text = ""

        def __init__(self, data):
            self._d = data

        def json(self):
            return self._d

    def fake(method, url, **kw):
        sent.append((method, url, kw))
        if url.endswith("/query"):
            return R({"data": []})
        return R({"data": [{"ROWID": "99"}]})

    monkeypatch.setattr(store.httpx, "request", fake)
    monkeypatch.setenv("X_ZOHO_CATALYST_CONSOLE_URL", "https://api.catalyst.zoho.in")
    req = _Req({"x-zc-projectid": "846", "x-zc-admin-cred-token": "TOK", "x-zc-admin-cred-type": "token",
                "x-zc-project-key": "PK", "x-zc-environment": "Development"})
    user = store.CatalystStore().upsert_login("a@b.co", provider="email", request=req)
    assert user == {"id": "99", "email": "a@b.co", "name": None, "provider": "email"}

    q_method, q_url, q_kw = sent[0]
    assert q_method == "POST" and q_url == "https://api.catalyst.zoho.in/baas/v1/project/846/query"
    assert q_kw["headers"]["Authorization"] == "Zoho-oauthtoken TOK"
    assert q_kw["headers"]["PROJECT_ID"] == "PK" and q_kw["headers"]["X-CATALYST-USER"] == "admin"
    assert q_kw["headers"]["Accept"] == "application/vnd.catalyst.v2+zcql"
    assert q_kw["json"]["query"] == "SELECT * FROM ZonulyUsers WHERE email = 'a@b.co'"
    i_method, i_url, i_kw = sent[1]
    assert i_method == "POST" and i_url.endswith("/table/ZonulyUsers/row")
    assert i_kw["json"][0]["email"] == "a@b.co"


def test_catalyst_store_refuses_without_the_gateway_credential():
    with pytest.raises(store.StoreError):
        store.CatalystStore().upsert_login("a@b.co", provider="email", request=_Req({}))


def test_a_store_outage_is_a_clear_503_not_a_stack_trace(monkeypatch):
    class Down:
        def upsert_login(self, *a, **k):
            raise store.StoreError("down")

    store.set_store(Down())
    client = _client()
    body = _start(client).json()
    r = client.post("/api/auth/email/verify", json={"challenge": body["challenge"], "code": body["dev_code"]})
    assert r.status_code == 503 and "try again" in r.json()["detail"]


def test_sessions_last_thirty_days():
    t = tokens.session_for({"id": 1, "email": "a@b.co"})
    assert tokens.read_session(t)["exp"] - time.time() > 29 * 24 * 3600
