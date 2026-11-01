# Runbook: running jev-trader live (Hyperliquid TESTNET)

What to do, in order, when something happens. Keep this next to the dashboard.

## The fallback ladder (what the bot does on its own)

Every block (~300 ms) the loop asks Jev for judgments. When that goes wrong, it
steps down this ladder by itself, and each step is safe:

| Situation | What the bot does | Counts as |
|---|---|---|
| Jev answers inside 250 ms | quote per policy | normal |
| Jev slower than 250 ms (~5% of blocks) | **hold**: cancel resting quotes, place nothing new | timeout |
| Jev returns an error | use the hand-rules judge for this block | fallback |
| more than 5 Jev errors **in a row** (~2 s outage) | **KILL**: cancel all, close the position, stop | kill |
| Jev says stand down / pull quotes while holding a position | keep one quote on the side that shrinks it | - |
| Jev says reduce "hard" (urgency >= 2) and the position is losing | **exit**: sell/buy half of it at market (at most every 20 blocks) | exit |
| open position losing more than $50 | **stop-loss**: close all of it at market, alert | stop_loss |
| market data older than 2 s | **hold** | - |
| position > max (= one order size), or held longer than 15 min | **reduce only**: quote only the side that shrinks it, half size | - |
| daily loss > limit, drawdown > limit, position > 1.5x max | **KILL** | kill |
| process stalls or crashes | Hyperliquid cancels all orders by itself (dead-man's switch) | - |
| process crashes | launchd restarts it after 30 s; orphan orders are cancelled on the first sync | - |

A kill is **sticky**: it is saved in `data/live_risk.json`, so restarting the
process does not resume trading. Only you can re-arm it (see below).

## Alerts and what to do

Alerts go to the log (`data/live.log`) and, if `ALERT_WEBHOOK_URL` is set in
`.env`, to Slack/Discord. Each kind repeats at most every 10 minutes.

| Alert | Means | Do this |
|---|---|---|
| `[latency]` decision latency p95 > 250 ms | Jev is slow; more blocks hold | Check [TypeSafe status](https://docs.typesafe.ai). Nothing to do if it passes; if it lasts an hour, stop the bot. |
| `[error_rate]` > 10% of blocks failed | many timeouts or errors | Same as above. Check your internet connection. |
| `[jev_down]` Jev failed 3 blocks in a row | Jev unreachable; hand rules are trading | Watch for 1 minute. At 6 in a row the bot kills itself. |
| `[drawdown]` past half the kill limit | losing money | Open the dashboard. Decide whether to stop now or let the limit handle it. |
| `[kill]` | bot stopped and flattened | Follow **After a kill** below. |

## Everyday commands

```bash
uv run jev-trader dashboard --log data/live_calibration.jsonl --every 30   # then open data/dashboard.html
uv run jev-trader kill      # cancel every order + close the position NOW (safe any time)
uv run jev-trader rearm     # clear a sticky kill so `live` may trade again
tail -f data/live.log       # the bot's own log
```

## After a kill

1. Run `uv run jev-trader kill` once more. It's harmless, and it confirms there are no open orders and no position.
2. Read the reason: `cat data/live_risk.json` (the `killed` field) and the end of `data/live.log`.
3. Fix the cause, or wait it out if it was an outage (e.g. DNS failure, TypeSafe 503s).
4. `uv run jev-trader rearm`. The daily-loss baseline is **not** reset by this;
   a loss-limit kill stays blocked for the rest of the UTC day unless you delete the file.
5. Restart: `launchctl kickstart gui/$(id -u)/com.jevtrader.live`.

## Install / stop the 24/7 service (macOS)

```bash
sed "s|__REPO__|$PWD|g" deploy/com.jevtrader.live.plist > ~/Library/LaunchAgents/com.jevtrader.live.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.jevtrader.live.plist   # start
launchctl bootout   gui/$(id -u)/com.jevtrader.live                               # stop (then run `kill`)
```

The Mac must stay awake: `caffeinate -s` in a terminal, or System Settings >
Battery > prevent sleeping when the display is off.

## Record market data 24/7 (Linux)

The Phase 7 replays need AAVE + BTC books and trades with no gaps. On Linux the
recorder runs under systemd instead of launchd:

```bash
sudo cp deploy/jev-recorder.service /etc/systemd/system/
sudo mkdir -p /etc/systemd/system/jev-recorder.service.d
sudo cp deploy/jev-recorder.service.d/override.conf /etc/systemd/system/jev-recorder.service.d/
sudo systemctl daemon-reload
sudo systemctl enable --now jev-recorder
systemctl status jev-recorder --no-pager
journalctl -u jev-recorder -f        # "starting pair ...", "archived ...", errors
```

The drop-in sets `REC_DIR=/data/jev-rec/rec` so the recordings never land on the
root volume; drop it and the recorder writes to the 8 GB disk that also holds the
toolchain.

`deploy/record_loop.sh` records both coins together and rotates at **UTC midnight**:

- one file pair per day: `data/rec/AAVE_<YYYYMMDD-HHMMSS>.jsonl`, `BTC_...`
- at midnight each file is gzipped to `.jsonl.gz`; `scripts/slow_replay.py` and
  `gatekeeper/tape.merged_records` read the archives in place, so do not gunzip by hand
- if one recorder dies, the pair is stopped (never one coin writing alone) and retried
  after 30 s, 60 s, ... up to 5 min
- `KEEP_DAYS=21` deletes only archives older than 21 days
- below `MIN_FREE_MB=1024` free it **stops starting pairs** and says so in the journal;
  it never deletes a recent day to make room. A full disk must be fixed by finding the
  real consumer: `sudo du -xh / | sort -h | tail -20`

Disk budget: ~9 MB per day for the gzipped pair, ~130 MB for two weeks — the
recordings are never what fills a volume.

Where the files live. `REC_DIR` (the systemd drop-in) is `/data/jev-rec/rec`, on the
100 GB data volume in `/etc/fstab` (`nofail`), **not** the 8 GB root volume.
`data/rec` in the repo is a symlink to it, so every path in this runbook,
`scripts/slow_replay.py` and `deploy/check_recorder.sh` keep resolving. After a
fresh checkout, recreate the link and prove the volume is mounted:

```bash
sudo mkdir -p /data/jev-rec/rec && sudo chown ubuntu: /data/jev-rec/rec
ln -sfn /data/jev-rec/rec data/rec
readlink -f data/rec && findmnt /data     # a dangling link means /data is not mounted
```

A full `/` is the toolchain, not the recorder: `~/.nvm` 1.8 G, `~/.npm` 700 M,
`/var/lib/snapd` 423 M. `npm cache clean --force`,
`sudo snap set system refresh.retain=2` and `sudo journalctl --vacuum-size=50M` are
all safe. `deploy/check_recorder.sh` prints the free space of the *recording*
volume, so a full `/` no longer reads as a recorder fault.

Daily health check. It restarts the unit when it is down, or when it is active but no
record has been written for 5 minutes, and appends the state to
`data/recorder_health.log`:

```bash
chmod +x deploy/check_recorder.sh
crontab -e   # add:
30 22 * * * "/home/ubuntu/jav trading/jev-trader/deploy/check_recorder.sh" >> "/home/ubuntu/jav trading/jev-trader/data/recorder_health.log" 2>&1
```

Verify a recording by hand: `tail -c 300 data/rec/BTC_*.jsonl` should end with a record
whose `ts` is the current Unix time (`date +%s`).

Troubleshooting:

| Symptom | Do this |
|---|---|
| `No space left on device` in the journal | Check both filesystems: `df -h /` and `df -h data/rec`. If `/` is full the recorder is not the consumer — `sudo du -xh / \| sort -h \| tail -20` (usually `~/.nvm`, `~/.npm`, `/var/lib/snapd`); `npm cache clean --force` and `sudo journalctl --vacuum-size=50M` are safe. The loop resumes on its next retry and will not delete recent days. |
| Unit active, files not growing | `journalctl -u jev-recorder -n 50`; check the network; `sudo systemctl restart jev-recorder`. |
| Disk full, nothing to delete | Growing an EBS volume is online in AWS but **manual inside the guest**: `sudo growpart /dev/nvme0n1 1 && sudo resize2fs /dev/nvme0n1p1` — ext4 never auto-extends. Attaching a second volume does not enlarge `/`. Or lower `KEEP_DAYS`, never the current days. |
| Files exist but `data/rec` looks empty | `readlink -f data/rec` and `findmnt /data`: the symlink to `/data/jev-rec/rec` is missing, or the `nofail` data volume is not mounted and the writers landed somewhere unexpected. |
| Want other coins / a fixed length | `sudo systemctl edit jev-recorder`, add `Environment=COINS="AAVE BTC"` or `Environment=MINUTES=60`. |

## Secrets

- Keys live only in `.env` (git-ignored). `live` refuses to start unless it is `chmod 600`.
- `HL_PRIVATE_KEY` must be an **API wallet** key. It can trade but cannot withdraw.
  Never put the main wallet key here.
- The trading account must be used by this bot only: any order the bot didn't
  place is treated as an orphan and cancelled.

## Upgrading the Jev model

`JEV_MODEL` is pinned to a version (`jev-1.13.0`), not the `jev-latest` alias,
because the thresholds were tuned against that version's answers. When TypeSafe
ships a new version:

1. `uv run jev-trader models` shows the aliases; the new versioned ID is in the docs.
2. Re-run the backtest with the new version: set `JEV_MODEL=<new>` and delete
   `data/cache_jev_*.jsonl`, then `uv run python scripts/backtest.py <train> <test>`.
3. Compare `docs/backtest.md` with the old one: net $, markouts, calibration.
4. Switch `JEV_MODEL` in `.env` only if the new version is at least as good. Then restart.

The run summary's `model_mismatches` counts any answer from a model other than
the pinned one. It should always be 0.
