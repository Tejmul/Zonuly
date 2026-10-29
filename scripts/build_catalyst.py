#!/usr/bin/env python3
"""Assemble the read-only backend for Zoho Catalyst AppSail (Catalyst-managed Python 3.12).

AppSail's managed Python runtime does not install anything: the build folder must carry
the app and every dependency, built for Linux x86_64 (this Mac is arm64, so they are
fetched as manylinux wheels, never copied from .venv). What ships is the public
instance only:

  - the code, config and the hand-picked lists
  - demo.db.gz, the stripped snapshot (no cached pages, no spend, no inbound mail),
    unpacked to /tmp at start because the deployed file system is not ours to write
  - ZONULY_PUBLIC=1, so every write route and every private read is refused

Never shipped: .env, secrets/, jobhunter.db, profile/, the backups.

Left out on purpose: Playwright and the Google API client (~235 MB together). They are
imported lazily by the logged-in X search and Gmail OAuth, neither of which a public,
read-only instance can reach.

    python scripts/build_catalyst.py          # -> deploy/catalyst/appsail/build/
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APPSAIL = ROOT / "deploy" / "catalyst" / "appsail"
BUILD = APPSAIL / "build"

#: never in the image — big, and only behind routes a public instance refuses
EXCLUDE = {"playwright", "google-api-python-client", "google-auth-oauthlib"}

START = """#!/bin/sh
# AppSail start: unpack the snapshot where we may write, then serve it read-only.
set -e
cd "$(dirname "$0")"
python3 -c "import gzip,shutil; shutil.copyfileobj(gzip.open('demo.db.gz','rb'), open('/tmp/zonuly.db','wb'))"
# the image shares AppSail's memory with /tmp: once unpacked, the 50 MB archive is dead weight
# (2026-10-29: the snapshot grew 54 MB and the instance stopped coming up)
rm -f demo.db.gz 2>/dev/null || true
export ZONULY_DB_PATH=/tmp/zonuly.db
export ZONULY_PUBLIC=1
export PYTHONPATH="$PWD/vendor:$PWD"
exec python3 -m uvicorn jobhunter.api:app --host 0.0.0.0 --port "${X_ZOHO_CATALYST_LISTEN_PORT:-9000}"
"""


def _requirements() -> list[str]:
    out = subprocess.run(
        ["uv", "export", "--frozen", "--no-dev", "--no-hashes", "--no-emit-project", "--no-header"],
        cwd=ROOT, check=True, capture_output=True, text=True,
    ).stdout
    keep = []
    for line in out.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "-e", "./")):
            continue
        name = line.split("==")[0].split(";")[0].strip().lower()
        if name in EXCLUDE:
            continue
        keep.append(line)
    return keep


def main() -> None:
    if BUILD.exists():
        shutil.rmtree(BUILD)
    BUILD.mkdir(parents=True)

    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    shutil.copytree(ROOT / "jobhunter", BUILD / "jobhunter", ignore=ignore)
    shutil.copytree(ROOT / "data" / "lists", BUILD / "data" / "lists")
    (BUILD / "knowledge").mkdir()
    for f in ("BRIEF.md", "context.yaml"):
        if (ROOT / "knowledge" / f).exists():
            shutil.copy2(ROOT / "knowledge" / f, BUILD / "knowledge" / f)
    for f in ("config.yaml", "companies.yaml", "demo.db.gz", "pyproject.toml"):
        shutil.copy2(ROOT / f, BUILD / f)
    (BUILD / "start.sh").write_text(START)
    (BUILD / "start.sh").chmod(0o755)

    reqs = _requirements()
    req_file = BUILD / ".requirements.txt"
    req_file.write_text("\n".join(reqs) + "\n")
    subprocess.run(
        ["uv", "pip", "install", "--quiet", "--target", str(BUILD / "vendor"),
         "--python-platform", "x86_64-manylinux_2_28", "--python-version", "3.12",
         "--no-deps", "-r", str(req_file)],
        cwd=ROOT, check=True,
    )
    req_file.unlink()

    # nothing private may ride along
    for bad in (".env", "secrets", "jobhunter.db", "profile"):
        if (BUILD / bad).exists():
            sys.exit(f"refusing: {bad} ended up in the build")

    size = sum(p.stat().st_size for p in BUILD.rglob("*") if p.is_file())
    print(f"{BUILD}  {size / 1_048_576:.0f} MB  ({len(reqs)} packages, excluded: {', '.join(sorted(EXCLUDE))})")


if __name__ == "__main__":
    main()
