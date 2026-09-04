"""Backend routing — Agent Reach's contract, JobHunter's plumbing.

Agent Reach (github.com/Panniantong/Agent-Reach) is an installer and a doctor, not
a library: its own `core.py` says "after installation, agents call the upstream
tools directly — no wrapper layer needed", and its CLI exposes no read or search
command. So we do not vendor it. We keep the two ideas that are worth keeping —

  1. a capability is served by an *ordered* list of candidate backends, and
  2. `which()` is not proof of health; a backend is only active once it has really
     answered a cheap command

— and shell out to the same upstream tools Agent Reach installs (mcporter/Exa,
Jina Reader, gh, yt-dlp, rdt). Installing them stays Agent Reach's job:

    agent-reach install --system
    agent-reach doctor --json

Secrets are read from the environment (or a gitignored .env) and never logged,
never written to config.yaml, never passed on a command line.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from functools import lru_cache

from jobhunter import CONFIG, ROOT

log = logging.getLogger(__name__)

RESEARCH = CONFIG.get("research") or {}
TIMEOUT = int(RESEARCH.get("timeout", 90))

#: capability -> ordered candidate backends. config.yaml `research.backends` wins.
DEFAULT_ROUTES: dict[str, list[str]] = {
    "web_search": ["exa-mcp", "exa-api"],
    "page_read": ["jina", "direct", "scrapedo"],
    "github": ["gh-cli", "github-api"],
    "reddit": ["rdt", "opencli"],
    "youtube": ["yt-dlp"],
}

#: backend -> the executable it needs (None = pure HTTP, always a candidate)
BACKEND_EXE: dict[str, str | None] = {
    "exa-mcp": "mcporter",
    "exa-api": None,
    "jina": None,
    "direct": None,
    "gh-cli": "gh",
    "github-api": None,
    "rdt": "rdt",
    "opencli": "opencli",
    "yt-dlp": "yt-dlp",
    "scrapedo": None,          # pure HTTP, needs the Scrape_dog token
}

#: backends that only work once a secret is present
BACKEND_SECRET: dict[str, str] = {"exa-api": "EXA_API_KEY", "scrapedo": "Scrape_dog"}


# ------------------------------------------------------------------ secrets

_DOTENV_LOADED = False


def _load_dotenv() -> None:
    """Fold a local, gitignored .env into os.environ. Values already set win.

    Deliberately hand-rolled: one more dependency for `KEY=value` is not worth it,
    and this way the parse rules are visible right here.
    """
    global _DOTENV_LOADED
    if _DOTENV_LOADED:
        return
    _DOTENV_LOADED = True
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
            value = value.strip().strip("\"").strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except OSError as e:  # noqa: BLE001 — a broken .env must not stop research
        log.debug("could not read .env: %s", e)


def secret(*names: str) -> str:
    """First non-empty environment value among `names`. Never logged, never echoed."""
    _load_dotenv()
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def has_secret(*names: str) -> bool:
    return bool(secret(*names))


def github_token() -> str:
    """GH token from the environment, falling back to the existing contacts config."""
    token = secret("GITHUB_TOKEN", "GH_TOKEN")
    if token:
        return token
    return ((CONFIG.get("contacts") or {}).get("github_token") or "").strip()


# ------------------------------------------------------------------ subprocess


@dataclass
class Proc:
    ok: bool
    out: str = ""
    err: str = ""
    code: int | None = None
    reason: str = ""          # missing | timeout | failed | broken


@lru_cache(maxsize=64)
def resolve(name: str) -> str | None:
    """Absolute path to a CLI, or None.

    On Windows this matters: node-installed tools land as `mcporter.CMD` and only
    `shutil.which` (PATHEXT-aware) finds them. The resolved path is then executed
    directly — never through a shell — so nothing has to be quoted or escaped.
    """
    return shutil.which(name)


def _child_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """A child environment that survives Windows consoles.

    Several of these CLIs print box-drawing characters and emoji. Under the default
    Windows code page that raises UnicodeEncodeError inside the child and the output
    is lost entirely, so force UTF-8 both ways.
    """
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    if os.name == "nt":
        env.setdefault("NO_COLOR", "1")
    if extra:
        env.update(extra)
    return env


def run(
    exe: str,
    args: list[str],
    *,
    timeout: int | None = None,
    env_extra: dict[str, str] | None = None,
    stdin: str | None = None,
) -> Proc:
    """Run a CLI and capture its output. Never raises; failures come back as Proc.

    `env_extra` is how secrets reach a child process — an environment variable is
    invisible to `ps` / Task Manager, an argv token is not.
    """
    path = resolve(exe)
    if not path:
        return Proc(False, reason="missing", err=f"{exe} not on PATH")
    try:
        r = subprocess.run(
            [path, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout or TIMEOUT,
            env=_child_env(env_extra),
            input=stdin,
            shell=False,
            check=False,
        )
    except subprocess.TimeoutExpired:
        log.debug("%s timed out after %ss", exe, timeout or TIMEOUT)
        return Proc(False, reason="timeout", err=f"{exe} timed out after {timeout or TIMEOUT}s")
    except OSError as e:  # noqa: BLE001 — a stale shim resolves but cannot execute
        return Proc(False, reason="broken", err=f"{exe} could not be executed: {e}")
    if r.returncode != 0:
        return Proc(
            False,
            out=r.stdout or "",
            err=(r.stderr or "")[:2000],
            code=r.returncode,
            reason="failed",
        )
    return Proc(True, out=r.stdout or "", err=r.stderr or "", code=0)


# ------------------------------------------------------------------ routing


def routes(capability: str) -> list[str]:
    configured = (RESEARCH.get("backends") or {}).get(capability)
    if isinstance(configured, list) and configured:
        return [str(b) for b in configured]
    return list(DEFAULT_ROUTES.get(capability, []))


def plausible(backend: str) -> bool:
    """Cheap candidacy test: the tool is on PATH and any secret it needs exists.

    Cheap on purpose — this is not a health claim. A backend only becomes *active*
    once it has actually returned content, which is what callers of `candidates()`
    find out by trying them in order.
    """
    exe = BACKEND_EXE.get(backend)
    if exe and not resolve(exe):
        return False
    need = BACKEND_SECRET.get(backend)
    if need and not has_secret(need):
        return False
    return True


def candidates(capability: str) -> list[str]:
    """Backends worth attempting for a capability, best first."""
    return [b for b in routes(capability) if plausible(b)]


# ------------------------------------------------------------------ doctor

_PROBES: dict[str, tuple[str, list[str]]] = {
    "mcporter": ("mcporter", ["--version"]),
    "gh": ("gh", ["--version"]),
    "yt-dlp": ("yt-dlp", ["--version"]),
    "rdt": ("rdt", ["--version"]),
    "opencli": ("opencli", ["--version"]),
    "agent-reach": ("agent-reach", ["version"]),
}


def probe(exe: str, timeout: int = 25) -> tuple[str, str]:
    """(status, detail) for one CLI.

    Really executes it: a stale venv shim passes `which()` and still cannot run,
    which is exactly the failure Agent Reach's own probe module exists to catch.
    """
    if exe not in _PROBES:
        return ("unknown", f"no probe defined for {exe}")
    name, args = _PROBES[exe]
    if not resolve(name):
        return ("missing", f"{name} not on PATH")
    p = run(name, args, timeout=timeout)
    if p.ok:
        first = (p.out.strip().splitlines() or [""])[0]
        return ("ok", first[:120])
    if p.reason == "broken":
        return ("broken", p.err[:200])
    if p.reason == "timeout":
        return ("timeout", p.err[:200])
    return ("error", (p.err or p.out).strip()[:200])


HINTS = {
    "exa-mcp": "npm install -g mcporter && mcporter config add exa https://mcp.exa.ai/mcp --scope home",
    "exa-api": "set EXA_API_KEY in .env (free tier at https://exa.ai)",
    "gh-cli": "install GitHub CLI (https://cli.github.com), then `gh auth login`",
    "github-api": "optional: set GITHUB_TOKEN in .env to lift 60/hr to 5000/hr",
    "rdt": "pipx install rdt-cli, then `rdt login` — Reddit has no anonymous path",
    "opencli": "desktop only: agent-reach install --system --channels opencli",
    "yt-dlp": 'python -m pip install -U "yt-dlp[default]"',
    "jina": "no setup — https://r.jina.ai is public",
    "direct": "no setup — plain HTTP via httpx",
}


def doctor() -> dict:
    """What this machine can actually do right now, and what would fix the rest."""
    tools = {exe: dict(zip(("status", "detail"), probe(exe))) for exe in _PROBES}

    caps: dict[str, dict] = {}
    for capability in DEFAULT_ROUTES:
        ordered = routes(capability)
        usable = candidates(capability)
        caps[capability] = {
            "configured": ordered,
            "usable": usable,
            "preferred": usable[0] if usable else None,
            "hints": {b: HINTS.get(b, "") for b in ordered if b not in usable},
        }

    return {
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "tools": tools,
        "capabilities": caps,
        "secrets": {  # presence only — values are never surfaced
            "EXA_API_KEY": has_secret("EXA_API_KEY"),
            "GITHUB_TOKEN": bool(github_token()),
        },
        "agent_reach": {
            "installed": tools["agent-reach"]["status"] == "ok",
            "note": (
                "Agent Reach installs and health-checks the upstream tools; run "
                "`agent-reach install --system` / `agent-reach doctor --json`."
            ),
        },
    }
