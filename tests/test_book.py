from __future__ import annotations

import pytest

from jev_trader.state.book import Level, OrderBook


def test_mid_spread_and_bps(make_book):
    book = make_book(100.0, spread=0.02)
    assert book.mid == pytest.approx(100.0)
    assert book.spread == pytest.approx(0.02)
    assert book.spread_bps == pytest.approx(2.0)


def test_microprice_tilts_toward_larger_side():
    book = OrderBook(
        ts=0.0,
        bids=(Level(99.0, 10.0),),
        asks=(Level(101.0, 30.0),),
    )
    assert book.microprice == pytest.approx(99.5)
    assert book.microprice < book.mid


def test_depth_and_imbalance():
    book = OrderBook(
        ts=0.0,
        bids=(Level(99.0, 30.0), Level(98.0, 10.0)),
        asks=(Level(101.0, 10.0), Level(102.0, 10.0)),
    )
    assert book.depth(3) == (40.0, 20.0)
    assert book.imbalance(3) == pytest.approx(1.0 / 3.0)


def test_one_sided_book_raises():
    book = OrderBook(ts=0.0, bids=(Level(99.0, 1.0),), asks=())
    assert not book.two_sided
    with pytest.raises(ValueError):
        _ = book.mid


def test_zero_depth_imbalance_is_neutral():
    book = OrderBook(ts=0.0, bids=(Level(99.0, 0.0),), asks=(Level(101.0, 0.0),))
    assert book.imbalance(3) == 0.0
