from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from pathlib import Path

from jev_trader.state.book import Level, OrderBook
from jev_trader.state.events import BlockEvent, Trade


class ReplayFeed:
    """Replays a recorded book + trades JSONL (see feeds/hyperliquid.py) as blocks.

    Each book snapshot is one block, carrying the trades printed since the
    previous snapshot. Timestamps are shifted so the first block is "now":
    the loop's stale-data check compares event time to the wall clock.

    ponytail: loads the whole file; stream it if recordings outgrow memory.
    """

    def __init__(
        self, path: Path, *, max_blocks: int | None = None, realtime: bool = False
    ) -> None:
        self._path = path
        self._max_blocks = max_blocks
        self._realtime = realtime

    async def events(self) -> AsyncIterator[BlockEvent]:
        records = [json.loads(line) for line in self._path.read_text().splitlines() if line]
        # trades at the same millisecond as a book print before it
        records.sort(key=lambda r: (r["ts"], r["kind"] == "book"))
        books = [r for r in records if r["kind"] == "book"]
        if not books:
            return
        shift = time.time() - books[0]["ts"]

        block = 0
        pending: list[Trade] = []
        last_ts: float | None = None
        for rec in records:
            ts = rec["ts"] + shift
            if rec["kind"] == "trade":
                pending.append(Trade(ts=ts, price=rec["px"], size=rec["sz"], side=rec["side"]))
                continue
            if self._max_blocks is not None and block >= self._max_blocks:
                return
            if self._realtime and last_ts is not None:
                await asyncio.sleep(max(0.0, ts - last_ts))
            book = OrderBook(
                ts=ts,
                bids=tuple(Level(px, sz) for px, sz in rec["bids"]),
                asks=tuple(Level(px, sz) for px, sz in rec["asks"]),
            )
            yield BlockEvent(block=block, ts=ts, book=book, trades=tuple(pending), book_updates=1)
            pending = []
            last_ts = ts
            block += 1
