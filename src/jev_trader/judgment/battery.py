from __future__ import annotations

from typing import Any

from typesafe_sdk import Choice, Noul, Score

from jev_trader.domain import JudgmentSet

REGIME_OPTIONS = ("trending", "mean_reverting", "high_vol", "crisis")
DIRECTION_OPTIONS = ("up", "down", "neutral")

QUOTE_ENVIRONMENT_LEVELS = (
    "Do not quote: adverse conditions for providing liquidity",
    "Marginal: quote only wide and small, if at all",
    "Standard: normal quoting conditions",
    "Excellent: favorable conditions for providing liquidity",
)

INVENTORY_PRESSURE_LEVELS = (
    "None: inventory is comfortable, no urgency",
    "Mild: begin skewing quotes to reduce inventory",
    "Hard: skew quotes strongly and reduce size on the increasing side",
    "Immediate: reduce inventory now",
)


def build_questions() -> dict[str, Choice | Noul | Score]:
    """One call, six atomic judgments. Atomic questions composed in code."""
    return {
        "regime": Choice(
            instructions="What market regime does this state describe?",
            criteria={name: None for name in REGIME_OPTIONS},
        ),
        "direction": Choice(
            instructions="Directional bias of the mid over the next 10 blocks?",
            criteria={name: None for name in DIRECTION_OPTIONS},
        ),
        "toxic_flow": Noul(
            instructions="Is aggressive flow likely informed rather than noise?",
            criteria={
                "true": "Aggressive flow looks informed or one-sided",
                "false": "Aggressive flow looks like uninformed noise",
            },
        ),
        "liquidity_stressed": Noul(
            instructions="Is the book thinner than its recent norm?",
            criteria={
                "true": "Depth is unusually thin relative to recent blocks",
                "false": "Depth is normal or deeper than usual",
            },
        ),
        "quote_environment": Score(
            instructions="How favorable is this state for providing liquidity?",
            criteria=list(QUOTE_ENVIRONMENT_LEVELS),
        ),
        "inventory_pressure": Score(
            instructions="How urgent is it to reduce inventory?",
            criteria=list(INVENTORY_PRESSURE_LEVELS),
        ),
    }


def parse_response(response: Any, *, latency_ms: float, source: str = "jev") -> JudgmentSet:
    regime = response.choices["regime"]
    direction = response.choices["direction"]
    quote_environment = response.scores["quote_environment"]
    inventory_pressure = response.scores["inventory_pressure"]

    request_id: str | None
    try:
        request_id = response.request_id
    except Exception:
        request_id = None

    return JudgmentSet(
        regime=regime.choice,
        regime_confidence=regime.confidence,
        regime_probabilities=dict(regime.probabilities),
        direction=direction.choice,
        direction_confidence=direction.confidence,
        direction_probabilities=dict(direction.probabilities),
        toxic_flow=response.nouls["toxic_flow"].noul,
        liquidity_stressed=response.nouls["liquidity_stressed"].noul,
        quote_environment=quote_environment.score,
        quote_environment_confidence=quote_environment.confidence,
        inventory_pressure=inventory_pressure.score,
        inventory_pressure_confidence=inventory_pressure.confidence,
        model=response.model,
        latency_ms=latency_ms,
        request_id=request_id,
        source=source,
    )
