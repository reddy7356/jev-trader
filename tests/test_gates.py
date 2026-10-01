from __future__ import annotations

from datetime import datetime

import pytest

from jev_trader.gatekeeper import gates
from jev_trader.gatekeeper.calendar import ET
from jev_trader.gatekeeper.gates import Account, btc_risk_off, entry_blockers, jev_blockers
from jev_trader.gatekeeper.tape import Tape

SATURDAY_NOON = datetime(2026, 10, 3, 12, 0, tzinfo=ET).timestamp()  # no US events
START = SATURDAY_NOON - 66 * 60
ACCOUNT = Account(equity=10_000.0, realized_today=0.0)


def market(*, btc_60m: float = 0.0, aave_last5: float = 0.003, spike: float = 4.0,
           bid_heavy_s: float = 300.0, buy_share: float = 0.7):
    """66 minutes of tape. AAVE dips to 99 at -15 min then climbs; BTC drifts by btc_60m."""
    aave, btc = Tape("AAVE"), Tape("BTC")
    for second in range(0, 66 * 60 + 1, 10):
        ts = START + second
        left = 66 * 60 - second  # seconds until "now"
        if left > 900:
            mid = 100.0
        elif left > 300:
            mid = 99.0 + (900 - left) / 600 * 1.0  # 99 -> 100
        else:
            mid = 100.0 * (1 + aave_last5 * (300 - left) / 300)
        bids = 30.0 if left <= bid_heavy_s else 10.0
        aave.on_book(ts, [[mid - 0.005, bids]], [[mid + 0.005, 20.0]])
        btc_mid = 80_000.0 * (1 + btc_60m * second / 3600)  # steady drift, btc_60m per hour
        btc.on_book(ts, [[btc_mid - 1, 1.0]], [[btc_mid + 1, 1.0]])
        size = 10.0 * (spike if left <= 300 else 1.0)
        aave.on_trade(ts, mid, size * buy_share, "buy")
        aave.on_trade(ts, mid, size * (1 - buy_share), "sell")
        btc.on_trade(ts, btc_mid, 0.01, "buy")
        btc.on_trade(ts, btc_mid, 0.01, "sell")
    return aave, btc


def test_all_rules_pass_and_the_plan_respects_risk_and_reward():
    aave, btc = market()
    plan, reasons = entry_blockers(aave, btc, SATURDAY_NOON, ACCOUNT)
    assert reasons == []
    assert plan.stop < 99.0 < plan.entry  # just under the 15-min low
    # the stop's fee is charged on the lower stop price, so a hair under $25
    assert plan.planned_loss == pytest.approx(10_000 * gates.RISK_PCT, rel=1e-3)
    assert plan.planned_loss <= 10_000 * gates.RISK_PCT
    risk_net = plan.entry - plan.stop + plan.entry * 2 * gates.TAKER_FEE
    reward_net = plan.target1 - plan.entry - plan.entry * (gates.TAKER_FEE + gates.MAKER_FEE)
    assert reward_net / risk_net == pytest.approx(1.5)  # after fees
    assert plan.quantity * plan.entry <= ACCOUNT.equity  # no leverage


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"btc_60m": -0.03}, "in 60 min"),
        ({"btc_60m": -0.015}, "BTC not stable or rising"),
        ({"spike": 1.0}, "volume"),
        ({"bid_heavy_s": 120.0}, "bids not stronger"),
        ({"buy_share": 0.4}, "aggressive buying"),
        ({"aave_last5": -0.003}, "not accepting higher"),
    ],
)
def test_each_rule_blocks_on_its_own(change, reason):
    aave, btc = market(**change)
    plan, reasons = entry_blockers(aave, btc, SATURDAY_NOON, ACCOUNT)
    assert plan is None
    assert any(reason in r for r in reasons), reasons


def test_event_window_daily_loss_and_stale_data_block():
    aave, btc = market()
    thursday_9am = datetime(2026, 10, 1, 9, 0, tzinfo=ET).timestamp()
    shift = thursday_9am - SATURDAY_NOON
    for tape in (aave, btc):
        tape.ts += shift
    assert any("scheduled event" in r for r in entry_blockers(aave, btc, thursday_9am, ACCOUNT)[1])

    aave, btc = market()
    down_90 = Account(equity=10_000.0, realized_today=-90.0)
    assert any("daily loss" in r for r in entry_blockers(aave, btc, SATURDAY_NOON, down_90)[1])
    assert "AAVE data stale" in entry_blockers(aave, btc, SATURDAY_NOON + 60, ACCOUNT)[1]


def test_btc_crash_forces_risk_off_and_jev_thresholds():
    _, btc = market(btc_60m=-0.03)
    assert btc_risk_off(btc, SATURDAY_NOON)
    ok = {"demand_accepted": 0.85, "absorption": 0.1, "btc_sell_pressure": 0.2}
    assert jev_blockers(ok) == []
    assert jev_blockers({**ok, "demand_accepted": 0.79})  # the user's 0.8 rule
    assert jev_blockers({**ok, "absorption": 0.3})
    assert jev_blockers({**ok, "btc_sell_pressure": 0.5})
