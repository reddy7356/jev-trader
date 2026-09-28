"""Hyperliquid public market data (no key): a live block feed and a JSONL recorder.

One line per record:
    {"kind": "book", "ts": 1790426360.809, "bids": [[px, sz], ...], "asks": [[px, sz], ...]}
    {"kind": "trade", "ts": 1790426362.711, "px": 121.01, "sz": 0.58, "side": "sell"}

`side` is the aggressor: Hyperliquid "B" (buyer lifted the ask) -> "buy", "A" -> "sell".
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import websockets

from jev_trader.feeds.replay import book_from_record
from jev_trader.state.events import BlockEvent, Trade

WS_URL = "wss://api.hyperliquid.xyz/ws"
TESTNET_WS_URL = "wss://api.hyperliquid-testnet.xyz/ws"
_SIDES = {"B": "buy", "A": "sell"}


def to_records(message: dict[str, Any], levels: int = 10) -> list[dict[str, Any]]:
    """Translate one websocket message into replay records (drops everything else)."""
    channel = message.get("channel")
    data = message.get("data")
    if channel == "l2Book":
        bids, asks = data["levels"]
        return [
            {
                "kind": "book",
                "ts": data["time"] / 1000.0,
                "bids": [[float(lv["px"]), float(lv["sz"])] for lv in bids[:levels]],
                "asks": [[float(lv["px"]), float(lv["sz"])] for lv in asks[:levels]],
            }
        ]
    if channel == "trades":
        return [
            {
                "kind": "trade",
                "ts": trade["time"] / 1000.0,
                "px": float(trade["px"]),
                "sz": float(trade["sz"]),
                "side": _SIDES[trade["side"]],
            }
            for trade in data
        ]
    return []


async def stream(
    coin: str, url: str = WS_URL, levels: int = 10
) -> AsyncIterator[dict[str, Any]]:
    """Endless book + trade records for `coin`, reconnecting after a dropped socket."""
    while True:
        try:
            async with contextlib.aclosing(_stream_once(coin, url, levels)) as records:
                async for rec in records:
                    yield rec
        except (websockets.ConnectionClosed, OSError) as exc:
            print(f"connection lost ({exc!r}); reconnecting")
            await asyncio.sleep(2.0)


async def _stream_once(coin: str, url: str, levels: int) -> AsyncIterator[dict[str, Any]]:
    first_book_ts: float | None = None
    async with websockets.connect(url) as ws:
        # fast=True: a book every ~0.5s instead of every ~5s
        book_sub = {"type": "l2Book", "coin": coin, "fast": True}
        for sub in (book_sub, {"type": "trades", "coin": coin}):
            await ws.send(json.dumps({"method": "subscribe", "subscription": sub}))
        async for raw in ws:
            for rec in to_records(json.loads(raw), levels):
                if rec["kind"] == "book":
                    first_book_ts = first_book_ts or rec["ts"]
                # each (re)subscription opens with a backlog of older trades
                elif first_book_ts is None or rec["ts"] < first_book_ts:
                    continue
                yield rec


async def record(coin: str, path: Path, seconds: float, levels: int = 10) -> int:
    """Stream `coin` for `seconds` into `path`. Returns the number of books written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    books = 0

    async def write_all() -> None:
        nonlocal books
        with path.open("w", buffering=1) as out:
            async for rec in stream(coin, levels=levels):
                books += rec["kind"] == "book"
                out.write(json.dumps(rec) + "\n")

    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(write_all(), timeout=seconds)
    return books


class HyperliquidFeed:
    """Live blocks: one per book snapshot, with the trades printed since the previous one."""

    def __init__(self, coin: str, *, testnet: bool = True, max_blocks: int | None = None) -> None:
        self._coin = coin
        self._url = TESTNET_WS_URL if testnet else WS_URL
        self._max_blocks = max_blocks

    async def events(self) -> AsyncIterator[BlockEvent]:
        block = 0
        pending: list[Trade] = []
        # aclosing: shut the websocket now when we stop early, not at interpreter exit
        async with contextlib.aclosing(stream(self._coin, self._url)) as records:
            async for rec in records:
                if rec["kind"] == "trade":
                    pending.append(
                        Trade(ts=rec["ts"], price=rec["px"], size=rec["sz"], side=rec["side"])
                    )
                    continue
                if self._max_blocks is not None and block >= self._max_blocks:
                    return
                yield BlockEvent(
                    block=block,
                    ts=rec["ts"],
                    book=book_from_record(rec, rec["ts"]),
                    trades=tuple(pending),
                    book_updates=1,
                )
                pending = []
                block += 1
