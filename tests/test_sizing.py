from __future__ import annotations

import pytest

from jev_trader.policy.sizing import inventory_skew, kelly_fraction


def test_kelly_zero_below_edge():
    assert kelly_fraction(0.5) == 0.0
    assert kelly_fraction(0.4) == 0.0


def test_kelly_linear_above_edge():
    assert kelly_fraction(0.6) == pytest.approx(0.2)
    assert kelly_fraction(0.625) == pytest.approx(0.25)


def test_kelly_capped_at_quarter():
    assert kelly_fraction(0.95) == pytest.approx(0.25)
    assert kelly_fraction(1.0, cap=0.1) == pytest.approx(0.1)


def test_inventory_skew_direction_and_bounds():
    assert inventory_skew(3.0, 10.0) == pytest.approx(-1.0)
    assert inventory_skew(3.0, -10.0) == pytest.approx(1.0)
    assert inventory_skew(3.0, 0.0) == 0.0
    assert inventory_skew(0.0, 10.0) == 0.0
    assert inventory_skew(1.5, 10.0) == pytest.approx(-0.5)
