# Backtest: four-way comparison

Generated 2026-09-28 by `scripts/backtest.py`.

- **Train (tuning):** `data/hl_SOL_60m.jsonl` — 6618 blocks
- **Test (reported):** `data/hl_SOL_test_4h.jsonl` — 3137 blocks
- **Costs:** Hyperliquid — 1.5 bps maker fee, no gas, 150 ms ack/cancel latency
- **Tuning rule:** best net PnL on train among settings with >= 30 fills
- **Judges:** every judge answered the same per-block states (from a hand-rules pass over each recording), cached once and replayed across the threshold grid. Inventory pressure always comes from code, since inventory differs per setting.
- **Jev** (`jev-1.13.0`): p50 169 ms, p99 310 ms; 308 of 9755 calls (3.2%) missed the 250 ms deadline or failed and replay as holds, exactly as live.
- **Frontier LLM** (`gpt-5.4-mini-2026-03-17`, no reasoning): p50 1594 ms, p99 2432 ms — it would miss essentially every 300 ms block, so its row **ignores latency** and scores the answers alone. Live, it could not quote at all.
- **Jev** quotes whenever Jev's environment score clears the bar (no confidence check). **Jev + confidence gating** also requires Jev's confidence to clear a tuned threshold; below it, the policy quotes wide at half size.
- Hand rules have fixed 0.5 confidence, so confidence gating only applies to Jev.

## Results on the held-out test set

| baseline | settings | net $ | Sharpe | Sortino | max DD | fills | hit rate 10b | markout bps 0b / 10b | fees $ | coverage | cost / 1M decisions |
|---|---|---|---|---|---|---|---|---|---|---|---|
| hand rules (default) | half_spread_bps=1 | +0.00 | 0.00 | 0.00 | 0.00% | 0 | 0.00 | +0.00 / +0.00 | 0.00 | 0.0% | $0 |
| hand rules (tuned) | half_spread_bps=1, toxic_pull=1.01, quote_wide_min_score=0, liquidity_widen=0.7 | +0.00 | 0.00 | 0.00 | 0.00% | 0 | 0.00 | +0.00 / +0.00 | 0.00 | 100.0% | $0 |
| always quote (tuned spread) | half_spread_bps=1 | +0.00 | 0.00 | 0.00 | 0.00% | 0 | 0.00 | +0.00 / +0.00 | 0.00 | 100.0% | $0 |
| frontier LLM | quote_min_confidence=0, half_spread_bps=5, toxic_pull=0.6, quote_wide_min_score=1, liquidity_widen=0.7 | +0.00 | 0.00 | 0.00 | 0.00% | 0 | 0.00 | +0.00 / +0.00 | 0.00 | 20.8% | $1272 |
| Jev | quote_min_confidence=0, half_spread_bps=1, toxic_pull=0.6, quote_wide_min_score=0.5, liquidity_widen=0.7 | +0.00 | 0.00 | 0.00 | 0.00% | 0 | 0.00 | +0.00 / +0.00 | 0.00 | 79.4% | $39 |
| Jev + confidence gating | half_spread_bps=1, toxic_pull=0.6, quote_wide_min_score=0.5, liquidity_widen=0.7, quote_min_confidence=0.5 | +0.00 | 0.00 | 0.00 | 0.00% | 0 | 0.00 | +0.00 / +0.00 | 0.00 | 79.4% | $39 |

## Direction calibration (P(up) over 10 blocks, test set)

- hand rules: Brier 0.3530, log loss 1.1829, ECE 0.3722 (414 pairs)
- frontier LLM: Brier 0.1354, log loss 0.4445, ECE 0.1948 (137 pairs)
- Jev: Brier 0.2603, log loss 0.8040, ECE 0.2279 (399 pairs)

## Train-set fit (for overfitting checks)

- hand rules (tuned): net +4.90, fills 25 (no setting reached 30 fills; best of all shown)
- always quote (tuned spread): net -9.54, fills 68
- frontier LLM: net +5.51, fills 11 (no setting reached 30 fills; best of all shown)
- Jev: net +5.17, fills 24 (no setting reached 30 fills; best of all shown)
- Jev + confidence gating: net +5.08, fills 23 (no setting reached 30 fills; best of all shown)
## Verdict (hand-written, 2026-09-28)

**The PnL comparison is inconclusive.** The test recording turned out to be 28.5
quiet minutes (SOL ranged $120.85–121.10, 660 trades). No trade printed through
any baseline's quote, and the 3 trades at our price always had queue ahead of us,
so **every baseline got 0 fills** and $0 net. That is the fill model working, not a
bug. The train set was barely better: no judge-based setting reached 30 fills.
Tuning on these fill counts is noise.

**LLM row is incomplete.** The OpenAI account ran out of credits partway through
the test set: the LLM answered all 6,618 train blocks but only 944 of 3,137 test
blocks. The rest replay as holds, hence its 20.8% coverage. Its calibration
numbers cover 137 labeled pairs, versus ~400 for the others.

What the data *does* show:

- **Latency is the decisive difference.** Jev answers in 169 ms (p50), fitting a
  300 ms block. The LLM takes 1.6 s (p50), so live it would miss every block and never quote.
- **Cost:** Jev is $39 per 1M decisions; the LLM is $1,272 (~33× more).
- **Direction calibration:** on its 137 pairs the LLM's P(up) was better
  calibrated than Jev's (Brier 0.135 vs 0.260). Jev beat the hand rules (0.353).
  Neither P(up) is usable as-is (see the direction-edge table below).
- **Does abstaining when uncertain help?** No evidence either way. Jev with and
  without confidence gating tuned to nearly identical settings (train +5.08 vs
  +5.17 on 23–24 fills) and identical test behavior.

### Direction edge after costs (Phase 6, `scripts/direction_edge.py`)

Trade the lean of P(up) − P(down) for 10 blocks on the test set:

| judge | lean > | trades | mean bps captured | t | net of 3 bps maker round trip |
|---|---|---|---|---|---|
| hand rules | 0.1 | 2760 | +0.07 | +4.7 | −2.93 |
| frontier LLM | 0.1 | 802 | +0.28 | +9.4 | −2.72 |
| Jev | 0.1 | 2635 | +0.19 | +11.8 | −2.81 |

The direction signals are real (t is inflated up to ~3× by overlapping windows,
and is still significant) but 10–40× smaller than the cheapest round trip. **The
direction answer does not add tradable edge at a 10-block horizon.** Sizing on
P(up) (fractional Kelly, Platt scaling) is therefore not worth wiring in.

**To make the PnL comparison meaningful:** record a longer, busier test set
(`jev-trader record --minutes 240` during US hours), then re-run
`scripts/backtest.py`. Jev re-caching costs about $0.04 per 1,000 blocks. The LLM
row needs OpenAI credits (~$1.30 per 1,000 blocks).
