from __future__ import annotations

import pytest

from jev_trader.judgment.fallback import HeuristicJudge


async def test_fallback_judgments_are_in_range(make_state):
    judge = HeuristicJudge(max_position=100.0)
    judgments = await judge.judge(make_state(imbalance=0.8, trade_intensity=2.0))
    assert 0.0 <= judgments.toxic_flow <= 1.0
    assert 0.0 <= judgments.liquidity_stressed <= 1.0
    assert 0.0 <= judgments.quote_environment <= 3.0
    assert 0.0 <= judgments.inventory_pressure <= 3.0
    assert judgments.source == "fallback"
    assert judgments.direction in {"up", "down", "neutral"}


async def test_direction_probabilities_sum_to_one(make_state):
    judge = HeuristicJudge(max_position=100.0)
    judgments = await judge.judge(make_state(ret_1m=0.01, imbalance=0.9))
    assert sum(judgments.direction_probabilities.values()) == pytest.approx(1.0)
    assert judgments.direction_probabilities["up"] > 0.5


async def test_inventory_pressure_scales_with_position(make_state):
    judge = HeuristicJudge(max_position=100.0)
    flat = await judge.judge(make_state(inventory=0.0))
    loaded = await judge.judge(make_state(inventory=100.0))
    assert flat.inventory_pressure == 0.0
    assert loaded.inventory_pressure == pytest.approx(3.0)
