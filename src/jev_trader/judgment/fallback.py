from __future__ import annotations

import math

from jev_trader.state.snapshot import MarketState
from jev_trader.types import JudgmentSet

FALLBACK_CONFIDENCE = 0.5


def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


class HeuristicJudge:
    """Deterministic fallback judgments used when Jev is unavailable.

    Confidences are deliberately fixed at a moderate level: heuristics are not
    calibrated, and the policy engine reduces size on low confidence.
    """

    def __init__(self, *, max_position: float) -> None:
        self._max_position = max(max_position, 1e-9)

    async def judge(self, state: MarketState) -> JudgmentSet:
        regime = self._regime(state)
        direction_probs = self._direction_probabilities(state)
        direction = max(direction_probs, key=direction_probs.__getitem__)
        toxic = self._toxic_flow(state)
        liquidity = self._liquidity_stressed(state)
        quote_environment = self._quote_environment(state, toxic)
        inventory_pressure = self._inventory_pressure(state)

        return JudgmentSet(
            regime=regime,
            regime_confidence=FALLBACK_CONFIDENCE,
            regime_probabilities={
                name: 0.25 for name in ("trending", "mean_reverting", "high_vol", "crisis")
            },
            direction=direction,
            direction_confidence=FALLBACK_CONFIDENCE,
            direction_probabilities=direction_probs,
            toxic_flow=toxic,
            liquidity_stressed=liquidity,
            quote_environment=quote_environment,
            quote_environment_confidence=FALLBACK_CONFIDENCE,
            inventory_pressure=inventory_pressure,
            inventory_pressure_confidence=FALLBACK_CONFIDENCE,
            model="heuristic",
            latency_ms=0.0,
            request_id=None,
            source="fallback",
        )

    def _regime(self, state: MarketState) -> str:
        if state.drawdown >= 0.04 or state.vol_ratio >= 3.0:
            return "crisis"
        if state.vol_ratio >= 1.5:
            return "high_vol"
        if abs(state.ret_5m) >= 0.003 and abs(state.ret_5m) > abs(state.ret_30m):
            return "trending"
        return "mean_reverting"

    def _direction_probabilities(self, state: MarketState) -> dict[str, float]:
        sigma = max(state.realized_vol_short, 1e-9)
        z = 0.7 * (state.ret_1m / sigma) + 0.5 * state.imbalance
        raw = {
            "up": max(0.05, _sigmoid(2.0 * z)),
            "down": max(0.05, _sigmoid(-2.0 * z)),
            "neutral": 0.35,
        }
        total = sum(raw.values())
        return {name: value / total for name, value in raw.items()}

    def _toxic_flow(self, state: MarketState) -> float:
        return _clip(
            0.5 + 0.4 * abs(state.imbalance) + 0.3 * math.log1p(state.trade_intensity),
            0.0,
            1.0,
        )

    def _liquidity_stressed(self, state: MarketState) -> float:
        stress = 0.35 * max(0.0, state.vol_ratio - 1.0) + 0.05 * state.spread_bps
        return _clip(stress, 0.0, 1.0)

    def _quote_environment(self, state: MarketState, toxic: float) -> float:
        if state.spread_bps >= 8.0 and state.vol_ratio < 2.0:
            base = 2.5
        elif state.spread_bps >= 3.0:
            base = 1.5
        else:
            base = 0.8
        return _clip(base - 1.5 * max(0.0, toxic - 0.6), 0.0, 3.0)

    def _inventory_pressure(self, state: MarketState) -> float:
        return _clip(3.0 * abs(state.inventory) / self._max_position, 0.0, 3.0)
