from __future__ import annotations

import asyncio
import random
import time
from collections.abc import AsyncIterator

from jev_trader.state.book import Level, OrderBook
from jev_trader.state.events import BlockEvent, Trade


class SyntheticFeed:
    """Seeded synthetic block feed: random-walk mid with regime shifts.

    Deterministic for a given seed. `realtime=True` paces blocks at `block_ms`;
    otherwise it runs accelerated so paper runs finish quickly.
    """

    def __init__(
        self,
        *,
        seed: int = 7,
        block_ms: int = 300,
        mid0: float = 100.0,
        tick_size: float = 0.01,
        levels: int = 3,
        base_size: float = 10.0,
        realtime: bool = False,
        max_blocks: int | None = None,
    ) -> None:
        self._seed = seed
        self._block_ms = block_ms
        self._mid0 = mid0
        self._tick = tick_size
        self._levels = levels
        self._base_size = base_size
        self._realtime = realtime
        self._max_blocks = max_blocks

    async def events(self) -> AsyncIterator[BlockEvent]:
        rng = random.Random(self._seed)
        mid = self._mid0
        ts = time.time()
        block = 0
        drift = 0.0
        vol = 0.0003
        regime_left = rng.randint(50, 200)

        while self._max_blocks is None or block < self._max_blocks:
            regime_left -= 1
            if regime_left <= 0:
                kind = rng.choice(["calm", "calm", "trend", "volatile", "crash"])
                if kind == "trend":
                    drift = rng.choice([-1.0, 1.0]) * 0.0002
                    vol = 0.0004
                elif kind == "volatile":
                    drift = 0.0
                    vol = 0.0015
                elif kind == "crash":
                    drift = -0.0008
                    vol = 0.003
                else:
                    drift = 0.0
                    vol = 0.0003
                regime_left = rng.randint(50, 300)

            ret = rng.gauss(drift, vol)
            mid = max(mid * (1.0 + ret), self._tick * 10)
            book = self._make_book(rng, mid)
            trades = self._make_trades(rng, book, ret, ts)

            yield BlockEvent(
                block=block,
                ts=ts,
                book=book,
                trades=trades,
                book_updates=rng.randint(1, 5),
            )

            ts += self._block_ms / 1000.0
            block += 1
            if self._realtime:
                await asyncio.sleep(self._block_ms / 1000.0)

    def _make_book(self, rng: random.Random, mid: float) -> OrderBook:
        spread_ticks = rng.randint(1, 4)
        bid_px = (mid - spread_ticks * self._tick / 2.0) // self._tick * self._tick
        ask_px = bid_px + spread_ticks * self._tick
        bids = []
        asks = []
        bid_level = bid_px
        ask_level = ask_px
        for _ in range(self._levels):
            bids.append(
                Level(round(bid_level, 6), self._base_size * (0.4 + rng.random()))
            )
            asks.append(
                Level(round(ask_level, 6), self._base_size * (0.4 + rng.random()))
            )
            bid_level -= self._tick * rng.randint(1, 3)
            ask_level += self._tick * rng.randint(1, 3)
        return OrderBook(
            ts=0.0,
            bids=tuple(bids),
            asks=tuple(asks),
        )

    def _make_trades(
        self,
        rng: random.Random,
        book: OrderBook,
        block_return: float,
        ts: float,
    ) -> tuple[Trade, ...]:
        if rng.random() < 0.3:
            return ()
        count = rng.randint(1, 3)
        block_s = self._block_ms / 1000.0
        trades = []
        for i in range(count):
            aligned = rng.random() < 0.7
            if block_return == 0.0:
                side = "buy" if rng.random() < 0.5 else "sell"
            elif aligned:
                side = "buy" if block_return > 0 else "sell"
            else:
                side = "sell" if block_return > 0 else "buy"
            price = book.best_ask.price if side == "buy" else book.best_bid.price
            trades.append(
                Trade(
                    # spread evenly over the interval since the previous block
                    ts=ts - block_s + (i + 1) / (count + 1) * block_s,
                    price=price,
                    size=self._base_size * (0.2 + rng.random()),
                    side=side,
                )
            )
        return tuple(trades)
