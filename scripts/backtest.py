"""Phase 3 backtest: tune on one recording, report on a held-out one.

1. Tune: grid-search the hand rules' policy thresholds (and each baseline's
   spread) on TRAIN; keep the best net PnL among settings with enough fills.
2. Test: run every baseline on TEST with its tuned settings, plus the default
   hand rules, and write docs/backtest.md.

    uv run python scripts/backtest.py data/hl_SOL_60m.jsonl data/hl_SOL_test_4h.jsonl

Jev and a frontier-LLM layer need API keys; their rows stay pending until then.
"""

from __future__ import annotations

import asyncio
import itertools
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import date
from pathlib import Path

from jev_trader.backtest import HYPERLIQUID_COSTS, NO_LOSS_LIMITS, run
from jev_trader.calibration.metrics import (
    brier_score,
    expected_calibration_error,
    load_pairs,
    log_loss,
)
from jev_trader.config import PolicyConfig, PricingConfig, Settings

MIN_FILLS = 30  # fewer fills than this on TRAIN is noise, not evidence
HALF_SPREAD_BPS = (1.0, 5.0, 10.0)
TOXIC_PULL = (0.6, 0.8, 0.9, 1.01)  # 1.01 = never pull
QUOTE_WIDE_MIN = (1.0, 0.5, 0.0)  # 0.0 = quote in any environment
LIQUIDITY_WIDEN = (0.7, 1.01)
PENDING = ("frontier LLM", "Jev", "Jev + confidence gating")


def settings_for(params: dict[str, float]) -> Settings:
    return Settings(
        pricing=PricingConfig(min_half_spread_bps=params["half_spread_bps"]),
        policy=PolicyConfig(
            toxic_pull=params.get("toxic_pull", 0.6),
            quote_wide_min_score=params.get("quote_wide_min_score", 1.0),
            liquidity_widen=params.get("liquidity_widen", 0.7),
        ),
        risk=NO_LOSS_LIMITS,
    )


def run_one(job: tuple[str, dict[str, float], str, str | None]) -> dict:
    judge, params, source, log = job
    log_path = Path(log) if log else None
    return asyncio.run(run(judge, settings_for(params), source, HYPERLIQUID_COSTS, log_path))


def grid(judge: str) -> list[dict[str, float]]:
    if judge == "always":
        return [{"half_spread_bps": h} for h in HALF_SPREAD_BPS]
    keys = ("half_spread_bps", "toxic_pull", "quote_wide_min_score", "liquidity_widen")
    values = (HALF_SPREAD_BPS, TOXIC_PULL, QUOTE_WIDE_MIN, LIQUIDITY_WIDEN)
    return [dict(zip(keys, combo, strict=True)) for combo in itertools.product(*values)]


def net(summary: dict) -> float:
    return summary["equity"] - Settings().starting_cash


def tune(
    pool: ProcessPoolExecutor, judge: str, train: str
) -> tuple[dict[str, float], dict, bool]:
    """Best train net PnL among settings with enough fills.

    Returns (params, train summary, enough_fills). With no setting reaching
    MIN_FILLS it falls back to the best of all, and says so in the report.
    """
    candidates = grid(judge)
    results = list(pool.map(run_one, [(judge, p, train, None) for p in candidates]))
    scored = [(net(r), p, r) for p, r in zip(candidates, results, strict=True)]
    eligible = [s for s in scored if s[2]["fills"] >= MIN_FILLS]
    best = max(eligible or scored, key=lambda s: s[0])
    return best[1], best[2], bool(eligible)


def calibration(log: Path) -> str:
    probabilities, outcomes = load_pairs(log)
    if len(probabilities) < 10:
        return "n/a"
    return (
        f"Brier {brier_score(probabilities, outcomes):.4f}, "
        f"log loss {log_loss(probabilities, outcomes):.4f}, "
        f"ECE {expected_calibration_error(probabilities, outcomes):.4f} "
        f"({len(probabilities)} pairs)"
    )


def row(name: str, params: str, s: dict) -> str:
    return (
        f"| {name} | {params} | {net(s):+.2f} | {s['sharpe']:.2f} | {s['sortino']:.2f}"
        f" | {s['max_drawdown'] * 100:.2f}% | {s['fills']} | {s['hit_rate']['10b']:.2f}"
        f" | {s['markout_bps']['0b']:+.2f} / {s['markout_bps']['10b']:+.2f}"
        f" | {s['fees']:.2f} | {s['coverage'] * 100:.1f}% | $0 |"
    )


def main() -> None:
    train, test = sys.argv[1], sys.argv[2]
    Path("data").mkdir(exist_ok=True)
    with ProcessPoolExecutor() as pool:
        tuned_rules, train_rules, rules_ok = tune(pool, "heuristic", train)
        tuned_always, train_always, always_ok = tune(pool, "always", train)
        default_rules = {"half_spread_bps": PricingConfig().min_half_spread_bps}
        runs = {
            "hand rules (default)": ("heuristic", default_rules),
            "hand rules (tuned)": ("heuristic", tuned_rules),
            "always quote (tuned spread)": ("always", tuned_always),
        }
        log = "data/backtest_calibration.jsonl"
        # only the first run logs: every baseline shares the hand rules' P(up)
        jobs = [
            (judge, params, test, log if i == 0 else None)
            for i, (judge, params) in enumerate(runs.values())
        ]
        results = dict(zip(runs, pool.map(run_one, jobs), strict=True))

    header = (
        "| baseline | settings | net $ | Sharpe | Sortino | max DD | fills | hit rate 10b"
        " | markout bps 0b / 10b | fees $ | coverage | cost / 1M decisions"
        " |\n" + "|---" * 12 + "|"
    )
    lines = [
        "# Backtest: four-way comparison",
        "",
        f"Generated {date.today()} by `scripts/backtest.py`.",
        "",
        f"- **Train (tuning):** `{train}` — {train_rules['blocks']} blocks",
        f"- **Test (reported):** `{test}` — {next(iter(results.values()))['blocks']} blocks",
        f"- **Costs:** Hyperliquid — {HYPERLIQUID_COSTS.fee_bps} bps maker fee, no gas,"
        f" {HYPERLIQUID_COSTS.ack_ms:.0f} ms ack/cancel latency",
        f"- **Tuning rule:** best net PnL on train among settings with >= {MIN_FILLS} fills",
        "- Hand rules have fixed 0.5 confidence, so confidence gating only applies to Jev.",
        "",
        "## Results on the held-out test set",
        "",
        header,
    ]
    for name, (_, params) in runs.items():
        shown = ", ".join(f"{k}={v:g}" for k, v in params.items())
        lines.append(row(name, shown, results[name]))
    for name in PENDING:
        lines.append(f"| {name} | pending: needs API key |" + " |" * 10)
    def fit(summary: dict, ok: bool) -> str:
        note = "" if ok else f" (no setting reached {MIN_FILLS} fills; best of all shown)"
        return f"net {net(summary):+.2f}, fills {summary['fills']}{note}"

    lines += [
        "",
        "## Direction calibration (hand rules' P(up), test set)",
        "",
        calibration(Path(log)),
        "",
        "## Train-set fit (for overfitting checks)",
        "",
        f"- hand rules (tuned): {fit(train_rules, rules_ok)}",
        f"- always quote (tuned): {fit(train_always, always_ok)}",
        "",
    ]
    out = Path("docs/backtest.md")
    out.parent.mkdir(exist_ok=True)
    out.write_text("\n".join(lines))
    print("\n".join(lines))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
