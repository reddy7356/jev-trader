from __future__ import annotations

from jev_trader.config import PolicyConfig, RiskConfig
from jev_trader.domain import ActionKind
from jev_trader.policy.engine import compose_action

POLICY = PolicyConfig()
RISK = RiskConfig()


def test_kill_on_drawdown(make_judgments, make_state):
    action = compose_action(
        make_judgments(), make_state(drawdown=0.5), POLICY, RISK
    )
    assert action.kind is ActionKind.KILL


def test_pull_quotes_on_toxic_flow(make_judgments, make_state):
    action = compose_action(
        make_judgments(toxic_flow=0.9), make_state(), POLICY, RISK
    )
    assert action.kind is ActionKind.PULL_QUOTES


def test_widen_on_liquidity_stress(make_judgments, make_state):
    action = compose_action(
        make_judgments(liquidity_stressed=0.9), make_state(), POLICY, RISK
    )
    assert action.kind is ActionKind.WIDEN
    assert action.size_scale < 1.0


def test_quote_both_sides_in_good_environment(make_judgments, make_state):
    action = compose_action(
        make_judgments(quote_environment=2.5, quote_environment_confidence=0.9),
        make_state(),
        POLICY,
        RISK,
    )
    assert action.kind is ActionKind.QUOTE_BOTH_SIDES
    assert action.size_scale == 1.0


def test_low_confidence_downgrades_to_wide_and_small(make_judgments, make_state):
    action = compose_action(
        make_judgments(quote_environment=2.5, quote_environment_confidence=0.5),
        make_state(),
        POLICY,
        RISK,
    )
    assert action.kind is ActionKind.QUOTE_WIDE
    assert action.size_scale == POLICY.low_confidence_scale


def test_quote_wide_in_marginal_environment(make_judgments, make_state):
    action = compose_action(
        make_judgments(quote_environment=1.5), make_state(), POLICY, RISK
    )
    assert action.kind is ActionKind.QUOTE_WIDE


def test_stand_down_in_bad_environment(make_judgments, make_state):
    action = compose_action(
        make_judgments(quote_environment=0.5), make_state(), POLICY, RISK
    )
    assert action.kind is ActionKind.STAND_DOWN


def test_inventory_skew_sign(make_judgments, make_state):
    long_action = compose_action(
        make_judgments(inventory_pressure=3.0),
        make_state(inventory=50.0),
        POLICY,
        RISK,
    )
    short_action = compose_action(
        make_judgments(inventory_pressure=3.0),
        make_state(inventory=-50.0),
        POLICY,
        RISK,
    )
    assert long_action.skew < 0
    assert short_action.skew > 0
