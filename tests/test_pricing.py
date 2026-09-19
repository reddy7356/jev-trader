from __future__ import annotations

import pytest

from jev_trader.pricing.avellaneda_stoikov import (
    compute_quote,
    optimal_half_spread,
    reservation_price,
)


def test_reservation_price_skews_away_from_inventory():
    neutral = reservation_price(100.0, 0.0, 0.5, 0.01, 60.0)
    long = reservation_price(100.0, 1.0, 0.5, 0.01, 60.0)
    short = reservation_price(100.0, -1.0, 0.5, 0.01, 60.0)
    assert neutral == pytest.approx(100.0)
    assert long < neutral < short


def test_half_spread_positive_and_increasing_in_sigma():
    low = optimal_half_spread(0.5, 0.001, 60.0, 1.5)
    high = optimal_half_spread(0.5, 0.01, 60.0, 1.5)
    assert 0.0 < low < high


def test_quote_symmetric_at_zero_inventory():
    quote = compute_quote(
        mid=100.0,
        inventory=0.0,
        sigma=0.001,
        tau=60.0,
        gamma=0.5,
        kappa=1.5,
        min_half_spread=0.0,
    )
    assert quote.bid == pytest.approx(100.0 - quote.half_spread)
    assert quote.ask == pytest.approx(100.0 + quote.half_spread)


def test_min_half_spread_floor_applies():
    quote = compute_quote(
        mid=100.0,
        inventory=0.0,
        sigma=0.0,
        tau=60.0,
        gamma=0.5,
        kappa=1000.0,
        min_half_spread=0.05,
    )
    assert quote.half_spread == pytest.approx(0.05)


def test_negative_skew_shifts_quotes_down():
    base = compute_quote(
        mid=100.0,
        inventory=0.0,
        sigma=0.001,
        tau=60.0,
        gamma=0.5,
        kappa=1.5,
        min_half_spread=0.05,
    )
    skewed = compute_quote(
        mid=100.0,
        inventory=0.0,
        sigma=0.001,
        tau=60.0,
        gamma=0.5,
        kappa=1.5,
        min_half_spread=0.05,
        skew=-1.0,
    )
    assert skewed.bid < base.bid
    assert skewed.ask < base.ask


def test_tick_rounding_widens_to_grid():
    quote = compute_quote(
        mid=100.0,
        inventory=0.0,
        sigma=0.001,
        tau=60.0,
        gamma=0.5,
        kappa=1.5,
        min_half_spread=0.013,
        tick_size=0.05,
    )
    for price in (quote.bid, quote.ask):
        ticks = price / 0.05
        assert abs(ticks - round(ticks)) < 1e-9
