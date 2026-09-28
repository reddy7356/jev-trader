from __future__ import annotations

import json
from dataclasses import asdict

import pytest

from jev_trader.backtest import CachedJudge
from jev_trader.domain import JudgmentSet


async def test_cached_judge_replays_hits_and_misses_the_deadline(tmp_path, make_state):
    fast = JudgmentSet(toxic_flow=0.9, inventory_pressure=3.0, latency_ms=100.0, source="jev")
    slow = JudgmentSet(toxic_flow=0.1, latency_ms=400.0, source="jev")
    cache = tmp_path / "cache.jsonl"
    rows = [
        {"block": 0, "judgments": asdict(fast)},
        {"block": 1, "judgments": asdict(slow)},
        # block 2: the API call failed, nothing saved
    ]
    cache.write_text("".join(json.dumps(r) + "\n" for r in rows))
    judge = CachedJudge(max_position=100.0, cache=cache, deadline_ms=250.0)

    hit = await judge.judge(make_state(inventory=0.0))
    assert hit.toxic_flow == 0.9
    assert hit.source == "jev"
    assert hit.inventory_pressure < 3.0  # from code on the live (flat) inventory
    for _ in range(3):  # too slow, failed, past the end of the cache
        with pytest.raises(TimeoutError):
            await judge.judge(make_state())


def test_llm_answer_parses_like_jev():
    from jev_trader.judgment.llm import parse_answer

    judgments = parse_answer(
        {
            "regime": {"trending": 2, "mean_reverting": 1, "high_vol": 1, "crisis": 0},
            "direction": {"up": 0.6, "down": 0.2, "neutral": 0.2},
            "toxic_flow": {"true": 0.3, "false": 0.7},
            "liquidity_stressed": {"true": 0.1, "false": 0.9},
            "quote_environment": {"0": 0.0, "1": 0.5, "2": 0.5, "3": 0.0},
            "inventory_pressure": {"0": 1.0, "1": 0.0, "2": 0.0, "3": 0.0},
        },
        model="test",
    )
    assert judgments.regime == "trending"
    assert judgments.regime_confidence == 0.5  # normalized: 2 of 4
    assert judgments.p_up == 0.6
    assert judgments.toxic_flow == 0.3
    assert judgments.quote_environment == 1.5  # probability-weighted level
    assert judgments.quote_environment_confidence == 0.5
    assert judgments.inventory_pressure == 0.0
