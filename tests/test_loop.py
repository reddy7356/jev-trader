from __future__ import annotations

import json

import pytest

from jev_trader.calibration.log import CalibrationLogger
from jev_trader.config import RiskConfig, Settings
from jev_trader.domain import Fill, JudgmentSet, Order
from jev_trader.execution.paper import PaperVenue
from jev_trader.feeds.synthetic import SyntheticFeed
from jev_trader.judgment.fallback import HeuristicJudge
from jev_trader.loop.engine import MarkoutTracker, TradingLoop


def build_loop(tmp_path, *, blocks: int, settings: Settings, judge=None, allow_fallback=True):
    feed = SyntheticFeed(seed=3, block_ms=300, max_blocks=blocks)
    venue = PaperVenue(
        starting_cash=settings.starting_cash,
        fee_bps=settings.fee_bps,
        tick_size=settings.pricing.tick_size,
        max_order_size=settings.risk.max_order_size,
    )
    fallback = HeuristicJudge(max_position=settings.risk.max_position)
    calibration = CalibrationLogger(tmp_path / "cal.jsonl")
    loop = TradingLoop(
        feed=feed,
        venue=venue,
        judge=judge or fallback,
        fallback=fallback,
        settings=settings,
        calibration=calibration,
        allow_fallback=allow_fallback,
    )
    return loop, calibration


async def test_paper_loop_runs_end_to_end(tmp_path):
    settings = Settings(log_path=tmp_path / "cal.jsonl")
    loop, calibration = build_loop(tmp_path, blocks=200, settings=settings)
    stats = await loop.run()
    calibration.close()

    assert stats.blocks == 200
    assert stats.decisions == 200
    assert stats.fallback_decisions == 200
    assert stats.timeouts == 0
    assert stats.jev_errors == 0
    assert not loop.killed

    lines = [
        json.loads(line)
        for line in (tmp_path / "cal.jsonl").read_text().strip().splitlines()
    ]
    decisions = [line for line in lines if line["kind"] == "decision"]
    outcomes = [line for line in lines if line["kind"] == "outcome"]
    assert len(decisions) == 200
    assert len(outcomes) == 200 - settings.calibration_horizon_blocks
    assert all(line["source"] == "fallback" for line in decisions)


async def test_loop_holds_when_decision_latency_breaches(tmp_path):
    settings = Settings(
        log_path=tmp_path / "cal.jsonl",
        risk=RiskConfig(max_decision_latency_ms=0.0),
    )
    loop, calibration = build_loop(tmp_path, blocks=50, settings=settings)
    stats = await loop.run()
    calibration.close()

    assert stats.holds == 50
    assert stats.actions.get("hold") == 50
    assert stats.fills == 0


async def test_kill_switch_on_api_errors(tmp_path):
    class FailingJudge:
        async def judge(self, state):
            raise RuntimeError("jev exploded")

    settings = Settings(
        log_path=tmp_path / "cal.jsonl",
        risk=RiskConfig(max_api_errors=0),
    )
    loop, calibration = build_loop(
        tmp_path, blocks=50, settings=settings, judge=FailingJudge()
    )
    stats = await loop.run()
    calibration.close()

    assert stats.kills == 1
    assert loop.killed
    assert stats.jev_errors >= 1


async def test_deterministic_paper_runs(tmp_path):
    settings = Settings(log_path=tmp_path / "cal.jsonl")
    first_loop, first_log = build_loop(tmp_path / "a", blocks=100, settings=settings)
    first = await first_loop.run()
    first_log.close()

    second_loop, second_log = build_loop(tmp_path / "b", blocks=100, settings=settings)
    second = await second_loop.run()
    second_log.close()

    first_summary = first.summary()
    second_summary = second.summary()
    for summary in (first_summary, second_summary):
        for key in (
            "avg_latency_ms",
            "p50_latency_ms",
            "p95_latency_ms",
            "p99_latency_ms",
            "max_latency_ms",
        ):
            summary.pop(key)
    assert first_summary == second_summary


class FakeJev:
    """Stands in for the live Jev client until a TypeSafe key is available."""

    def __init__(self, model: str = "jev-latest") -> None:
        self._model = model

    async def judge(self, state):
        return JudgmentSet(
            quote_environment=2.5,
            quote_environment_confidence=0.9,
            model=self._model,
            source="jev",
            input_tokens=380,
            output_tokens=40,
        )


async def test_live_jev_tokens_and_version_are_counted(tmp_path):
    settings = Settings(log_path=tmp_path / "cal.jsonl", jev_model="jev-2026-09")
    loop, calibration = build_loop(
        tmp_path, blocks=20, settings=settings, judge=FakeJev(model="jev-2026-10")
    )
    stats = await loop.run()
    calibration.close()

    summary = stats.summary()
    assert stats.jev_decisions == 20
    assert summary["input_tokens"] == 20 * 380
    assert summary["output_tokens"] == 20 * 40
    assert summary["tokens_per_jev_decision"] == 420.0
    assert summary["model_mismatches"] == 20


async def test_live_only_holds_instead_of_falling_back(tmp_path):
    class FailingJudge:
        async def judge(self, state):
            raise RuntimeError("jev down")

    settings = Settings(log_path=tmp_path / "cal.jsonl", risk=RiskConfig(max_api_errors=1000))
    loop, calibration = build_loop(
        tmp_path, blocks=20, settings=settings, judge=FailingJudge(), allow_fallback=False
    )
    stats = await loop.run()
    calibration.close()

    assert stats.fallback_decisions == 0
    assert stats.jev_errors == 20
    assert stats.actions == {"hold": 20}
    assert stats.fills == 0


def test_markouts_sign_moves_from_our_side_of_the_trade():
    tracker = MarkoutTracker(horizons=(0, 2))
    buy = Fill(side="buy", price=99.99, size=2.0, fee=0.0)
    sell = Fill(side="sell", price=100.01, size=1.0, fee=0.0)
    tracker.on_block(0, 100.00, (buy, sell))
    tracker.on_block(1, 99.95)
    tracker.on_block(2, 99.90)  # mid fell: bad for the buy, good for the sell

    assert tracker.pnl[0] == pytest.approx(2 * 0.01 + 1 * 0.01)
    assert tracker.pnl[2] == pytest.approx(2 * (99.90 - 99.99) + 1 * (100.01 - 99.90))
    summary = tracker.summary()
    assert summary["markout_bps"]["0b"] > 0 > summary["markout_bps"]["2b"]
    assert summary["hit_rate"]["0b"] == 1.0  # both filled inside the mid
    assert summary["hit_rate"]["2b"] == 0.5  # the sell won, the buy lost
    tracker.on_block(3, 50.0)
    assert tracker.pnl[2] == pytest.approx(2 * (99.90 - 99.99) + 1 * (100.01 - 99.90))


async def test_reconcile_keeps_quotes_that_are_still_on_target(tmp_path, make_event):
    settings = Settings(log_path=tmp_path / "cal.jsonl")
    loop, _ = build_loop(tmp_path, blocks=1, settings=settings)
    venue = PaperVenue(
        starting_cash=1000.0,
        fee_bps=0.0,
        tick_size=0.01,
        max_order_size=25.0,
        gas_per_place=1.0,
        gas_per_cancel=10.0,
    )
    loop._venue = venue
    venue.on_block(make_event())
    bid = Order(side="buy", price=99.98, size=1.0)
    ask = Order(side="sell", price=100.02, size=1.0)

    await loop._reconcile([bid, ask])
    await loop._reconcile([bid, ask])  # unchanged: nothing sent
    assert venue.pnl().gas == pytest.approx(2.0)

    await loop._reconcile([Order(side="buy", price=99.97, size=1.0), ask])  # bid moved
    assert venue.pnl().gas == pytest.approx(2.0 + 10.0 + 1.0)
    assert sorted(o.price for o in venue.open_orders().values()) == [99.97, 100.02]

    await loop._reconcile([])  # stand down: cancel both
    assert venue.open_orders() == {}
