# Runbook: running jev-trader live (Hyperliquid TESTNET)

What to do, in order, when something happens. Keep this next to the dashboard.

## The fallback ladder (what the bot does on its own)

Every block (~300 ms) the loop asks Jev for judgments. When that goes wrong, it
steps down this ladder by itself, and each step is safe:

| Situation | What the bot does | Counts as |
|---|---|---|
| Jev answers inside 250 ms | quote per policy | normal |
| Jev slower than 250 ms (~5% of blocks) | **hold**: keep resting quotes, place nothing new | timeout |
| Jev returns an error | use the hand-rules judge for this block | fallback |
| more than 5 Jev errors **in a row** (~2 s outage) | **KILL**: cancel all, close the position, stop | kill |
| market data older than 2 s | **hold** | - |
| position > max, or held longer than 15 min | **reduce only**: quote only the side that shrinks it, half size | - |
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
