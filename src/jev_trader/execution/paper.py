from __future__ import annotations

import math
from dataclasses import dataclass

from jev_trader.domain import Fill, Order, PnL
from jev_trader.execution.base import OrderRejected, VenueHealth
from jev_trader.state.book import OrderBook
from jev_trader.state.events import BlockEvent


@dataclass(slots=True)
class _Resting:
    order: Order
    remaining: float
    ahead: float  # size queued ahead of us at our price
    live_at: float  # placement time + ack latency
    dies_at: float = math.inf  # cancel time + cancel latency


class PaperVenue:
    """In-memory venue with post-only order lifecycle and a queue-position fill model.

    A resting order joins the back of the queue at its price level. A trade that
    prints strictly through the price fills it outright. A trade at the price
    (the touch) must first consume the size queued ahead; only the remainder
    fills us. Every trade fills at most its printed size.

    Latency: a new order goes live `ack_ms` after placement, and a cancel takes
    `cancel_ms` to land. A trade fills only orders live at the trade's timestamp,
    so a quote cancelled just before a price jump can still be picked off.

    ponytail: queue ahead only shrinks by trades, never by cancellations ahead,
    so fills are conservative; model cancel-driven queue decay if fills look too rare.
    ponytail: post-only is checked at submit, not re-checked on arrival; a late
    order that would cross fills as a trade-through (pessimistic).
    """

    def __init__(
        self,
        *,
        starting_cash: float,
        fee_bps: float,
        tick_size: float,
        max_order_size: float,
        ack_ms: float = 0.0,
        cancel_ms: float = 0.0,
    ) -> None:
        self._starting_cash = starting_cash
        self._fee_rate = fee_bps / 10_000.0
        self._tick_size = tick_size
        self._max_order_size = max_order_size
        self._ack_s = ack_ms / 1000.0
        self._cancel_s = cancel_ms / 1000.0
        self._now = 0.0
        self._book: OrderBook | None = None
        self._orders: dict[str, _Resting] = {}
        self._fill_queue: list[Fill] = []
        self._position = 0.0
        self._avg_entry = 0.0
        self._realized = 0.0
        self._fees = 0.0
        self._position_opened_ts: float | None = None
        self._attempts = 0
        self._rejects = 0
        self._fills = 0
        self._next_id = 0

    def on_block(self, event: BlockEvent) -> None:
        self._book = event.book
        self._now = event.ts
        for trade in event.trades:
            trade_left = trade.size
            for oid, rest in list(self._orders.items()):
                if trade_left <= 0:
                    break
                if not rest.live_at <= trade.ts < rest.dies_at:
                    continue
                order = rest.order
                # half-tick tolerance: prices are floats, 99.99 may be stored as 99.99000000000001
                half_tick = self._tick_size / 2
                through = (order.side == "buy" and trade.price < order.price - half_tick) or (
                    order.side == "sell" and trade.price > order.price + half_tick
                )
                # a sell aggressor hits resting bids; a buy aggressor lifts resting asks
                at_touch = abs(trade.price - order.price) < half_tick and trade.side != order.side
                if at_touch and not through:
                    eaten = min(rest.ahead, trade_left)
                    rest.ahead -= eaten
                    trade_left -= eaten
                elif not through:
                    continue
                qty = min(rest.remaining, trade_left)
                if qty <= 0:
                    continue
                fee = order.price * qty * self._fee_rate
                self._apply_fill(order.side, order.price, qty, fee)
                self._fill_queue.append(Fill(order.side, order.price, qty, fee))
                self._fills += 1
                rest.remaining -= qty
                trade_left -= qty
                if rest.remaining <= 0:
                    del self._orders[oid]
        for oid, rest in list(self._orders.items()):
            if rest.dies_at <= event.ts:
                del self._orders[oid]

    def drain_fills(self) -> tuple[Fill, ...]:
        fills = tuple(self._fill_queue)
        self._fill_queue.clear()
        return fills

    def book(self) -> OrderBook:
        if self._book is None:
            raise RuntimeError("paper venue has not seen a block yet")
        return self._book

    def inventory(self) -> float:
        return self._position

    def equity(self) -> float:
        return self._starting_cash + self.pnl().net

    def pnl(self) -> PnL:
        unrealized = 0.0
        if self._position != 0 and self._book is not None:
            unrealized = (self._book.mid - self._avg_entry) * self._position
        return PnL(realized=self._realized, unrealized=unrealized, fees=self._fees)

    def health(self) -> VenueHealth:
        if self._attempts == 0:
            return VenueHealth()
        return VenueHealth(
            fill_ratio=self._fills / self._attempts,
            reject_rate=self._rejects / self._attempts,
            slippage_bps=0.0,
        )

    def position_age_s(self, now: float) -> float:
        if self._position == 0 or self._position_opened_ts is None:
            return 0.0
        return max(0.0, now - self._position_opened_ts)

    def open_orders(self) -> tuple[Order, ...]:
        return tuple(r.order for r in self._orders.values() if r.dies_at == math.inf)

    async def cancel_all(self) -> int:
        """Cancel every open order; each stays fillable until its cancel lands."""
        pending = [r for r in self._orders.values() if r.dies_at == math.inf]
        if self._cancel_s == 0:
            self._orders = {oid: r for oid, r in self._orders.items() if r.dies_at != math.inf}
        for rest in pending:
            rest.dies_at = self._now + self._cancel_s
        return len(pending)

    async def place(self, order: Order) -> str:
        self._attempts += 1
        if order.size <= 0 or order.size > self._max_order_size:
            self._rejects += 1
            raise OrderRejected(
                f"size {order.size} outside (0, {self._max_order_size}]"
            )
        book = self.book()
        price = round(order.price / self._tick_size) * self._tick_size
        if order.post_only and book.two_sided:
            if order.side == "buy" and price >= book.best_ask.price:
                self._rejects += 1
                raise OrderRejected("post-only buy would cross the book")
            if order.side == "sell" and price <= book.best_bid.price:
                self._rejects += 1
                raise OrderRejected("post-only sell would cross the book")
        levels = book.bids if order.side == "buy" else book.asks
        ahead = sum(
            level.size for level in levels if abs(level.price - price) < self._tick_size / 2
        )
        self._next_id += 1
        oid = f"paper-{self._next_id}"
        self._orders[oid] = _Resting(
            order=Order(side=order.side, price=price, size=order.size, post_only=order.post_only),
            remaining=order.size,
            ahead=ahead,
            live_at=self._now + self._ack_s,
        )
        return oid

    async def flatten(self) -> Fill | None:
        await self.cancel_all()
        if self._position == 0:
            return None
        book = self.book()
        if self._position > 0:
            side, price = "sell", book.best_bid.price
        else:
            side, price = "buy", book.best_ask.price
        qty = abs(self._position)
        fee = price * qty * self._fee_rate
        self._apply_fill(side, price, qty, fee)
        self._fills += 1
        return Fill(side, price, qty, fee)

    def _apply_fill(self, side: str, price: float, qty: float, fee: float) -> None:
        self._fees += fee
        signed = qty if side == "buy" else -qty
        if self._position == 0:
            self._position_opened_ts = None
        increasing = self._position == 0 or (self._position > 0) == (signed > 0)
        if increasing:
            new_position = self._position + signed
            if new_position != 0:
                self._avg_entry = (
                    abs(self._position) * self._avg_entry + qty * price
                ) / abs(new_position)
            self._position = new_position
            if self._position_opened_ts is None:
                self._position_opened_ts = self._book.ts if self._book else 0.0
        else:
            closing = min(abs(signed), abs(self._position))
            if self._position > 0:
                self._realized += (price - self._avg_entry) * closing
            else:
                self._realized += (self._avg_entry - price) * closing
            self._position += signed
            if self._position == 0:
                self._avg_entry = 0.0
                self._position_opened_ts = None
            elif abs(signed) > closing:
                self._avg_entry = price
                self._position_opened_ts = self._book.ts if self._book else 0.0
