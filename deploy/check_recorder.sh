#!/usr/bin/env bash
# Daily health check for the 24/7 recorder (cron line in docs/runbook.md).
#
# Restarts jev-recorder when the unit is down, or when it is "active" but no record
# has been written for STALE_S seconds (a hung websocket, a stalled disk, ...).
set -uo pipefail

cd "$(dirname "$0")/.." || exit 1

REC_DIR=${REC_DIR:-data/rec}
STALE_S=${STALE_S:-300}

echo "=== $(date -u '+%F %H:%M UTC') ==="
unit=$(systemctl is-active jev-recorder 2>/dev/null)
echo "Service:   ${unit}"
if [ "$unit" != "active" ]; then
  echo "  -> not active, restarting"
  sudo systemctl restart jev-recorder
  unit=$(systemctl is-active jev-recorder 2>/dev/null)
fi

echo "Disk:      $(df -h . | awk 'NR==2 {print $4 " free (" $5 " used)"}')"
newest=$(ls -1t "$REC_DIR"/*.jsonl 2>/dev/null | head -1)
if [ -z "$newest" ]; then
  age=999999
  echo "Recording: no active .jsonl file"
else
  age=$(( $(date -u +%s) - $(stat -c %Y "$newest") ))
  echo "Recording: $(basename "$newest") $(du -h "$newest" | cut -f1), last write ${age}s ago"
fi

if [ "$age" -gt "$STALE_S" ] && [ "$unit" = "active" ]; then
  echo "  -> no record for ${STALE_S}s, restarting"
  sudo systemctl restart jev-recorder
fi

echo "Archives:  $(ls -1 "$REC_DIR"/*.jsonl.gz 2>/dev/null | wc -l) days, newest: $(ls -1t "$REC_DIR"/*.jsonl.gz 2>/dev/null | head -1 | xargs -r basename)"
echo
