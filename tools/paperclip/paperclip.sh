#!/usr/bin/env bash
# Paperclip for ZoNuLy — start / stop / status of the local orchestrator, and the pinned CLI.
#
#   tools/paperclip/paperclip.sh start        # loopback :3100, embedded Postgres, telemetry off
#   tools/paperclip/paperclip.sh stop
#   tools/paperclip/paperclip.sh status       # health + the ZoNuLy agents and their state
#   tools/paperclip/paperclip.sh bootstrap    # company, project, agents, routine (idempotent)
#   tools/paperclip/paperclip.sh cli <args>   # any paperclipai command at the pinned version
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
  *)
    sed -n '2,10p' "$0"; exit 2 ;;
esac
