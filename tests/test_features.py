from __future__ import annotations

import pytest

from jev_trader.state.events import Trade
from jev_trader.state.features import FeatureEngine


def test_returns_measured_from_mid_history(make_event):
    engine = FeatureEngine()
    for i in range(61):
        features = engine.update(
            make_event(block=i, ts=float(i), mid=100.0 + i * (1.0 / 60.0))
        )
    assert features.ret_1m == pytest.approx(0.01, rel=0.05)
    assert features.ret_5m == 0.0
    assert features.realized_vol_short > 0.0
    assert features.vol_ratio > 0.0


def test_flat_market_has_zero_vol(make_event):
    engine = FeatureEngine()
    for i in range(30):
        features = engine.update(make_event(block=i, ts=float(i), mid=100.0))
    assert features.realized_vol_short == pytest.approx(0.0)
    assert features.ret_1m == pytest.approx(0.0)


def test_aggressive_buy_ratio_from_trades(make_event):
    engine = FeatureEngine()
    trades = (
        Trade(ts=0.0, price=100.01, size=9.0, side="buy"),
        Trade(ts=0.0, price=99.99, size=1.0, side="sell"),
    )
    features = engine.update(make_event(block=0, ts=0.0, trades=trades))
    assert features.aggressive_buy_ratio == pytest.approx(0.9)
    assert features.trade_intensity == pytest.approx(2.0 / 30.0)


def test_no_trades_is_neutral(make_event):
    engine = FeatureEngine()
    features = engine.update(make_event())
    assert features.aggressive_buy_ratio == 0.5
    assert features.trade_intensity == 0.0
