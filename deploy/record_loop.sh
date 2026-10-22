#!/usr/bin/env bash
# Record AAVE + BTC together, forever, in 24-hour files: data/rec/<COIN>_<YYYYMMDD-HHMM>.jsonl
# Both coins start at the same moment and share the same stamp, so they always pair up.
# Run by systemd (deploy/jev-recorder.service); a restart starts a fresh pair, never overwrites.
set -u
cd "$(dirname "$0")/.."
mkdir -p data/rec
while true; do
  stamp=$(date -u +%Y%m%d-%H%M)
  echo "$(date -u '+%F %T') starting pair $stamp"
  for coin in AAVE BTC; do
    uv run --frozen jev-trader record --coin "$coin" --minutes 1440 \
      --out "data/rec/${coin}_${stamp}.jsonl" &
  done
  wait            # both coins finish their 24 h, then the next pair starts
  df -h . | tail -1
done
