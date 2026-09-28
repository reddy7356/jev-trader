# Jev Trader — Build Plan

A 24/7 market-making system built around **Jev** (TypeSafe AI's System One decision
model). The design principle that carries the whole build:

> **Jev should not own the trading system. It should own selected judgments inside it.**
>
> Code calculates the state. Jev interprets the state. Code applies policy.
> Execution places the order. The hard risk layer always wins.

The loop is nine stages, each a tested module with its own failure modes:

```
Block event (WebSocket newHeads + polling backstop)
  -> read L2 book (best bid/ask/depth)
  -> compute state snapshot (deterministic, dense, < 400 tokens)
  -> fire Jev battery (six judgments, one call, one latency)
  -> policy engine composes action (thresholds live in code)
  -> A-S pricing computes reservation + spread
  -> risk engine checks hard limits (absolute veto)
  -> cancel old quotes, post new post-only orders
  -> log snapshot + decision + outcome (calibration triples)
  -> next block
```

## Status legend

- [x] done in this repo
- [ ] planned

---

## Phase 0 — Foundation (current)

Scaffold the full loop against a paper venue so every interface is exercised
before real money or a real venue touches it.

- [x] Repo, `uv` toolchain, ruff + pytest + CI
- [x] Config via env (`Settings`, nested risk/policy/pricing configs)
- [x] Core types (`JudgmentSet`, `Action`, `Order`, `Fill`, `PnL`)
- [x] State engine: `OrderBook` math (mid, microprice, spread bps, depth, imbalance)
- [x] Feature engine: returns 1m/5m/30m, realized vol, vol ratio, flow intensity
- [x] State snapshot builder (dense numeric dict for Jev)
- [x] Judgment battery: 6 atomic questions in one call (2 Choice, 2 Noul, 2 Score)
- [x] `JevClient` (async SDK wrapper, pinned model, per-call latency + request id)
- [x] `HeuristicJudge` deterministic fallback (used when Jev is unavailable)
- [x] Policy engine with code-owned thresholds + fractional Kelly sizing
- [x] Avellaneda-Stoikov pricing (reservation price + optimal half spread + skew)
- [x] Risk engine: hard-coded vetoes, precedence `KILL > HOLD > REDUCE_ONLY > OK`
- [x] Paper venue: post-only order lifecycle, trade-through fills, inventory + PnL
- [x] Synthetic block feed (seeded, deterministic, realtime or accelerated)
- [x] 24/7 loop with block-deadline rule and fallback ladder
- [x] Calibration logging (decision + outcome JSONL triples)
- [x] Calibration metrics: Brier, log loss, ECE, reliability curve, Platt scaling
- [x] CLI: `paper`, `calibrate`, `models`
- [x] Test suite for every deterministic module

**Acceptance:** `uv run pytest` green, `uv run jev-trader paper --blocks 2000` runs
end to end without a TypeSafe key (fallback mode) and writes calibration triples.

## Phase 1 — Live Jev hardening

- [x] Apply for a TypeSafe key, run `paper` in live-judgment mode (heuristics off)
      — 10,000 blocks `--live-only`: 0 errors, 0 kills, 419 deadline misses (4.2%) held
- [x] Measure real decision latency distribution per block; tune `decision_timeout_s`
      — uncapped: p50 175, p95 249, p99 321, max 391 ms. Kept 250 ms: the 300 ms block
        needs ~50 ms to send orders; ~5% of blocks hold. Error kill limit now counts
        *consecutive* errors (1 transient 503 per ~1000 calls would have killed a 24/7 run)
- [x] Log and assert the returned `model` matches the pinned version (silent-upgrade guard)
      — pinned `jev-1.13.0` (not the `jev-latest` alias); 0 mismatches in 10k blocks
- [x] Token accounting: cost per 1M decisions from `usage`, per the $10–25/month claim
      — $0.042 per 1M input tokens, output free; ~1,130 tokens/decision = **$40.94 per 1M
        decisions**. 24/7 at 300 ms blocks = 8.6M decisions/month ≈ **$350/month**, not $10–25
- [x] Verify parallel-battery economics: 6 questions, single-question latency
      — 100 calls each: 1 question p50 167 ms / 665 tokens; 6 questions p50 154 ms / 956
        tokens. Same latency, 1.44x the tokens (6 separate calls would be ~4.2x)
- [x] Retry/timeout policy review: never retry past the block deadline
      — `max_retries=0`, SDK timeout = 0.8 × `decision_timeout_s`, loop holds past deadline

**Acceptance:** 10k blocks with live Jev, zero stale-state quotes, latency p99
inside the block budget, cost per block measured.

**Result (2026-09-28): met.** 10,000 blocks, loop-measured p99 254 ms (deadline-capped)
inside the 300 ms block; missed deadlines hold, and a hold cancels resting quotes, so no
quote rests on stale state; $0.39 for the run.

## Phase 2 — Realistic simulation

- [x] Queue-position fill model (trade-through fills outright; touch fills only after the queue ahead is consumed)
- [x] Latency simulation: order ack + cancel/replace round trip (`--ack-ms`, `--cancel-ms`)
- [x] Gas / fee model for cancel-replace every block (the "gas honesty check")
      — `--gas-place`, `--gas-cancel`; 2000 blocks = ~1.5k txs; realized PnL is negative even at $0 gas
- [x] Historical L2 replay feed (recorded book + trades)
      — `jev-trader record --coin SOL` (Hyperliquid, public) + `paper --replay`.
        60 min SOL (6.6k books, 2.5k trades), 1.5 bps maker fee, no gas:
        heuristic judge never quotes (thresholds tuned on synthetic data);
        always-quote at 1-5 bps: markout at fill ~-1 bps, fees 1.5 bps -> net loss;
        10 bps: +$2..6 on 2-17 fills (noise, not edge); >=20 bps: no fills.
- [x] Adverse-selection metrics: markout at 1/5/10/30 blocks after each fill
      — `markout_bps`/`markout_usd` in the summary; baseline is ~-7 bps already at fill time (stale quotes)
- [x] Spread/time-in-book tuning: widen, rest longer, or find directional edge
      — `scripts/sweep.py`; quotes now rest until the target moves > `requote_ticks`.
        Result (synthetic feed, 150ms latency, $0.01 gas, 3 seeds x 10k blocks):
        every setting loses; best is 40 bps half-spread + requote 5 ticks at -$189.
        Markout at fill is -12..-16 bps everywhere: synthetic trades print only at the
        touch, so we fill only when price runs through us. Verdict needs item 4 (real data).

**Acceptance:** simulated cancel-replace cost model reproduces the structural-loss
result from the article's critique; a configuration with positive net edge exists
in simulation or the strategy is rejected.

**Result (2026-09-26):** no configuration shows positive net edge. Synthetic: every
setting loses (-12..-16 bps markout at fill). Real SOL replay: adverse selection is
small (~-1 bps) but the 1.5 bps maker fee exceeds the spread captured at the touch.
Rejected as a plain quoting strategy at base-tier fees; the open question for Phase 3
is whether judgment (Jev or retuned rules) can skip the adverse fills.

## Phase 3 — Calibration & backtest harness

- [x] Four baselines on identical data: hand rules, frontier LLM layer, Jev, Jev + confidence gating
      — per-block answer cache replayed across the threshold grid; LLM = gpt-5.4-mini.
        Test window too quiet: 0 fills for every baseline (PnL inconclusive); LLM test
        cache incomplete (OpenAI credits ran out at 944 of 3,137 blocks)
- [x] Metrics: Sharpe, Sortino, max drawdown, hit rate, slippage, adverse selection,
      cost per 1M decisions, coverage
      — in every run summary; slippage is 0 for post-only quotes (markouts carry it)
- [x] Reliability curves from logged triples; Brier / log loss / ECE
      — `jev-trader calibrate`; backtest report includes the hand rules' P(up) calibration
- [x] Platt scaling in the policy layer when the reliability curve bends
      — not pursued: P(up) carries no tradable edge (Phase 6 direction check), so there is
        nothing worth sizing on. `jev-trader calibrate` still reports the Platt fit
- [x] Threshold sweep: one threshold per action, scaled to cost of being wrong
      — backtest tunes toxic_pull, quote_wide_min_score, liquidity_widen, spread on train
- [x] Answer the real research question: does abstaining when uncertain improve the book?
      — no evidence either way: gated and ungated Jev tuned to near-identical settings and
        behaved identically on test. Needs a busier test set to answer

**Acceptance:** published backtest report in `docs/` with the four-way comparison.

**Result (2026-09-28):** `docs/backtest.md`. Decisive: latency (Jev 169 ms vs LLM 1.6 s, so
the LLM could never quote live) and cost ($39 vs $1,272 per 1M decisions). Calibration:
LLM Brier 0.135 (137 pairs) < Jev 0.260 < hand rules 0.353. PnL: inconclusive (0 fills).
Next: record a busier 4h test set and re-run.

## Phase 4 — Live venue adapter

- [x] Venue adapter interface (`VenueAdapter` protocol) hardened for async IO
      — `await venue.sync()` each block; getters answer from local state
- [x] Target selection: Monad/Kuru MON-USDC, Hyperliquid, or Solana DEX
      — Hyperliquid (public data, no gas); testnet only in this phase
- [x] WebSocket `newHeads` block events + polling backstop
      — HL has no newHeads: `fast` l2Book WS is the block clock; REST reconcile every 5s
- [x] Post-only limit order submission, nonce management, tx confirmation tracking
      — `execution/hyperliquid.py`: ALO orders via official SDK, serialized calls (unique
        ms nonces), every response status checked; unit-tested against a fake exchange
- [x] Balance/inventory reconciliation against on-chain state
      — position + account value every block; open orders + fills every 5s, deduped
- [ ] Kill switch: cancel-all + flatten path tested on testnet
      — built (`jev-trader kill`, loop KILL path, scheduleCancel dead-man's switch);
        needs a testnet run: HL testnet faucet requires a prior mainnet deposit

**Acceptance:** 24h testnet run, no orphan orders, no stale quotes, kill switch
demonstrated live.

## Phase 5 — Production 24/7

- [x] Alerting (latency, error rate, drawdown, Jev unavailability)
      — `loop/alerts.py`: ~1 min window, log + optional Slack/Discord `ALERT_WEBHOOK_URL`
- [x] Dashboard: equity, inventory, decision latency, action mix, calibration drift
      — `jev-trader dashboard --every 30`: self-contained auto-reloading HTML
- [x] Secret management, process supervision, restart-with-state recovery
      — `.env` must be `chmod 600`; launchd plist restarts on crash only; loss baselines and
        the kill persist in `data/live_risk.json` (sticky kill until `jev-trader rearm`);
        orphan orders from a crashed run are cancelled on the first sync
- [ ] Canary deployment: tiny size, real venue, watched
      — not started: needs real money on mainnet, and Phase 4's testnet run comes first
- [x] Runbook: what to do when the fallback ladder fires — `docs/runbook.md`

**Acceptance:** one week unattended with the fallback ladder exercised at least
once and handled correctly.

## Phase 6 — Research

- [x] Directional edge from the battery (does `direction` add edge after costs?)
      — no: `scripts/direction_edge.py` finds Jev's lean worth +0.19 bps per 10 blocks
        (t≈12, real) against a 3 bps maker round trip (LLM +0.28, hand rules +0.07)
- [ ] Multi-venue quoting and cross-venue divergence feature (`CROSS` block)
- [ ] Fractional Kelly sizing gated on verified calibration
      — deprioritized: calibration is not verified and P(up) has no edge to size
- [x] Model upgrade procedure: recalibrate thresholds on version change
      — pinned version + mismatch counter; procedure in `docs/runbook.md`
- [ ] Prediction markets (200–500 bps spreads) as a second target venue

---

## Non-goals (the honest contract)

Jev cannot: generate text or design the strategy, chain dependent judgments in one
call, compete in the microsecond lane, replace market data infra, numerical
computation, exchange connectivity, or hard risk controls, or turn a losing
strategy into a winning one. Jev makes decisions cheap and fast. Edge is still
our job.
