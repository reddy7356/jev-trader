from __future__ import annotations

from types import SimpleNamespace

from jev_trader.judgment.battery import (
    DIRECTION_OPTIONS,
    REGIME_OPTIONS,
    build_questions,
    parse_response,
)


def test_battery_has_six_atomic_questions():
    questions = build_questions()
    assert set(questions) == {
        "regime",
        "direction",
        "toxic_flow",
        "liquidity_stressed",
        "quote_environment",
        "inventory_pressure",
    }
    assert set(questions["regime"].criteria) == set(REGIME_OPTIONS)
    assert set(questions["direction"].criteria) == set(DIRECTION_OPTIONS)
    assert len(questions["quote_environment"].criteria) == 4
    assert len(questions["inventory_pressure"].criteria) == 4


def test_parse_response_maps_typed_answers():
    response = SimpleNamespace(
        model="jev-latest",
        request_id="req_123",
        choices={
            "regime": SimpleNamespace(
                choice="trending",
                confidence=0.71,
                probabilities={"trending": 0.71, "mean_reverting": 0.29},
            ),
            "direction": SimpleNamespace(
                choice="up",
                confidence=0.62,
                probabilities={"up": 0.62, "down": 0.18, "neutral": 0.20},
            ),
        },
        nouls={
            "toxic_flow": SimpleNamespace(noul=0.83),
            "liquidity_stressed": SimpleNamespace(noul=0.21),
        },
        scores={
            "quote_environment": SimpleNamespace(score=2.3, confidence=0.8),
            "inventory_pressure": SimpleNamespace(score=0.4, confidence=0.9),
        },
    )
    judgments = parse_response(response, latency_ms=81.0)
    assert judgments.regime == "trending"
    assert judgments.direction == "up"
    assert judgments.toxic_flow == 0.83
    assert judgments.liquidity_stressed == 0.21
    assert judgments.quote_environment == 2.3
    assert judgments.inventory_pressure == 0.4
    assert judgments.p_up == 0.62
    assert judgments.model == "jev-latest"
    assert judgments.request_id == "req_123"
    assert judgments.latency_ms == 81.0
    assert judgments.source == "jev"
