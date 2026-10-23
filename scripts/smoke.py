#!/usr/bin/env python3
"""Check that a running instance serves what a visitor expects — and fail loudly if not.

Every check here is one that has broken silently before: a snapshot older than the
code, an image without the list files, a list page with no companies on it.

    python scripts/smoke.py https://zonuly-api-50046893527.development.catalystappsail.in   # after a deploy
    python scripts/smoke.py --db demo.db                           # before one (CI)
    python scripts/smoke.py <api-url> --dashboard https://<app>    # and the dashboard

Exit 0 when everything passes, 1 with the list of failures otherwise.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

#: The cities the ★ India list must show. Only these two — Delhi/NCR is out by choice.
CITIES = ("Bengaluru", "Hyderabad")


def _client(url: str | None, db: Path | None):
    if db is not None:
        os.environ["ZONULY_DB_PATH"] = str(db.resolve())
        os.environ.setdefault("ZONULY_PUBLIC", "1")   # judge it as a visitor would
        from fastapi.testclient import TestClient

        from jobhunter.api import app

        return TestClient(app)
    import httpx

    return httpx.Client(base_url=url.rstrip("/"), timeout=60, follow_redirects=True)


def run(url: str | None, db: Path | None, dashboard: str | None) -> list[str]:
    fails: list[str] = []
    c = _client(url, db)

    def get(path: str):
        r = c.get(path)
        if r.status_code != 200:
            fails.append(f"GET {path} -> HTTP {r.status_code}")
            return None
        return r.json()

    health = get("/api/health")
    if health:
        snap = health.get("snapshot")
        if snap is None:
            fails.append("health: no snapshot stamp — demo.db was not built by scripts/make_demo_db.py")
        else:
            if not snap.get("schema_ok"):
                fails.append(f"health: snapshot schema {snap.get('schema')} != code {snap.get('schema_now')} "
                             "— rebuild demo.db and redeploy")
            for key, want in (snap.get("lists_expected") or {}).items():
                have = (snap.get("lists") or {}).get(key, 0)
                if have < want:
                    fails.append(f"health: list {key} has {have} of {want} companies attached")
            print(f"  snapshot {snap.get('built_at')} commit {snap.get('git_sha')}")

    lists = get("/api/lists") or []
    if not lists:
        fails.append("/api/lists is empty — data/lists/*.yaml is missing from the image")
    for spec in lists:
        body = get(f"/api/lists/{spec['key']}")
        if not body:
            continue
        rows = body.get("companies") or []
        if not rows:
            fails.append(f"list {spec['key']}: no companies")
        if spec["key"] == "india-star":
            by_city = (body.get("stats") or {}).get("by_city") or {}
            for city in CITIES:
                if not any(city in k for k in by_city):
                    fails.append(f"list india-star: no {city} companies (by_city={by_city})")
        print(f"  list {spec['key']}: {len(rows)} companies")

    companies = get("/api/companies?limit=5")
    if companies is not None and not companies:
        fails.append("/api/companies returned nothing")
    get("/api/outreach/pipeline")

    if dashboard:
        import httpx

        r = httpx.get(dashboard, timeout=60, follow_redirects=True)
        if r.status_code != 200 or "<title>" not in r.text:
            fails.append(f"dashboard {dashboard} -> HTTP {r.status_code}")
    return fails


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url", nargs="?", help="base URL of the API")
    ap.add_argument("--db", type=Path, help="check a database file in-process instead of a URL")
    ap.add_argument("--dashboard", help="the dashboard URL, to check it answers")
    args = ap.parse_args()
    if not args.url and not args.db:
        ap.error("give an API URL or --db")

    fails = run(args.url, args.db, args.dashboard)
    if fails:
        print("FAIL")
        for f in fails:
            print(f"  - {f}")
        sys.exit(1)
    print("OK")


if __name__ == "__main__":
    main()
