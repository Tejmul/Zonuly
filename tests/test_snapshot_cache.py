"""The public instance's response cache (jobhunter/snapshot_cache.py)."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.testclient import TestClient

from jobhunter import access, snapshot_cache


def _app():
    app = FastAPI()
    calls = {"n": 0}

    @app.get("/api/big")
    def big():
        calls["n"] += 1
        return {"rows": ["x" * 40] * 200}  # over the gzip threshold

    @app.get("/api/small")
    def small():
        calls["n"] += 1
        return {"ok": True}

    @app.get("/api/health")
    def health():
        calls["n"] += 1
        return {"ok": True}

    app.add_middleware(snapshot_cache.SnapshotCache)
    app.add_middleware(GZipMiddleware, minimum_size=2048)  # outermost, as in jobhunter/api.py
    return app, calls


def test_cached_answers_decode_the_same_with_gzip_outside(monkeypatch):
    # Regression: gzip edits the header list in place; a cached reference to it labelled
    # a plain body as gzip and every client failed to decode the second answer.
    monkeypatch.setattr(access, "PUBLIC", True)
    app, calls = _app()
    client = TestClient(app)
    for path in ("/api/big", "/api/small"):
        first = client.get(path, headers={"accept-encoding": "gzip"})
        second = client.get(path, headers={"accept-encoding": "gzip"})
        plain = client.get(path, headers={"accept-encoding": "identity"})
        assert first.json() == second.json() == plain.json()
        assert second.headers.get("x-snapshot-cache") == "hit"
        assert "s-maxage" in second.headers["cache-control"]
        assert "content-encoding" not in plain.headers
    assert calls["n"] == 2  # each route computed once


def test_off_on_a_private_instance_and_for_probes_and_operators(monkeypatch):
    monkeypatch.setattr(access, "PUBLIC", False)
    app, calls = _app()
    client = TestClient(app)
    client.get("/api/small"), client.get("/api/small")
    assert calls["n"] == 2

    monkeypatch.setattr(access, "PUBLIC", True)
    monkeypatch.setattr(access, "_KEY", "sekret")
    client.get("/api/health"), client.get("/api/health")
    client.get("/api/big", headers={"x-api-key": "sekret"}), client.get("/api/big", headers={"x-api-key": "sekret"})
    assert calls["n"] == 6
