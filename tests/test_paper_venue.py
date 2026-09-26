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


async def test_touch_fill_waits_for_queue_ahead(make_event):
    # make_event's book rests 10.0 on the best bid at 99.99
    venue = make_venue()
    venue.on_block(make_event())
    await venue.place(Order(side="buy", price=99.99, size=5.0))

    first = Trade(ts=1.0, price=99.99, size=6.0, side="sell")
    venue.on_block(make_event(block=1, ts=1.0, trades=(first,)))
    assert venue.drain_fills() == ()

    second = Trade(ts=2.0, price=99.99, size=7.0, side="sell")
    venue.on_block(make_event(block=2, ts=2.0, trades=(second,)))
    fills = venue.drain_fills()
    assert len(fills) == 1
    assert fills[0].size == pytest.approx(3.0)
    assert venue.inventory() == pytest.approx(3.0)


async def test_touch_trade_from_same_side_does_not_fill(make_event):
    venue = make_venue()
    venue.on_block(make_event())
    await venue.place(Order(side="buy", price=99.99, size=5.0))
    trade = Trade(ts=1.0, price=99.99, size=50.0, side="buy")
    venue.on_block(make_event(block=1, ts=1.0, trades=(trade,)))
    assert venue.drain_fills() == ()


async def test_improving_the_price_puts_us_first_in_queue(make_event):
    venue = make_venue()
    venue.on_block(make_event())
    await venue.place(Order(side="buy", price=100.00, size=2.0))
    trade = Trade(ts=1.0, price=100.00, size=2.0, side="sell")
    venue.on_block(make_event(block=1, ts=1.0, mid=100.01, trades=(trade,)))
    fills = venue.drain_fills()
    assert len(fills) == 1
    assert fills[0].size == pytest.approx(2.0)


async def test_order_is_not_live_until_acked(make_event):
    venue = make_venue(ack_ms=500.0)
    venue.on_block(make_event(ts=0.0))
    await venue.place(Order(side="buy", price=99.98, size=5.0))
    early = Trade(ts=0.3, price=99.97, size=2.0, side="sell")
    late = Trade(ts=0.9, price=99.97, size=2.0, side="sell")
    venue.on_block(make_event(block=1, ts=1.0, trades=(early, late)))
    fills = venue.drain_fills()
    assert len(fills) == 1
    assert fills[0].size == pytest.approx(2.0)


async def test_cancelled_quote_can_be_picked_off_before_cancel_lands(make_event):
    venue = make_venue(cancel_ms=200.0)
    venue.on_block(make_event(ts=0.0))
    await venue.place(Order(side="buy", price=99.98, size=5.0))
    assert await venue.cancel_all() == 1
    assert venue.open_orders() == ()

    in_flight = Trade(ts=0.1, price=99.90, size=2.0, side="sell")
    after = Trade(ts=0.5, price=99.90, size=2.0, side="sell")
    venue.on_block(make_event(block=1, ts=1.0, mid=99.9, trades=(in_flight, after)))
    fills = venue.drain_fills()
    assert len(fills) == 1
    assert fills[0].price == pytest.approx(99.98)
    assert venue.inventory() == pytest.approx(2.0)
    assert await venue.cancel_all() == 0


async def test_gas_is_charged_per_place_and_cancel_even_without_fills(make_event):
    venue = make_venue(gas_per_place=0.02, gas_per_cancel=0.01)
    venue.on_block(make_event())
    for _ in range(3):  # cancel-replace both sides, three blocks in a row
        await venue.cancel_all()
        await venue.place(Order(side="buy", price=99.98, size=1.0))
        await venue.place(Order(side="sell", price=100.02, size=1.0))
    with pytest.raises(OrderRejected):
        await venue.place(Order(side="buy", price=100.05, size=1.0))

    assert venue.drain_fills() == ()
    # 7 submissions (incl. the post-only reject) and 4 cancelled orders
    assert venue.pnl().gas == pytest.approx(7 * 0.02 + 4 * 0.01)
    assert venue.equity() == pytest.approx(1000.0 - 0.18)
