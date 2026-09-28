from __future__ import annotations

import pytest

from jev_trader.domain import Order
from jev_trader.execution.base import OrderRejected
from jev_trader.execution.hyperliquid import HyperliquidVenue, round_price


def ok(*statuses):
    return {"status": "ok", "response": {"type": "order", "data": {"statuses": list(statuses)}}}


class FakeExchange:
    def __init__(self):
        self.calls = []
        self.next_order = ok({"resting": {"oid": 101}})

    def order(self, coin, is_buy, sz, px, order_type):
        self.calls.append(("order", coin, is_buy, sz, px, order_type))
        return self.next_order

    def cancel(self, coin, oid):
        self.calls.append(("cancel", coin, oid))
        return ok("success")

    def bulk_cancel(self, requests):
        self.calls.append(("bulk_cancel", requests))
        return ok(*["success"] * len(requests))

    def schedule_cancel(self, when_ms):
        self.calls.append(("schedule_cancel", when_ms))
        return {"status": "ok", "response": {"type": "default"}}

    def market_close(self, coin):
        self.calls.append(("market_close", coin))
        return ok({"filled": {"totalSz": "0.5", "avgPx": "120.0", "oid": 9}})


class FakeInfo:
    def __init__(self):
        self.position = None
        self.orders = []
        self.fills = []

    def user_state(self, address):
        positions = [{"position": self.position, "type": "oneWay"}] if self.position else []
        return {"assetPositions": positions, "marginSummary": {"accountValue": "1000.0"}}

    def open_orders(self, address):
        return self.orders

    def user_fills_by_time(self, address, start_ms):
        return self.fills


def make_venue():
    exchange, info = FakeExchange(), FakeInfo()
    venue = HyperliquidVenue(
        coin="SOL", exchange=exchange, info=info, address="0xabc", sz_decimals=2, reconcile_s=0
    )
    return venue, exchange, info


def test_round_price_follows_hyperliquid_rules():
    assert round_price(121.4149, sz_decimals=2) == 121.41  # 5 significant figures
    assert round_price(84048.51, sz_decimals=5) == 84049.0  # 5 sig figs beats decimals
    assert round_price(0.0123456, sz_decimals=0) == 0.012346  # <= 6 decimals


async def test_place_sends_post_only_and_tracks_the_resting_order():
    venue, exchange, _ = make_venue()
    oid = await venue.place(Order(side="buy", price=121.4149, size=0.1234))
    assert oid == "101"
    assert exchange.calls == [("order", "SOL", True, 0.12, 121.41, {"limit": {"tif": "Alo"}})]
    assert venue.open_orders() == {"101": Order("buy", 121.41, 0.12)}


async def test_rejections_raise_and_orders_under_ten_dollars_never_leave():
    venue, exchange, _ = make_venue()
    with pytest.raises(OrderRejected, match="below"):
        await venue.place(Order(side="buy", price=121.0, size=0.05))  # $6
    assert exchange.calls == []

    exchange.next_order = ok({"error": "Post only order would have immediately matched"})
    with pytest.raises(OrderRejected, match="Post only"):
        await venue.place(Order(side="buy", price=121.0, size=0.1))
    assert venue.open_orders() == {}
    assert venue.health().reject_rate == 1.0


async def test_sync_reads_position_fills_and_drops_orders_the_exchange_no_longer_has():
    venue, exchange, info = make_venue()
    await venue.place(Order(side="buy", price=121.0, size=0.1))
    info.position = {"coin": "SOL", "szi": "0.1", "entryPx": "121.0", "unrealizedPnl": "0.05"}
    fill = {"coin": "SOL", "side": "B", "px": "121.0", "sz": "0.1", "time": 1, "oid": 101,
            "tid": 7, "fee": "0.0018", "closedPnl": "0.0"}
    info.fills = [fill, fill]  # duplicates must count once

    await venue.sync()

    assert venue.inventory() == pytest.approx(0.1)
    assert venue.open_orders() == {}  # filled, so no longer open on the exchange
    assert len(venue.drain_fills()) == 1
    assert venue.pnl().fees == pytest.approx(0.0018)
    assert venue.equity() == 1000.0
    assert any(call[0] == "schedule_cancel" for call in exchange.calls)

    await venue.sync()  # the same fill again on the next poll is ignored
    assert venue.drain_fills() == ()


async def test_sync_cancels_orphans_left_by_a_crashed_run():
    venue, exchange, info = make_venue()
    info.orders = [{"coin": "SOL", "oid": 55}, {"coin": "BTC", "oid": 56}]
    await venue.sync()
    assert ("bulk_cancel", [{"coin": "SOL", "oid": 55}]) in exchange.calls


async def test_cancel_all_is_one_bulk_request():
    venue, exchange, _ = make_venue()
    exchange.next_order = ok({"resting": {"oid": 1}})
    await venue.place(Order(side="buy", price=121.0, size=0.1))
    exchange.next_order = ok({"resting": {"oid": 2}})
    await venue.place(Order(side="sell", price=122.0, size=0.1))

    assert await venue.cancel_all() == 2
    bulk = [c for c in exchange.calls if c[0] == "bulk_cancel"]
    assert bulk == [("bulk_cancel", [{"coin": "SOL", "oid": 1}, {"coin": "SOL", "oid": 2}])]
    assert venue.open_orders() == {}


async def test_kill_switch_cancels_orders_it_never_placed_and_flattens():
    venue, exchange, info = make_venue()  # fresh process: tracks no orders
    info.orders = [{"coin": "SOL", "oid": 55}, {"coin": "ETH", "oid": 66}]  # from a crashed run
    info.position = {"coin": "SOL", "szi": "0.5", "entryPx": "121.0", "unrealizedPnl": "0"}

    fill = await venue.flatten()

    assert ("bulk_cancel", [{"coin": "SOL", "oid": 55}]) in exchange.calls
    assert ("market_close", "SOL") in exchange.calls
    assert fill is not None and fill.side == "sell" and fill.size == 0.5


def test_connect_offers_no_mainnet_switch():
    import inspect

    params = inspect.signature(HyperliquidVenue.connect).parameters
    assert not any("mainnet" in name or "url" in name or "testnet" in name for name in params)
