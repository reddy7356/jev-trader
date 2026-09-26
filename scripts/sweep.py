"""Phase 2 tuning sweep: does any spread / requote setting make money after costs?

Runs the paper loop over a grid of judges x settings x seeds,
with realistic latency and gas, and prints net PnL per setting. Pass a recorded
file to replay real market data instead (Hyperliquid: no gas, 1.5 bps maker fee).

    uv run python scripts/sweep.py
    uv run python scripts/sweep.py data/hl_SOL_60m.jsonl
"""

from __future__ import annotations

import asyncio
import dataclasses
import itertools
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from jev_trader.config import PricingConfig, RiskConfig, Settings
from jev_trader.execution.paper import PaperVenue
from jev_trader.feeds.replay import ReplayFeed
from jev_trader.feeds.synthetic import SyntheticFeed
from jev_trader.judgment.fallback import HeuristicJudge
from jev_trader.loop.engine import TradingLoop

BLOCKS = 10_000
SEEDS = (7, 11, 13)
HALF_SPREAD_BPS = (1.0, 5.0, 10.0, 20.0, 40.0)
REQUOTE_TICKS = (0.0, 2.0, 5.0)
ACK_MS = CANCEL_MS = 150.0
GAS_PER_TX = 0.01
FEE_BPS = 1.0
REPLAY = sys.argv[1] if len(sys.argv) > 1 else None
if REPLAY:
    SEEDS = (0,)
    GAS_PER_TX = 0.0
    FEE_BPS = 1.5
JUDGES = ("heuristic", "always")


class AlwaysQuoteJudge(HeuristicJudge):
    """Baseline with no judgment: never pulls, always quotes both sides.

    Keeps the heuristic's inventory pressure so quotes still skew to reduce inventory.
    """

    async def judge(self, state):
        return dataclasses.replace(
            await super().judge(state),
            toxic_flow=0.0,
            liquidity_stressed=0.0,
            quote_environment=3.0,
            quote_environment_confidence=1.0,
        )


async def _run(judge_name: str, half_spread_bps: float, requote_ticks: float, seed: int) -> dict:
    settings = Settings(
        pricing=PricingConfig(min_half_spread_bps=half_spread_bps, requote_ticks=requote_ticks),
        # loss limits off so every run finishes and settings compare on equal footing
        risk=RiskConfig(max_daily_loss=1e9, max_drawdown=1.0),
    )
    venue = PaperVenue(
        starting_cash=settings.starting_cash,
        fee_bps=FEE_BPS,
        tick_size=settings.pricing.tick_size,
        max_order_size=settings.risk.max_order_size,
        ack_ms=ACK_MS,
        cancel_ms=CANCEL_MS,
        gas_per_place=GAS_PER_TX,
        gas_per_cancel=GAS_PER_TX,
    )
    fallback = HeuristicJudge(max_position=settings.risk.max_position)
    judge = AlwaysQuoteJudge(max_position=settings.risk.max_position)
    loop = TradingLoop(
        feed=ReplayFeed(Path(REPLAY)) if REPLAY else SyntheticFeed(seed=seed, max_blocks=BLOCKS),
        venue=venue,
        judge=judge if judge_name == "always" else fallback,
        fallback=fallback,
        settings=settings,
    )
    return (await loop.run()).summary()


def run_one(args: tuple[str, float, float, int]) -> tuple[str, float, float, int, dict]:
    return (*args, asyncio.run(_run(*args)))


def main() -> None:
    grid = list(itertools.product(JUDGES, HALF_SPREAD_BPS, REQUOTE_TICKS, SEEDS))
    with ProcessPoolExecutor() as pool:
        results = list(pool.map(run_one, grid))

    feed = f"replay {REPLAY}" if REPLAY else f"synthetic {BLOCKS} blocks x seeds {SEEDS}"
    print(f"{feed}, latency {ACK_MS:.0f}ms, gas ${GAS_PER_TX}/tx, fee {FEE_BPS} bps")
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
