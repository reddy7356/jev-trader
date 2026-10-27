from __future__ import annotations

import asyncio
import contextlib
import gzip
import json
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

from jev_trader.state.book import Level, OrderBook
from jev_trader.state.events import BlockEvent, Trade


def book_from_record(rec: dict, ts: float) -> OrderBook:
    return OrderBook(
        ts=ts,
        bids=tuple(Level(px, sz) for px, sz in rec["bids"]),
        asks=tuple(Level(px, sz) for px, sz in rec["asks"]),
    )


def read_records(path: Path) -> Iterator[dict]:
    """Records from a JSONL recording, plain or gzipped.

    `record_loop.sh` gzips each finished day (`*.jsonl.gz`), so every replay path
    has to accept both forms; gzip is detected by the suffix, not the content.

    One line is held back so a partial last line — a recorder killed mid-write, or a
    file read while it is still being appended — is dropped instead of raising.
    """
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as lines:
        previous: str | None = None
        for line in lines:
            if previous is not None and previous.strip():
                yield json.loads(previous)
            previous = line
        if previous is not None and previous.strip():
            with contextlib.suppress(json.JSONDecodeError):
                yield json.loads(previous)


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
        records = list(read_records(self._path))
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
            yield BlockEvent(
                block=block,
                ts=ts,
                book=book_from_record(rec, ts),
                trades=tuple(pending),
                book_updates=1,
            )
            pending = []
            last_ts = ts
            block += 1
