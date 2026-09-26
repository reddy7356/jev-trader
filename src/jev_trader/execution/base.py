from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from jev_trader.domain import Fill, Order, PnL
from jev_trader.state.book import OrderBook
from jev_trader.state.events import BlockEvent


class OrderRejected(Exception):
    """The venue refused the order (size, tick, post-only, balance)."""


@dataclass(frozen=True, slots=True)
class VenueHealth:
    fill_ratio: float = 0.0
    reject_rate: float = 0.0
    slippage_bps: float = 0.0


class VenueAdapter(Protocol):
    def on_block(self, event: BlockEvent) -> None: ...

    def drain_fills(self) -> tuple[Fill, ...]: ...

    def book(self) -> OrderBook: ...

    def inventory(self) -> float: ...

    def equity(self) -> float: ...

    def pnl(self) -> PnL: ...

    def health(self) -> VenueHealth: ...

    def position_age_s(self, now: float) -> float: ...

    def open_orders(self) -> dict[str, Order]: ...

    async def cancel(self, oid: str) -> bool: ...

    async def cancel_all(self) -> int: ...

    async def place(self, order: Order) -> str: ...

    async def flatten(self) -> Fill | None: ...
