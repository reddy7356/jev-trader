"""Shared backtest plumbing: cost models, baseline judges, one-run helper.

Used by scripts/sweep.py (grid search) and scripts/backtest.py (train/test report).
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import tempfile
import types
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from jev_trader.calibration.log import CalibrationLogger
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


class CachedJudge(HeuristicJudge):
    """Replays judgments saved by `cache_judgments`, keyed by block index.

    The loop asks the judge exactly once per block, so the n-th call is block n.
    (Not keyed by timestamp: the replay feed shifts timestamps to the wall clock.)

    Lets a threshold grid reuse one set of paid API calls. A block whose saved
    call was slower than `deadline_ms` (or failed) replays as a missed deadline,
    exactly like the live loop. Inventory pressure comes from code on the live
    state, since each setting holds a different inventory than the cached run.
    """

    def __init__(self, *, max_position: float, cache: Path, deadline_ms: float) -> None:
        super().__init__(max_position=max_position)
        self._deadline_ms = deadline_ms
        self._calls = 0
        self._by_block: dict[int, dict] = {}
        for line in cache.read_text().splitlines():
            row = json.loads(line)
            self._by_block[row["block"]] = row["judgments"]

    async def judge(self, state: MarketState) -> JudgmentSet:
        cached = self._by_block.get(self._calls)
        self._calls += 1
        if cached is None or cached["latency_ms"] > self._deadline_ms:
            raise TimeoutError("cached judgment missed the block deadline")
        live = await super().judge(state)
        return dataclasses.replace(
            JudgmentSet(**cached),
            inventory_pressure=live.inventory_pressure,
            inventory_pressure_confidence=live.inventory_pressure_confidence,
        )


JUDGES: dict[str, type[HeuristicJudge]] = {
    "heuristic": HeuristicJudge,
    "always": AlwaysQuoteJudge,
    "cached": CachedJudge,
}


async def cache_judgments(judge: Any, source: str, out: Path, concurrency: int) -> None:
    """Ask `judge` once per block of a recording; save to `out` (JSONL, resumable).

    States come from a hand-rules run over the same recording, so every judge
    sees identical inputs. Calls run concurrently; blocks already in `out` are skipped,
    and failed calls are not saved, so rerunning fills the gaps.
    """
    with tempfile.TemporaryDirectory() as tmp:
        log = Path(tmp) / "states.jsonl"
        await run("heuristic", Settings(risk=NO_LOSS_LIMITS), source, HYPERLIQUID_COSTS, log)
        states = [
            row["state"]
            for row in map(json.loads, log.read_text().splitlines())
            if row["kind"] == "decision"
        ]
    done = set()
    if out.exists():
        done = {json.loads(line)["block"] for line in out.read_text().splitlines()}
    gate = asyncio.Semaphore(concurrency)

    async def one(block: int, state: dict) -> None:
        async with gate:
            try:
                judgments = asdict(
                    await judge.judge(types.SimpleNamespace(to_state_dict=lambda: state))
                )
            except Exception as exc:  # not saved: a rerun retries it, replay holds meanwhile
                print(f"  block {block}: {type(exc).__name__}: {exc}")
                return
            with out.open("a") as f:
                f.write(json.dumps({"block": block, "judgments": judgments}) + "\n")

    todo = [(block, state) for block, state in enumerate(states) if block not in done]
    print(f"{out}: {len(todo)} of {len(states)} blocks to judge")
    await asyncio.gather(*(one(block, state) for block, state in todo))


def make_feed(source: str | Path | int, blocks: int = 10_000) -> Any:
    """A recording path replays real data; an int is a synthetic seed."""
    if isinstance(source, int):
        return SyntheticFeed(seed=source, max_blocks=blocks)
    return ReplayFeed(Path(source))


def make_judge(judge: str, settings: Settings, cache: Path | None) -> HeuristicJudge:
    max_position = settings.risk.max_position
    if judge == "cached":
        assert cache is not None, "cached judge needs a cache file"
        deadline_ms = settings.decision_timeout_s * 1000.0
        return CachedJudge(max_position=max_position, cache=cache, deadline_ms=deadline_ms)
    return JUDGES[judge](max_position=max_position)


async def run(
    judge: str,
    settings: Settings,
    source: str | Path | int,
    costs: Costs,
    log_path: Path | None = None,
    cache: Path | None = None,
) -> dict:
    """One paper run; `log_path` (overwritten) captures calibration triples.

    `judge="cached"` replays `cache` (see `cache_judgments`).
    """
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
    calibration = None
    if log_path is not None:
        log_path.unlink(missing_ok=True)
        calibration = CalibrationLogger(log_path)
    loop = TradingLoop(
        feed=make_feed(source),
        venue=venue,
        judge=make_judge(judge, settings, cache),
        fallback=fallback,
        settings=settings,
        calibration=calibration,
    )
    try:
        return (await loop.run()).summary()
    finally:
        if calibration is not None:
            calibration.close()
