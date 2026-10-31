#!/usr/bin/env bash
# Paperclip for ZoNuLy — start / stop / status of the local orchestrator, and the pinned CLI.
#
#   tools/paperclip/paperclip.sh start        # loopback :3100, embedded Postgres, telemetry off
#   tools/paperclip/paperclip.sh stop
#   tools/paperclip/paperclip.sh status       # health + the ZoNuLy agents and their state
#   tools/paperclip/paperclip.sh bootstrap    # company, project, agents, routine (idempotent)
#   tools/paperclip/paperclip.sh cli <args>   # any paperclipai command at the pinned version
#   tools/paperclip/paperclip.sh review-stage ZON-7   # the Reviewer reviews this issue before it can be done
#
# All of Paperclip's state lives in ~/.paperclip (config, Postgres, logs, secrets) — nothing
# in this repository. The repo keeps only this launcher, the roster, and the agents' rules.
# Nothing in jobhunter/ imports or depends on it (same stance as REA and Agent Reach).
set -euo pipefail

VERSION=2026.1023.0
PC_HOME=${PAPERCLIP_HOME:-$HOME/.paperclip}
INSTANCE=$PC_HOME/instances/${PAPERCLIP_INSTANCE_ID:-default}
INFO=$INSTANCE/runtime-info.json
OUT=$INSTANCE/logs/server.out
API=http://127.0.0.1:${PORT:-3100}/api
REPO=$(cd "$(dirname "$0")/../.." && pwd)

export PAPERCLIP_TELEMETRY_DISABLED=1 DO_NOT_TRACK=1

pc() { npx -y "paperclipai@$VERSION" "$@"; }
healthy() { curl -fs -m 3 "$API/health" >/dev/null 2>&1; }

case "${1:-}" in
  start)
    if healthy; then echo "paperclip already up at $API"; exit 0; fi
    mkdir -p "$(dirname "$OUT")"
    nohup npx -y "paperclipai@$VERSION" run >>"$OUT" 2>&1 &
    for _ in $(seq 1 60); do healthy && { echo "paperclip up: http://127.0.0.1:${PORT:-3100}"; exit 0; }; sleep 1; done
    echo "paperclip did not come up in 60s — see $OUT" >&2; exit 1 ;;
  stop)
    if [ -f "$INFO" ]; then
      pid=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["pid"])' "$INFO")
      kill "$pid" 2>/dev/null && echo "sent TERM to $pid" || echo "no process $pid"
      for _ in $(seq 1 20); do kill -0 "$pid" 2>/dev/null || { echo stopped; exit 0; }; sleep 1; done
      echo "still running after 20s" >&2; exit 1
    fi
    echo "no runtime-info.json — not running?" ;;
  status)
    if ! healthy; then echo "paperclip is down ($API)"; exit 1; fi
    curl -s "$API/health" | python3 -c 'import json,sys; h=json.load(sys.stdin); print("paperclip", h["version"], "·", h["deploymentMode"], "·", h["bootstrapStatus"])'
    python3 "$REPO/tools/paperclip/bootstrap.py" --status ;;
  bootstrap)
    shift; healthy || { echo "start paperclip first" >&2; exit 1; }
    python3 "$REPO/tools/paperclip/bootstrap.py" "$@" ;;
  cli)
    shift; pc "$@" ;;
  review-stage)
    # Put the Reviewer on an issue as its review stage: when the engineer marks it done the runtime
    # routes it to the Reviewer by itself (no Manager click). Agents cannot reach the API with curl
    # (not allowlisted) — this subcommand is the one allowed route. Reads ids from data/paperclip.json.
    shift; python3 - "$REPO" "$API" "$@" <<'PY'
import json, sys, urllib.request
repo, api, idents = sys.argv[1], sys.argv[2], sys.argv[3:]
ids = json.load(open(f"{repo}/data/paperclip.json")); rev = ids["agents"]["reviewer"]; cid = ids["company"]
issues = {i["identifier"]: i for i in json.load(urllib.request.urlopen(f"{api}/companies/{cid}/issues", timeout=10))}
policy = {"mode": "normal", "commentRequired": True, "maxReviewRounds": 3,
          "stages": [{"type": "review", "approvalsNeeded": 1, "participants": [{"type": "agent", "agentId": rev}]}]}
for ident in idents:
    i = issues.get(ident.upper())
    if not i: print(f"{ident}: no such issue"); continue
    req = urllib.request.Request(f"{api}/issues/{i['id']}", data=json.dumps({"executionPolicy": policy}).encode(),
                                 method="PATCH", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        st = [s["type"] for s in (json.load(r).get("executionPolicy") or {}).get("stages", [])]
    print(f"{ident}: review stage → Reviewer {st}")
PY
    ;;
  *)
    sed -n '2,10p' "$0"; exit 2 ;;
esac
