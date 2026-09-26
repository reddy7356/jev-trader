"""Shared backtest plumbing: cost models, baseline judges, one-run helper.

Used by scripts/sweep.py (grid search) and scripts/backtest.py (train/test report).
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jev_trader.config import RiskConfig, Settings
from jev_trader.domain import JudgmentSet
from jev_trader.execution.paper import PaperVenue
from jev_trader.feeds.replay import ReplayFeed
from jev_trader.feeds.synthetic import SyntheticFeed
from jev_trader.judgment.fallback import HeuristicJudge
from jev_trader.loop.engine import TradingLoop
from jev_trader.state.snapshot import MarketState

# loss limits off so every run finishes and settings compare on equal footing
NO_LOSS_LIMITS = RiskConfig(max_daily_loss=1e9, max_drawdown=1.0)


@dataclass(frozen=True)
class Costs:
    ack_ms: float
    cancel_ms: float
    gas_per_tx: float
    fee_bps: float


SYNTHETIC_COSTS = Costs(ack_ms=150.0, cancel_ms=150.0, gas_per_tx=0.01, fee_bps=1.0)
# Hyperliquid: no gas on orders, base-tier maker fee
HYPERLIQUID_COSTS = Costs(ack_ms=150.0, cancel_ms=150.0, gas_per_tx=0.0, fee_bps=1.5)


class AlwaysQuoteJudge(HeuristicJudge):
    """Baseline with no judgment: never pulls, always quotes both sides.

    Keeps the heuristic's inventory pressure so quotes still skew to reduce inventory.
    """

    async def judge(self, state: MarketState) -> JudgmentSet:
        return dataclasses.replace(
            await super().judge(state),
            toxic_flow=0.0,
            liquidity_stressed=0.0,
            quote_environment=3.0,
            quote_environment_confidence=1.0,
        )


JUDGES: dict[str, type[HeuristicJudge]] = {
    "heuristic": HeuristicJudge,
    "always": AlwaysQuoteJudge,
}


def make_feed(source: str | Path | int, blocks: int = 10_000) -> Any:
    """A recording path replays real data; an int is a synthetic seed."""
    if isinstance(source, int):
        return SyntheticFeed(seed=source, max_blocks=blocks)
    return ReplayFeed(Path(source))


async def run(judge: str, settings: Settings, source: str | Path | int, costs: Costs) -> dict:
    venue = PaperVenue(
        starting_cash=settings.starting_cash,
        fee_bps=costs.fee_bps,
        tick_size=settings.pricing.tick_size,
        max_order_size=settings.risk.max_order_size,
        ack_ms=costs.ack_ms,
        cancel_ms=costs.cancel_ms,
        gas_per_place=costs.gas_per_tx,
        gas_per_cancel=costs.gas_per_tx,
    )
    fallback = HeuristicJudge(max_position=settings.risk.max_position)
    loop = TradingLoop(
        feed=make_feed(source),
        venue=venue,
        judge=JUDGES[judge](max_position=settings.risk.max_position),
        fallback=fallback,
        settings=settings,
    )
    return (await loop.run()).summary()
