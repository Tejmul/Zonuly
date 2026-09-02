"""JobHunter — AI job-scraping & referral outreach agent."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def load_config() -> dict:
    with open(ROOT / "config.yaml") as f:
        return yaml.safe_load(f)


CONFIG = load_config()


# ---------------------------------------------------------------- secrets

_DOTENV_LOADED = False


def _load_dotenv() -> None:
    """Fold a local, gitignored .env into os.environ. Values already set win.

    Hand-rolled on purpose: one more dependency for `KEY=value` is not worth it,
    and this way the parse rules are visible right here.
    """
    global _DOTENV_LOADED
    if _DOTENV_LOADED:
        return
    _DOTENV_LOADED = True
    import os

    path = ROOT / ".env"
    try:
        if not path.exists():
            return
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except OSError:  # noqa: BLE001 — a broken .env must not stop the program
        pass


def secret(*names: str) -> str:
    """First non-empty environment value among `names`, else "".

    The one way credentials enter this program. Never read from config.yaml, never
    logged, never passed on a command line — an environment variable is invisible
    to `ps` and Task Manager, an argv token is not.
    """
    import os

    _load_dotenv()
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def has_secret(*names: str) -> bool:
    return bool(secret(*names))
