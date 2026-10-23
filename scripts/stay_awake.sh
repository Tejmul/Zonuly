#!/bin/sh
# Keep the Mac running with the lid closed, so the scrapers keep going (2026-10-23).
#
#   sh scripts/stay_awake.sh on       # never sleep, even with the lid closed (asks for your password)
#   sh scripts/stay_awake.sh off      # back to normal sleep
#   sh scripts/stay_awake.sh status
#
# Why this and not Caffeine / caffeinate: both stop *idle* sleep only. macOS sleeps on lid
# close regardless; `pmset disablesleep 1` is the switch that overrides that. The launchd job
# com.zonuly.awake (caffeinate -i -s) already covers the lid-open case.
#
# Keep it plugged in and out of a closed bag: a laptop that never sleeps gets hot and drains
# its battery. Nothing keeps running once the Mac is shut down.
set -e
case "${1:-status}" in
  on)
    sudo pmset -a disablesleep 1
    echo "On: the Mac stays awake with the lid closed. Turn off with: sh scripts/stay_awake.sh off"
    ;;
  off)
    sudo pmset -a disablesleep 0
    echo "Off: normal sleep again."
    ;;
  status)
    if pmset -g | grep -q "SleepDisabled[[:space:]]*1"; then
      echo "Lid-closed running: ON"
    else
      echo "Lid-closed running: OFF (sh scripts/stay_awake.sh on)"
    fi
    if launchctl list | grep -q com.zonuly.awake; then
      echo "Idle-sleep guard (com.zonuly.awake, caffeinate): running"
    else
      echo "Idle-sleep guard (com.zonuly.awake): not loaded"
    fi
    ;;
  *)
    echo "usage: sh scripts/stay_awake.sh on|off|status" >&2
    exit 2
    ;;
esac
