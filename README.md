# jev-trader

A 24/7 market-making trading system built around [Jev](https://typesafe.ai), TypeSafe AI's
System One decision model. Jev returns calibrated, typed decisions in ~70–500 ms — inside a
300 ms block budget. This repo is the system that calls it: state engine, judgment battery,
policy engine, A-S pricing, hard risk vetoes, execution, and the calibration loop.

> **One principle carries the whole build:** Jev should not own the trading system.
> It should own selected judgments inside it. Code calculates the state. Jev interprets
> the state. Code applies policy. Execution places the order.

## Architecture

```
MARKET DATA
     |
FEATURE ENGINE                      deterministic, pure code
     |
STATE SNAPSHOT  (< 400 tokens)
     |
JEV  — one call, six atomic judgments
  regime | direction | toxic_flow | liquidity_stressed
  quote_environment | inventory_pressure
     |
PROBABILITY VECTOR (typed answers + confidence)
     |
POLICY ENGINE                       thresholds live in code
     |
  HARD RISK RULES  ------------->   absolute veto, never delegated
     |
EXECUTION (post-only quotes)
     |
CALIBRATION LOG (state, decision, outcome triples)
```

Everything computable stays in code (mid, microprice, spread, imbalance, realized vol,
inventory, drawdown, queue position). Everything that is a judgment goes to Jev.
Atomic questions, composed in code — change a coefficient, not a prompt.

## Quickstart

```bash
uv sync

# Run the paper loop in fallback mode (no API key needed)
uv run jev-trader paper --blocks 2000

# With live Jev judgments
export TYPESAFE_API_KEY="..."
uv run jev-trader paper --blocks 10000

# Realtime pacing (300 ms blocks) instead of accelerated simulation
uv run jev-trader paper --realtime

# Calibration report from the logged triples
uv run jev-trader calibrate

# List available models
uv run jev-trader models
```

Paper runs are deterministic for a given `--seed` and write JSONL calibration triples to
`data/calibration.jsonl` by default.

## Layout

| Module | Responsibility |
| --- | --- |
| `state/` | L2 book math, feature engine, dense snapshot builder |
| `judgment/` | Six-question battery, async Jev client, deterministic fallback judge |
| `policy/` | Action composition (thresholds in code), fractional Kelly sizing |
| `pricing/` | Avellaneda-Stoikov reservation price, half spread, inventory skew |
| `risk/` | Hard-coded limits, absolute veto, kill switch |
| `execution/` | `VenueAdapter` protocol, paper venue with post-only lifecycle + fills |
| `loop/` | Nine-stage loop, block-deadline rule, fallback ladder |
| `calibration/` | JSONL triple logging, Brier / log loss / ECE / reliability / Platt |

## Fallback ladder

The system is autonomous only if it degrades in order:

```
healthy + high confidence  -> normal operation
healthy + low confidence   -> reduce size
late past block deadline   -> hold, never quote stale state
Jev unavailable            -> deterministic fallback judgments only
hard limit breached        -> kill switch, flatten, stop
```

## Configuration

All config is env-driven (`TYPESAFE_API_KEY`, `JEV_*`). See `.env.example` for the full set.
Risk limits are hard-coded vetoes and are never delegated to the model.

## Calibration

Every block logs the triple `(state, decision, outcome)`. `jev-trader calibrate` reports
Brier score, log loss, expected calibration error, and a reliability curve, and fits Platt
scaling so policy thresholds can be recalibrated on your venue's data — RLCD calibrates
against TypeSafe's distribution, not yours.

## Roadmap

See [PLAN.md](PLAN.md). Current status: Phase 0 (foundation + paper loop) complete.

## Disclaimer

This software is for research and educational purposes. It is not financial advice.
Trading involves substantial risk of loss. Run in paper mode and testnet before risking
capital, and never disable the risk engine.
