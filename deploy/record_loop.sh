#!/usr/bin/env bash
# 24/7 recorder supervisor: AAVE + BTC, one file pair per UTC day under data/rec/.
#
# Install / verify: docs/runbook.md ("Record market data 24/7 on Linux").
#
# - Each pair records until the next UTC midnight, then both files are gzipped to
#   <COIN>_<YYYYMMDD-HHMMSS>.jsonl.gz (slow_replay.py reads those archives as they are).
# - If one recorder dies, the other is stopped too, so the coins never drift apart;
#   the pair is retried with a backoff (30 s, 60 s, ... capped at 5 min).
# - Disk policy: never delete a recent day to make room. Archives older than KEEP_DAYS
#   are removed, and below MIN_FREE_MB the supervisor waits instead of starting a pair
#   that would fail with ENOSPC.
set -uo pipefail

cd "$(dirname "$0")/.." || exit 1

REC_DIR=${REC_DIR:-data/rec}
COINS=${COINS:-AAVE BTC}
MINUTES=${MINUTES:-}                # set to override the UTC-midnight rotation
MIN_FREE_MB=${MIN_FREE_MB:-1024}    # never start a pair below this much free space
KEEP_DAYS=${KEEP_DAYS:-21}          # delete only archives older than this
SHORT_PAIR_S=${SHORT_PAIR_S:-300}   # a pair that ends sooner than this has failed

mkdir -p "$REC_DIR"

log() { printf '%s %s\n' "$(date -u '+%Y-%m-%d %H:%M:%S')" "$*"; }

# `wait -n <pids>` is bash >= 5.1; on an older shell it would fail every iteration.
if (( BASH_VERSINFO[0] < 5 || (BASH_VERSINFO[0] == 5 && BASH_VERSINFO[1] < 1) )); then
  log "needs bash >= 5.1 (wait -n with pids); found ${BASH_VERSION}"
  exit 1
fi

free_mb() { df --output=avail -k "$REC_DIR" | tail -1 | awk '{printf "%d", $1 / 1024}'; }

# One supervisor only: a second copy would interleave two pairs into the same files.
if command -v flock >/dev/null 2>&1; then
  exec 9>"$REC_DIR/.record_loop.lock"
  flock -n 9 || { log "another record_loop.sh holds the lock; exiting"; exit 0; }
fi

archive() {  # gzip one finished file, once, and say what it cost
  local f=$1
  [ -f "$f" ] || return 0
  if gzip -f "$f"; then
    log "archived $(basename "$f").gz ($(du -h "$f.gz" | cut -f1))"
    return 0
  fi
  log "ERROR: could not gzip $(basename "$f") (disk full?); left in place"
  return 1
}

prune_old() {  # age-based retention only: recent days are never deleted for space
  local f
  while IFS= read -r f; do
    log "retention: deleting $(basename "$f") (older than ${KEEP_DAYS} days)"
    rm -f "$f"
  done < <(find "$REC_DIR" -maxdepth 1 -type f -name '*.jsonl.gz' -mtime "+${KEEP_DAYS}" 2>/dev/null)
}

# A crashed run leaves a .jsonl nobody is writing any more: archive it before rotating.
for stale in "$REC_DIR"/*.jsonl; do
  [ -e "$stale" ] || continue
  log "archiving stale $(basename "$stale") from a previous run"
  archive "$stale" || true
done

fails=0
while true; do
  prune_old
  avail=$(free_mb)
  if [ "$avail" -lt "$MIN_FREE_MB" ]; then
    log "low disk: ${avail} MB free (< ${MIN_FREE_MB} MB); not starting a pair, retry in 300s"
    sleep 300
    continue
  fi

  if [ -n "$MINUTES" ]; then
    minutes=$MINUTES
  else
    # Rotate at UTC midnight: one file per day, and a crash never mixes two days.
    remaining=$(( 86400 - $(date -u +%s) % 86400 ))
    minutes=$(awk -v s="$remaining" 'BEGIN { printf "%.4f", s / 60 }')
  fi

  # Seconds, not minutes: a failed pair retried inside the same minute would otherwise
  # reuse its name and `gzip -f` would overwrite the earlier partial recording.
  stamp=$(date -u +%Y%m%d-%H%M%S)
  log "starting pair $stamp for ${minutes} min (${avail} MB free)"
  started=$(date -u +%s)
  pids=()
  for coin in $COINS; do
    uv run --frozen jev-trader record --coin "$coin" --minutes "$minutes" \
      --out "$REC_DIR/${coin}_${stamp}.jsonl" &
    pids+=("$!")
  done

  # The first recorder to exit ends the pair; otherwise the coins drift apart and one
  # keeps writing into a day the other has already finished.
  wait -n "${pids[@]}"
  status=$?
  elapsed=$(( $(date -u +%s) - started ))
  if [ "$elapsed" -lt "$SHORT_PAIR_S" ] || [ "$status" -ne 0 ]; then
    log "recorder exited early after ${elapsed}s (status ${status}); stopping the pair"
    kill "${pids[@]}" 2>/dev/null
    failed=1
  else
    failed=0
  fi
  for pid in "${pids[@]}"; do wait "$pid" 2>/dev/null; done

  for coin in $COINS; do archive "$REC_DIR/${coin}_${stamp}.jsonl"; done

  if [ "$failed" -eq 1 ]; then
    fails=$(( fails + 1 ))
    delay=$(( fails * 30 ))
    [ "$delay" -gt 300 ] && delay=300
    log "pair $stamp failed (${fails} in a row); retry in ${delay}s"
    sleep "$delay"
  else
    fails=0
    log "pair $stamp finished after ${elapsed}s"
  fi
done
