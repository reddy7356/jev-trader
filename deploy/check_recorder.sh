#!/usr/bin/env bash
# Daily health check for the 24/7 recorder (cron line in docs/runbook.md).
#
# Restarts jev-recorder when the unit is down, or when it is "active" but no record
# has been written for STALE_S seconds (a hung websocket, a stalled disk, ...).
set -uo pipefail

cd "$(dirname "$0")/.." || exit 1

# Resolve to the same directory the unit writes to (Environment=REC_DIR in the
# systemd drop-in). `data/rec` is a symlink to /data/jev-rec/rec on this host, so the
# repo-relative default keeps this check and the recorder in agreement: a check that
# looks at a different directory calls a healthy pair stale and restarts it.
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

# Report the filesystem the recordings actually live on: `df .` would print the repo
# volume (the root disk), which is what makes a full `/` look like a recorder problem.
rec_real=$(readlink -f "$REC_DIR" 2>/dev/null)
echo "Record dir: ${rec_real:-$REC_DIR}"
echo "Disk:      $(df -h "$REC_DIR" 2>/dev/null | awk 'NR==2 {print $4 " free (" $5 " used) on " $1}')"
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
