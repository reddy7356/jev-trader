from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Level:
    price: float
    size: float


@dataclass(frozen=True, slots=True)
class OrderBook:
    ts: float
    bids: tuple[Level, ...]
    asks: tuple[Level, ...]

    @property
    def best_bid(self) -> Level | None:
        return self.bids[0] if self.bids else None

    @property
    def best_ask(self) -> Level | None:
        return self.asks[0] if self.asks else None

    @property
    def two_sided(self) -> bool:
        return bool(self.bids) and bool(self.asks)

    @property
    def mid(self) -> float:
        if not self.two_sided:
            raise ValueError("book is one-sided")
        return (self.bids[0].price + self.asks[0].price) / 2.0

    @property
    def spread(self) -> float:
        if not self.two_sided:
            raise ValueError("book is one-sided")
        return self.asks[0].price - self.bids[0].price

    @property
    def spread_bps(self) -> float:
        mid = self.mid
        if mid <= 0:
            return 0.0
        return 10_000.0 * self.spread / mid

    @property
    def microprice(self) -> float:
        if not self.two_sided:
            raise ValueError("book is one-sided")
        bid, ask = self.bids[0], self.asks[0]
        total = bid.size + ask.size
        if total <= 0:
            return self.mid
        return (bid.price * ask.size + ask.price * bid.size) / total

    def depth(self, levels: int = 3) -> tuple[float, float]:
        bid = sum(level.size for level in self.bids[:levels])
        ask = sum(level.size for level in self.asks[:levels])
        return bid, ask

    def imbalance(self, levels: int = 3) -> float:
        bid, ask = self.depth(levels)
        total = bid + ask
        if total <= 0:
            return 0.0
        return (bid - ask) / total
