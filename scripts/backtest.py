"""Phase 3 backtest: tune on one recording, report on a held-out one.

0. Cache: ask Jev and the frontier LLM once per block of each recording
   (data/cache_<judge>_<recording>.jsonl, resumable). Every judge sees the
   same states; the threshold grid then replays the cache instead of paying again.
1. Tune: grid-search each baseline's policy thresholds and spread on TRAIN;
   keep the best net PnL among settings with enough fills.
2. Test: run every baseline on TEST with its tuned settings and write docs/backtest.md.

    uv run python scripts/backtest.py data/hl_SOL_60m.jsonl data/hl_SOL_test_4h.jsonl

Needs TYPESAFE_API_KEY (Jev) and OPENAI_API_KEY (LLM) in .env for step 0.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import date
from pathlib import Path

from jev_trader.backtest import HYPERLIQUID_COSTS, NO_LOSS_LIMITS, cache_judgments, run
from jev_trader.calibration.metrics import (
    brier_score,
    expected_calibration_error,
    load_pairs,
    log_loss,
)
from jev_trader.config import PolicyConfig, PricingConfig, Settings
from jev_trader.judgment.client import JevClient
from jev_trader.judgment.llm import LLMJudge

MIN_FILLS = 30  # fewer fills than this on TRAIN is noise, not evidence
HALF_SPREAD_BPS = (1.0, 5.0, 10.0)
TOXIC_PULL = (0.6, 0.8, 0.9, 1.01)  # 1.01 = never pull
QUOTE_WIDE_MIN = (1.0, 0.5, 0.0)  # 0.0 = quote in any environment
LIQUIDITY_WIDEN = (0.7, 1.01)
QUOTE_MIN_CONFIDENCE = (0.5, 0.7, 0.8, 0.9)  # below this, quote wide and small
NO_DEADLINE_S = 1e6  # the LLM is judged on answers alone; live it misses every block
LLM_MODEL = "gpt-5.4-mini"
# $ per 1M input / output tokens
PRICES = {"jev": (0.042, 0.0), "llm": (0.75, 4.50)}

Job = tuple[str, dict[str, float], str, str | None, str | None]


def cache_path(judge: str, recording: str) -> Path:
    return Path(f"data/cache_{judge}_{Path(recording).stem}.jsonl")


async def build_caches(recordings: list[str]) -> None:
    settings = Settings()
    async with JevClient(settings.model_copy(update={"decision_timeout_s": 10.0})) as jev:
        for recording in recordings:
            await cache_judgments(jev, recording, cache_path("jev", recording), concurrency=2)
    assert settings.openai_api_key is not None, "set OPENAI_API_KEY in .env"
    llm = LLMJudge(settings.openai_api_key.get_secret_value(), LLM_MODEL)
    for recording in recordings:
        await cache_judgments(llm, recording, cache_path("llm", recording), concurrency=16)


def settings_for(params: dict[str, float]) -> Settings:
    return Settings(
        decision_timeout_s=params.get("decision_timeout_s", Settings().decision_timeout_s),
        pricing=PricingConfig(min_half_spread_bps=params["half_spread_bps"]),
        policy=PolicyConfig(
            toxic_pull=params.get("toxic_pull", 0.6),
            quote_wide_min_score=params.get("quote_wide_min_score", 1.0),
            liquidity_widen=params.get("liquidity_widen", 0.7),
            quote_min_confidence=params.get("quote_min_confidence", 0.8),
        ),
        risk=NO_LOSS_LIMITS,
    )


def run_one(job: Job) -> dict:
    judge, params, source, log, cache = job
    log_path = Path(log) if log else None
    cache_file = Path(cache) if cache else None
    settings = settings_for(params)
    return asyncio.run(run(judge, settings, source, HYPERLIQUID_COSTS, log_path, cache_file))


def grid(always: bool, fixed: dict[str, float], gating: bool) -> list[dict[str, float]]:
    if always:
        return [{"half_spread_bps": h} for h in HALF_SPREAD_BPS]
    axes = {
        "half_spread_bps": HALF_SPREAD_BPS,
        "toxic_pull": TOXIC_PULL,
        "quote_wide_min_score": QUOTE_WIDE_MIN,
        "liquidity_widen": LIQUIDITY_WIDEN,
    }
    if gating:
        axes["quote_min_confidence"] = QUOTE_MIN_CONFIDENCE
    return [
        {**fixed, **dict(zip(axes, combo, strict=True))}
        for combo in itertools.product(*axes.values())
    ]


def net(summary: dict) -> float:
    return summary["equity"] - Settings().starting_cash


def tune(
    pool: ProcessPoolExecutor, judge: str, candidates: list[dict[str, float]], train: str
) -> tuple[dict[str, float], dict, bool]:
    """Best train net PnL among settings with enough fills.

    Returns (params, train summary, enough_fills). With no setting reaching
    MIN_FILLS it falls back to the best of all, and says so in the report.
    """
    cache = str(cache_path(CACHED[judge], train)) if judge in CACHED else None
    engine = "cached" if cache else judge
    jobs = [(engine, p, train, None, cache) for p in candidates]
    results = list(pool.map(run_one, jobs))
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


def cache_stats(judge: str, recordings: list[str], blocks: int) -> dict[str, float]:
    """Latency, miss rate and $ per 1M decisions from the cached API calls.

    Blocks with no saved answer (the call failed) count as missed.
    """
    rows = [
        json.loads(line)["judgments"]
        for r in dict.fromkeys(recordings)  # train == test in a dry run
        for line in cache_path(judge, r).read_text().splitlines()
    ]
    answered = [j for j in rows if j is not None]
    latencies = sorted(j["latency_ms"] for j in answered)
    deadline_ms = Settings().decision_timeout_s * 1000.0
    price_in, price_out = PRICES[judge]
    tokens_in = sum(j["input_tokens"] for j in answered) / len(answered)
    tokens_out = sum(j["output_tokens"] for j in answered) / len(answered)
    return {
        "calls": blocks,
        "p50": latencies[len(latencies) // 2],
        "p99": latencies[int(0.99 * len(latencies))],
        "missed": sum(x > deadline_ms for x in latencies) + blocks - len(answered),
        "cost_per_1m": tokens_in * price_in + tokens_out * price_out,
        "model": answered[0]["model"],
    }


def row(name: str, params: str, s: dict, cost: str) -> str:
    return (
        f"| {name} | {params} | {net(s):+.2f} | {s['sharpe']:.2f} | {s['sortino']:.2f}"
        f" | {s['max_drawdown'] * 100:.2f}% | {s['fills']} | {s['hit_rate']['10b']:.2f}"
        f" | {s['markout_bps']['0b']:+.2f} / {s['markout_bps']['10b']:+.2f}"
        f" | {s['fees']:.2f} | {s['coverage'] * 100:.1f}% | {cost} |"
    )


# baseline name -> cache it replays
CACHED = {"frontier LLM": "llm", "Jev": "jev", "Jev + confidence gating": "jev"}


def main() -> None:
    train, test = sys.argv[1], sys.argv[2]
    asyncio.run(build_caches([train, test]))

    default_rules = {"half_spread_bps": PricingConfig().min_half_spread_bps}
    grids = {
        "hand rules (tuned)": grid(False, {}, gating=False),
        "always quote (tuned spread)": grid(True, {}, gating=False),
        "frontier LLM": grid(
            False, {"quote_min_confidence": 0.0, "decision_timeout_s": NO_DEADLINE_S}, False
        ),
        "Jev": grid(False, {"quote_min_confidence": 0.0}, gating=False),
        "Jev + confidence gating": grid(False, {}, gating=True),
    }
    engines = {"hand rules (tuned)": "heuristic", "always quote (tuned spread)": "always"}
    with ProcessPoolExecutor() as pool:
        tuned = {name: tune(pool, engines.get(name, name), g, train) for name, g in grids.items()}
        runs = {"hand rules (default)": ("heuristic", default_rules)} | {
            name: (engines.get(name, name), params) for name, (params, _, _) in tuned.items()
        }
        jobs: list[Job] = []
        for name, (judge, params) in runs.items():
            cache = str(cache_path(CACHED[name], test)) if name in CACHED else None
            # P(up) calibration: hand rules, LLM, Jev (the gated run shares Jev's answers)
            log = {"hand rules (default)": "rules", "frontier LLM": "llm", "Jev": "jev"}.get(name)
            log_path = f"data/backtest_calibration_{log}.jsonl" if log else None
            jobs.append(("cached" if cache else judge, params, test, log_path, cache))
        results = dict(zip(runs, pool.map(run_one, jobs), strict=True))

    blocks = tuned["hand rules (tuned)"][1]["blocks"] + results["hand rules (default)"]["blocks"]
    stats = {judge: cache_stats(judge, [train, test], blocks) for judge in ("jev", "llm")}

    def cost(name: str) -> str:
        if name not in CACHED:
            return "$0"
        return f"${stats[CACHED[name]]['cost_per_1m']:.0f}"

    header = (
        "| baseline | settings | net $ | Sharpe | Sortino | max DD | fills | hit rate 10b"
        " | markout bps 0b / 10b | fees $ | coverage | cost / 1M decisions"
        " |\n" + "|---" * 12 + "|"
    )
    jev, llm = stats["jev"], stats["llm"]
    lines = [
        "# Backtest: four-way comparison",
        "",
        f"Generated {date.today()} by `scripts/backtest.py`.",
        "",
        f"- **Train (tuning):** `{train}` — {tuned['hand rules (tuned)'][1]['blocks']} blocks",
        f"- **Test (reported):** `{test}` — {results['hand rules (default)']['blocks']} blocks",
        f"- **Costs:** Hyperliquid — {HYPERLIQUID_COSTS.fee_bps} bps maker fee, no gas,"
        f" {HYPERLIQUID_COSTS.ack_ms:.0f} ms ack/cancel latency",
        f"- **Tuning rule:** best net PnL on train among settings with >= {MIN_FILLS} fills",
        "- **Judges:** every judge answered the same per-block states (from a hand-rules"
        " pass over each recording), cached once and replayed across the threshold grid."
        " Inventory pressure always comes from code, since inventory differs per setting.",
        f"- **Jev** (`{jev['model']}`): p50 {jev['p50']:.0f} ms, p99 {jev['p99']:.0f} ms;"
        f" {jev['missed']} of {jev['calls']} calls ({jev['missed'] / jev['calls']:.1%}) missed"
        f" the {Settings().decision_timeout_s * 1000:.0f} ms deadline or failed and replay as"
        " holds, exactly as live.",
        f"- **Frontier LLM** (`{llm['model']}`, no reasoning): p50 {llm['p50']:.0f} ms,"
        f" p99 {llm['p99']:.0f} ms — it would miss essentially every 300 ms block, so its row"
        " **ignores latency** and scores the answers alone. Live, it could not quote at all.",
        "- **Jev** quotes whenever Jev's environment score clears the bar (no confidence"
        " check). **Jev + confidence gating** also requires Jev's confidence to clear a"
        " tuned threshold; below it, the policy quotes wide at half size.",
        "- Hand rules have fixed 0.5 confidence, so confidence gating only applies to Jev.",
        "",
        "## Results on the held-out test set",
        "",
        header,
    ]
    for name, (_, params) in runs.items():
        shown = ", ".join(
            f"{k}={v:g}" for k, v in params.items() if k != "decision_timeout_s"
        )
        lines.append(row(name, shown, results[name], cost(name)))

    def fit(name: str) -> str:
        _, summary, ok = tuned[name]
        note = "" if ok else f" (no setting reached {MIN_FILLS} fills; best of all shown)"
        return f"- {name}: net {net(summary):+.2f}, fills {summary['fills']}{note}"

    lines += [
        "",
        "## Direction calibration (P(up) over 10 blocks, test set)",
        "",
        f"- hand rules: {calibration(Path('data/backtest_calibration_rules.jsonl'))}",
        f"- frontier LLM: {calibration(Path('data/backtest_calibration_llm.jsonl'))}",
        f"- Jev: {calibration(Path('data/backtest_calibration_jev.jsonl'))}",
        "",
        "## Train-set fit (for overfitting checks)",
        "",
        *(fit(name) for name in tuned),
        "",
    ]
    out = Path("docs/backtest.md")
    out.parent.mkdir(exist_ok=True)
    out.write_text("\n".join(lines))
    print("\n".join(lines))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
