from __future__ import annotations

from jev_trader.config import PolicyConfig, RiskConfig
from jev_trader.domain import Action, ActionKind, JudgmentSet
from jev_trader.policy.sizing import inventory_skew
from jev_trader.state.snapshot import MarketState


def compose_action(
    judgments: JudgmentSet,
    state: MarketState,
    config: PolicyConfig,
    risk: RiskConfig,
) -> Action:
    """Policy engine: thresholds live here in code, never in the model.

    One threshold per action, scaled to what being wrong costs.
    """
    if state.drawdown > risk.max_drawdown:
        return Action(ActionKind.KILL, reason=f"drawdown {state.drawdown:.4f}")

    if judgments.toxic_flow > config.toxic_pull:
        return Action(
            ActionKind.PULL_QUOTES,
            reason=f"toxic flow {judgments.toxic_flow:.2f}",
        )

    if judgments.liquidity_stressed > config.liquidity_widen:
        return Action(
            ActionKind.WIDEN,
            size_scale=config.low_confidence_scale,
            reason=f"liquidity stress {judgments.liquidity_stressed:.2f}",
        )

    environment = judgments.quote_environment
    confidence = judgments.quote_environment_confidence
    skew = inventory_skew(judgments.inventory_pressure, state.inventory)

    if environment >= config.quote_min_score:
        if confidence >= config.quote_min_confidence:
            return Action(
                ActionKind.QUOTE_BOTH_SIDES,
                size_scale=1.0,
                skew=skew,
                reason=f"quote env {environment:.2f} conf {confidence:.2f}",
            )
        return Action(
            ActionKind.QUOTE_WIDE,
            size_scale=config.low_confidence_scale,
            skew=skew,
            reason=f"low confidence {confidence:.2f} on env {environment:.2f}",
        )

    if environment >= config.quote_wide_min_score:
        return Action(
            ActionKind.QUOTE_WIDE,
            size_scale=config.low_confidence_scale,
            skew=skew,
            reason=f"wide env {environment:.2f}",
        )

    return Action(ActionKind.STAND_DOWN, reason=f"quote env {environment:.2f}")
