from __future__ import annotations

import json

from jev_trader.calibration.log import CalibrationLogger
from jev_trader.config import RiskConfig, Settings
from jev_trader.execution.paper import PaperVenue
from jev_trader.feeds.synthetic import SyntheticFeed
from jev_trader.judgment.fallback import HeuristicJudge
from jev_trader.loop.engine import TradingLoop


def build_loop(tmp_path, *, blocks: int, settings: Settings, judge=None):
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
        for key in ("avg_latency_ms", "p95_latency_ms", "max_latency_ms"):
            summary.pop(key)
    assert first_summary == second_summary
