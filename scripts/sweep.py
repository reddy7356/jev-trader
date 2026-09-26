"""Phase 2 tuning sweep: does any spread / requote setting make money after costs?

Runs the paper loop over a grid of judges x settings x seeds, with realistic
latency and gas, and prints net PnL per setting. Pass a recorded file to replay
real market data instead (Hyperliquid: no gas, 1.5 bps maker fee).

    uv run python scripts/sweep.py
    uv run python scripts/sweep.py data/hl_SOL_60m.jsonl
"""

from __future__ import annotations

import asyncio
import itertools
import sys
from concurrent.futures import ProcessPoolExecutor

from jev_trader.backtest import HYPERLIQUID_COSTS, NO_LOSS_LIMITS, SYNTHETIC_COSTS, run
from jev_trader.config import PricingConfig, Settings

REPLAY = sys.argv[1] if len(sys.argv) > 1 else None
SOURCES: tuple[str | int, ...] = (REPLAY,) if REPLAY else (7, 11, 13)
COSTS = HYPERLIQUID_COSTS if REPLAY else SYNTHETIC_COSTS
JUDGES = ("heuristic", "always")
HALF_SPREAD_BPS = (1.0, 5.0, 10.0, 20.0, 40.0)
REQUOTE_TICKS = (0.0, 2.0, 5.0)


def run_one(args: tuple[str, float, float, str | int]) -> tuple[str, float, float, str | int, dict]:
    judge, half, requote, source = args
    settings = Settings(
        pricing=PricingConfig(min_half_spread_bps=half, requote_ticks=requote),
        risk=NO_LOSS_LIMITS,
    )
    return (*args, asyncio.run(run(judge, settings, source, COSTS)))


def main() -> None:
    grid = list(itertools.product(JUDGES, HALF_SPREAD_BPS, REQUOTE_TICKS, SOURCES))
    with ProcessPoolExecutor() as pool:
        results = list(pool.map(run_one, grid))

    feed = f"replay {REPLAY}" if REPLAY else f"synthetic 10000 blocks x seeds {SOURCES}"
    print(
        f"{feed}, latency {COSTS.ack_ms:.0f}ms, gas ${COSTS.gas_per_tx}/tx,"
        f" fee {COSTS.fee_bps} bps"
    )
    print(
        "judge     half_bps requote blocks  fills  markout0_bps  realized    fees     gas"
        "   net(avg)  net per seed"
    )
    for judge, half, requote in itertools.product(JUDGES, HALF_SPREAD_BPS, REQUOTE_TICKS):
        rows = [r for j, h, q, _, r in results if (j, h, q) == (judge, half, requote)]
        nets = [r["equity"] - Settings().starting_cash for r in rows]
        keys = ("blocks", "fills", "realized", "fees", "gas")
        avg = {k: sum(r[k] for r in rows) / len(rows) for k in keys}
        markout0 = sum(r["markout_bps"]["0b"] for r in rows) / len(rows)
        print(
            f"{judge:9s} {half:8.1f} {requote:7.0f} {avg['blocks']:6.0f} {avg['fills']:6.0f}"
            f" {markout0:13.2f}"
            f" {avg['realized']:9.2f} {avg['fees']:7.2f} {avg['gas']:7.2f}"
            f" {sum(nets) / len(nets):9.2f}  {' '.join(f'{n:8.2f}' for n in nets)}"
        )


if __name__ == "__main__":
    main()
