from __future__ import annotations

from dataclasses import dataclass

from jev_trader.state.book import OrderBook


@dataclass(frozen=True, slots=True)
class Trade:
    ts: float
    price: float
    size: float
    side: str


@dataclass(frozen=True, slots=True)
class BlockEvent:
    block: int
    ts: float
    book: OrderBook
    trades: tuple[Trade, ...] = ()
    book_updates: int = 0
