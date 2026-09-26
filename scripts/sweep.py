"""Phase 2 tuning sweep: does any spread / requote setting make money after costs?

Runs the paper loop (heuristic judge) over a grid of settings and several seeds,
with realistic latency and gas, and prints net PnL per setting.

    uv run python scripts/sweep.py
"""

from __future__ import annotations

import asyncio
import itertools
from concurrent.futures import ProcessPoolExecutor

from jev_trader.config import PricingConfig, RiskConfig, Settings
from jev_trader.execution.paper import PaperVenue
from jev_trader.feeds.synthetic import SyntheticFeed
from jev_trader.judgment.fallback import HeuristicJudge
from jev_trader.loop.engine import TradingLoop

BLOCKS = 10_000
SEEDS = (7, 11, 13)
HALF_SPREAD_BPS = (1.0, 5.0, 10.0, 20.0, 40.0)
REQUOTE_TICKS = (0.0, 2.0, 5.0)
ACK_MS = CANCEL_MS = 150.0
GAS_PER_TX = 0.01


async def _run(half_spread_bps: float, requote_ticks: float, seed: int) -> dict:
    settings = Settings(
        pricing=PricingConfig(min_half_spread_bps=half_spread_bps, requote_ticks=requote_ticks),
        # loss limits off so every run finishes and settings compare on equal footing
        risk=RiskConfig(max_daily_loss=1e9, max_drawdown=1.0),
    )
    venue = PaperVenue(
        starting_cash=settings.starting_cash,
        fee_bps=settings.fee_bps,
        tick_size=settings.pricing.tick_size,
        max_order_size=settings.risk.max_order_size,
        ack_ms=ACK_MS,
        cancel_ms=CANCEL_MS,
        gas_per_place=GAS_PER_TX,
        gas_per_cancel=GAS_PER_TX,
    )
    fallback = HeuristicJudge(max_position=settings.risk.max_position)
    loop = TradingLoop(
        feed=SyntheticFeed(seed=seed, max_blocks=BLOCKS),
        venue=venue,
        judge=fallback,
        fallback=fallback,
        settings=settings,
    )
    return (await loop.run()).summary()


def run_one(args: tuple[float, float, int]) -> tuple[float, float, int, dict]:
    return (*args, asyncio.run(_run(*args)))


def main() -> None:
    grid = list(itertools.product(HALF_SPREAD_BPS, REQUOTE_TICKS, SEEDS))
    with ProcessPoolExecutor() as pool:
        results = list(pool.map(run_one, grid))

    print(f"{BLOCKS} blocks x seeds {SEEDS}, latency {ACK_MS:.0f}ms, gas ${GAS_PER_TX}/tx")
    print(
        "half_bps requote blocks  fills  markout0_bps  realized    fees     gas"
        "   net(avg)  net per seed"
    )
    for half, requote in itertools.product(HALF_SPREAD_BPS, REQUOTE_TICKS):
        rows = [r for h, q, _, r in results if h == half and q == requote]
        nets = [r["equity"] - Settings().starting_cash for r in rows]
        keys = ("blocks", "fills", "realized", "fees", "gas")
        avg = {k: sum(r[k] for r in rows) / len(rows) for k in keys}
        markout0 = sum(r["markout_bps"]["0b"] for r in rows) / len(rows)
        print(
            f"{half:8.1f} {requote:7.0f} {avg['blocks']:6.0f} {avg['fills']:6.0f} {markout0:13.2f}"
            f" {avg['realized']:9.2f} {avg['fees']:7.2f} {avg['gas']:7.2f}"
            f" {sum(nets) / len(nets):9.2f}  {' '.join(f'{n:8.2f}' for n in nets)}"
        )


if __name__ == "__main__":
    main()
