"""Create (or find) the ZoNuLy company, project, agents and routine in the local Paperclip.

Idempotent: everything is looked up by name first; only what is missing is created. The ids are
written to data/paperclip.json (git-ignored — they are this machine's). Mutations go through the
pinned `paperclipai` CLI, reads through the loopback API. Nothing here imports jobhunter.

The agents run Claude Code **without** the permission bypass: file edits inside their worktree
are accepted (`--permission-mode acceptEdits`), and a Bash command runs only if the repo's
.claude/settings.json allows it. A command that is not allowed does not run — it is reported.

    python tools/paperclip/bootstrap.py            # create what is missing, print the ids
    python tools/paperclip/bootstrap.py --status          # agents, routines, open issues
    python tools/paperclip/bootstrap.py --instructions    # push agents/*.md to every agent in place
"""
from __future__ import annotations

import json
import subprocess
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
VERSION = "2026.1023.0"
API = "http://127.0.0.1:3100/api"
IDS = REPO / "data" / "paperclip.json"
ROSTER = json.loads((HERE / "roster.json").read_text())


def cli(*args: str) -> dict | list:
    cmd = ["npx", "-y", f"paperclipai@{VERSION}", *args, "--json"]
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit(f"paperclipai {' '.join(args[:2])} failed:\n{out.stderr.strip() or out.stdout.strip()}")
    text = out.stdout.strip()
    # the CLI may print a notice line before the JSON — take from the first bracket
    start = min((i for i in (text.find("{"), text.find("[")) if i >= 0), default=0)
    return json.loads(text[start:])


def get(path: str):
    with urllib.request.urlopen(f"{API}{path}", timeout=10) as r:
        return json.load(r)


def rows(x) -> list:
    """The API answers with a list, or an object with the list under a key."""
    if isinstance(x, list):
        return x
    for v in x.values():
        if isinstance(v, list):
            return v
    return []


def find(items, key, value):
    return next((i for i in rows(items) if i.get(key) == value), None)


def instructions(name: str) -> str:
    return (HERE / "agents" / "COMMON.md").read_text() + "\n\n---\n\n" + (HERE / "agents" / name).read_text()


def ensure_experimental() -> None:
    """Isolated git-worktree workspaces are an experimental instance setting; without it every
    issue runs in the shared main checkout (seen 2026-10-31)."""
    want = ROSTER.get("experimental") or {}
    cur = get("/instance/settings").get("experimental") or {}
    if all(cur.get(k) == v for k, v in want.items()):
        print("instance   experimental settings    ok")
        return
    req = urllib.request.Request(f"{API}/instance/settings/experimental", data=json.dumps(want).encode(),
                                 method="PATCH", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10):
        pass
    print(f"instance   experimental settings    set {want}")


def bootstrap() -> dict:
    ids: dict = {"repo": str(REPO)}
    ensure_experimental()
    c = ROSTER["company"]
    company = find(get("/companies"), "name", c["name"]) or cli("company", "create", "--payload-json", json.dumps(c))
    ids["company"] = cid = company["id"]
    print(f"company   {c['name']:<22} {cid}")

    agents_now = get(f"/companies/{cid}/agents")
    ids["agents"] = {}
    for a in ROSTER["agents"]:
        existing = find(agents_now, "name", a["name"])
        if existing:
            ids["agents"][a["key"]] = existing["id"]
            print(f"agent     {a['name']:<22} {existing['id']}  (exists)")
            continue
        payload = {
            "name": a["name"], "role": a["role"], "title": a["title"], "capabilities": a["capabilities"],
            "adapterType": "claude_local",
            "adapterConfig": {
                "cwd": str(REPO), "engine": "cli", "model": a["model"],
                "maxTurnsPerRun": a["maxTurnsPerRun"], "timeoutSec": a["timeoutSec"], "graceSec": 30,
                "dangerouslySkipPermissions": False,
                "extraArgs": ["--permission-mode", a["permissionMode"]],
            },
            "instructionsBundle": {"entryFile": "AGENTS.md", "files": {"AGENTS.md": instructions(a["instructions"])}},
            "budgetMonthlyCents": a["budgetMonthlyCents"],
            # woken by issues and the routine, never by a timer; one run at a time on an 8 GB machine
            "runtimeConfig": {"heartbeat": {"enabled": False, "maxConcurrentRuns": 1}},
        }
        if a.get("reportsTo"):
            payload["reportsTo"] = ids["agents"][a["reportsTo"]]
        created = cli("agent", "create", "-C", cid, "--payload-json", json.dumps(payload))
        ids["agents"][a["key"]] = created["id"]
        print(f"agent     {a['name']:<22} {created['id']}")

    p = ROSTER["project"]
    project = find(get(f"/companies/{cid}/projects"), "name", p["name"])
    if not project:
        project = cli("project", "create", "-C", cid, "--name", p["name"], "--description", p["description"],
                      "--status", p["status"], "--lead-agent-id", ids["agents"]["cto"],
                      "--execution-workspace-policy-json", json.dumps(p["executionWorkspacePolicy"]))
        ws = dict(p["workspace"], cwd=str(REPO))
        cli("project-workspace", "create", project["id"], "--payload-json", json.dumps(ws))
        print(f"project   {p['name']:<22} {project['id']}  + primary workspace {REPO}")
    else:
        print(f"project   {p['name']:<22} {project['id']}  (exists)")
    ids["project"] = project["id"]

    routines_now = get(f"/companies/{cid}/routines")
    ids["routines"] = {}
    for r in ROSTER["routines"]:
        routine = find(routines_now, "title", r["title"])
        if not routine:
            routine = cli("routine", "create", "-C", cid, "--payload-json", json.dumps({
                "title": r["title"], "description": r["description"], "priority": r["priority"],
                "assigneeAgentId": ids["agents"][r["assignee"]],
            }))
            cli("routine", "trigger:create", routine["id"], "--payload-json", json.dumps(r["trigger"]))
            print(f"routine   {r['trigger']['label']:<22} {routine['id']}  → {r['assignee']}")
        else:
            print(f"routine   {r['trigger']['label']:<22} {routine['id']}  (exists)")
        ids["routines"][r["key"]] = routine["id"]

    IDS.parent.mkdir(exist_ok=True)
    IDS.write_text(json.dumps(ids, indent=2) + "\n")
    print(f"ids → {IDS.relative_to(REPO)}")
    return ids


def push_instructions() -> None:
    """Re-upload COMMON.md + <ROLE>.md as every agent's AGENTS.md, in place (no recreate).
    The API wants the current revision id as the base, so each file is read first."""
    ids = json.loads(IDS.read_text())
    for a in ROSTER["agents"]:
        aid = ids["agents"][a["key"]]
        cur = get(f"/agents/{aid}/instructions-bundle/file?path=AGENTS.md")
        if cur.get("content") == instructions(a["instructions"]):
            print(f"{a['name']:<18} unchanged")
            continue
        body = json.dumps({"path": "AGENTS.md", "content": instructions(a["instructions"]),
                           "baseRevisionId": (cur.get("revision") or {}).get("id")}).encode()
        req = urllib.request.Request(f"{API}/agents/{aid}/instructions-bundle/file", data=body, method="PUT",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15):
            pass
        print(f"{a['name']:<18} updated")


def status() -> None:
    if not IDS.exists():
        sys.exit("not bootstrapped — run tools/paperclip/paperclip.sh bootstrap")
    ids = json.loads(IDS.read_text())
    cid = ids["company"]
    for a in rows(get(f"/companies/{cid}/agents")):
        spent = a.get("spentMonthlyCents", 0) / 100
        cap = a.get("budgetMonthlyCents", 0) / 100
        print(f"agent    {a['name']:<10} {a.get('status','?'):<9} ${spent:.2f} / ${cap:.0f}  {a.get('pauseReason') or ''}")
    for r in rows(get(f"/companies/{cid}/routines")):
        print(f"routine  {r['title'][:40]:<40} {r.get('status','?')}")
    issues = [i for i in rows(get(f"/companies/{cid}/issues")) if i.get("status") not in ("done", "cancelled")]
    print(f"issues   {len(issues)} open")
    for i in issues[:10]:
        print(f"         {i.get('identifier','?'):<8} {i.get('status','?'):<12} {i['title'][:60]}")


if __name__ == "__main__":
    if "--status" in sys.argv:
        status()
    elif "--instructions" in sys.argv:
        push_instructions()
    else:
        bootstrap()
