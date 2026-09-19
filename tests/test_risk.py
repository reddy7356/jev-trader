from __future__ import annotations

from jev_trader.config import RiskConfig
from jev_trader.risk.limits import RiskEngine, RiskState, Verdict

LIMITS = RiskConfig()
ENGINE = RiskEngine(LIMITS)


def test_ok_when_within_limits():
    verdict = ENGINE.check(RiskState())
    assert verdict.verdict is Verdict.OK
    assert verdict.breaches == ()


def test_kill_on_drawdown():
    verdict = ENGINE.check(RiskState(drawdown=LIMITS.max_drawdown + 0.01))
    assert verdict.verdict is Verdict.KILL


def test_kill_on_daily_loss():
    verdict = ENGINE.check(RiskState(daily_pnl=-LIMITS.max_daily_loss - 1.0))
    assert verdict.verdict is Verdict.KILL


def test_kill_on_api_errors():
    verdict = ENGINE.check(RiskState(api_errors=LIMITS.max_api_errors + 1))
    assert verdict.verdict is Verdict.KILL


def test_kill_on_position_beyond_multiplier():
    verdict = ENGINE.check(
        RiskState(position=LIMITS.max_position * LIMITS.kill_position_multiplier + 1.0)
    )
    assert verdict.verdict is Verdict.KILL


def test_reduce_only_when_position_over_max():
    verdict = ENGINE.check(RiskState(position=LIMITS.max_position + 1.0))
    assert verdict.verdict is Verdict.REDUCE_ONLY


def test_reduce_only_on_stale_inventory():
    verdict = ENGINE.check(RiskState(inventory_age_s=LIMITS.max_inventory_age_s + 1.0))
    assert verdict.verdict is Verdict.REDUCE_ONLY


def test_hold_on_stale_data():
    verdict = ENGINE.check(RiskState(data_age_s=LIMITS.max_stale_data_age_s + 0.1))
    assert verdict.verdict is Verdict.HOLD


def test_hold_on_slow_decision():
    verdict = ENGINE.check(
        RiskState(decision_latency_ms=LIMITS.max_decision_latency_ms + 1.0)
    )
    assert verdict.verdict is Verdict.HOLD


def test_kill_beats_hold_precedence():
    verdict = ENGINE.check(
        RiskState(
            drawdown=LIMITS.max_drawdown + 0.01,
            data_age_s=LIMITS.max_stale_data_age_s + 0.1,
        )
    )
    assert verdict.verdict is Verdict.KILL
    assert len(verdict.breaches) == 2
