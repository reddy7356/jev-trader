from __future__ import annotations

import math

import pytest

from jev_trader.loop.performance import SECONDS_PER_YEAR, performance


def test_performance_by_hand():
    # one block per second; returns +1%, -1%, +2% of starting cash
    curve = [(0.0, 100.0), (1.0, 101.0), (2.0, 100.0), (3.0, 102.0)]
    result = performance(curve, starting_cash=100.0)

    mean = (0.01 - 0.01 + 0.02) / 3
    std = math.sqrt(((0.01 - mean) ** 2 + (-0.01 - mean) ** 2 + (0.02 - mean) ** 2) / 2)
    downside = math.sqrt(0.01**2 / 3)
    per_year_scale = math.sqrt(SECONDS_PER_YEAR)
    assert result["sharpe"] == pytest.approx(mean / std * per_year_scale)
    assert result["sortino"] == pytest.approx(mean / downside * per_year_scale)
    assert result["max_drawdown"] == pytest.approx(1.0 / 101.0)


def test_flat_curve_scores_zero():
    curve = [(float(t), 100.0) for t in range(10)]
    assert performance(curve, starting_cash=100.0) == {
        "sharpe": 0.0,
        "sortino": 0.0,
        "max_drawdown": 0.0,
    }
