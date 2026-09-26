from __future__ import annotations

import pytest

from jev_trader.domain import Order
from jev_trader.execution.base import OrderRejected
from jev_trader.execution.paper import PaperVenue
from jev_trader.state.events import Trade


def make_venue(**overrides: float) -> PaperVenue:
    values = {
        "starting_cash": 1000.0,
        "fee_bps": 0.0,
        "tick_size": 0.01,
        "max_order_size": 10.0,
    }
    values.update(overrides)
    return PaperVenue(**values)


async def test_post_only_order_rests_then_fills_on_trade_through(make_event):
    venue = make_venue()
    venue.on_block(make_event())
    await venue.place(Order(side="buy", price=99.98, size=5.0))
    assert len(venue.open_orders()) == 1

    trade = Trade(ts=1.0, price=99.97, size=3.0, side="sell")
    venue.on_block(make_event(block=1, ts=1.0, mid=99.99, trades=(trade,)))
    fills = venue.drain_fills()
    assert len(fills) == 1
    assert fills[0].size == 3.0
    assert fills[0].price == 99.98
    assert venue.inventory() == pytest.approx(3.0)
    assert venue.drain_fills() == ()


async def test_post_only_rejects_crossing_order(make_event):
    venue = make_venue()
    venue.on_block(make_event())
    with pytest.raises(OrderRejected):
        await venue.place(Order(side="buy", price=100.05, size=1.0))
    assert venue.open_orders() == ()
    assert venue.health().reject_rate == pytest.approx(1.0)


async def test_order_size_limit(make_event):
    venue = make_venue()
    venue.on_block(make_event())
    with pytest.raises(OrderRejected):
        await venue.place(Order(side="buy", price=99.9, size=11.0))


async def test_realized_pnl_roundtrip(make_event):
    venue = make_venue()
    venue.on_block(make_event())

    await venue.place(Order(side="buy", price=99.0, size=2.0))
    buy_trade = Trade(ts=1.0, price=98.99, size=2.0, side="sell")
    venue.on_block(make_event(block=1, ts=1.0, mid=99.0, trades=(buy_trade,)))
    venue.drain_fills()
    assert venue.inventory() == pytest.approx(2.0)

    await venue.place(Order(side="sell", price=101.0, size=2.0))
    sell_trade = Trade(ts=2.0, price=101.01, size=2.0, side="buy")
    venue.on_block(make_event(block=2, ts=2.0, mid=101.0, trades=(sell_trade,)))
    venue.drain_fills()

    assert venue.inventory() == pytest.approx(0.0)
    assert venue.pnl().realized == pytest.approx(4.0)
    assert venue.equity() == pytest.approx(1004.0)


async def test_flatten_closes_inventory_at_touch(make_event):
    venue = make_venue()
    venue.on_block(make_event())
    await venue.place(Order(side="buy", price=99.98, size=4.0))
    trade = Trade(ts=1.0, price=99.97, size=4.0, side="sell")
    venue.on_block(make_event(block=1, ts=1.0, mid=99.99, trades=(trade,)))
    venue.drain_fills()
    assert venue.inventory() == pytest.approx(4.0)

    fill = await venue.flatten()
    assert fill is not None
    assert fill.side == "sell"
    assert venue.inventory() == pytest.approx(0.0)
    assert venue.open_orders() == ()


async def test_cancel_all_removes_orders(make_event):
    venue = make_venue()
    venue.on_block(make_event())
    await venue.place(Order(side="buy", price=99.9, size=1.0))
    await venue.place(Order(side="sell", price=100.1, size=1.0))
    assert len(venue.open_orders()) == 2
    cancelled = await venue.cancel_all()
    assert cancelled == 2
    assert venue.open_orders() == ()


async def test_fees_are_charged(make_event):
    venue = make_venue(fee_bps=10.0)
    venue.on_block(make_event())
    await venue.place(Order(side="buy", price=100.0, size=1.0))
    trade = Trade(ts=1.0, price=99.99, size=1.0, side="sell")
    venue.on_block(make_event(block=1, ts=1.0, mid=100.0, trades=(trade,)))
    venue.drain_fills()
    assert venue.pnl().fees == pytest.approx(0.1)
