"""Record Hyperliquid L2 book + trades to JSONL for replay (public data, no key).

One line per record:
    {"kind": "book", "ts": 1790426360.809, "bids": [[px, sz], ...], "asks": [[px, sz], ...]}
    {"kind": "trade", "ts": 1790426362.711, "px": 121.01, "sz": 0.58, "side": "sell"}

`side` is the aggressor: Hyperliquid "B" (buyer lifted the ask) -> "buy", "A" -> "sell".
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, TextIO

import websockets

WS_URL = "wss://api.hyperliquid.xyz/ws"
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


async def record(coin: str, path: Path, seconds: float, levels: int = 10) -> int:
    """Stream `coin` for `seconds` into `path`. Returns the number of books written.

    Reconnects after a dropped socket; the replay just sees a longer gap between blocks.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    books = 0
    with path.open("w", buffering=1) as out:
        while deadline - loop.time() > 0:
            try:
                books += await _stream(coin, out, deadline, levels)
            except (websockets.ConnectionClosed, OSError) as exc:
                print(f"connection lost ({exc!r}); reconnecting")
                await asyncio.sleep(2.0)
    return books


async def _stream(coin: str, out: TextIO, deadline: float, levels: int) -> int:
    loop = asyncio.get_running_loop()
    books = 0
    first_book_ts: float | None = None
    async with websockets.connect(WS_URL) as ws:
        # fast=True: a book every ~0.5s instead of every ~5s
        book_sub = {"type": "l2Book", "coin": coin, "fast": True}
        for sub in (book_sub, {"type": "trades", "coin": coin}):
            await ws.send(json.dumps({"method": "subscribe", "subscription": sub}))
        while (left := deadline - loop.time()) > 0:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=left)
            except TimeoutError:
                break
            for rec in to_records(json.loads(raw), levels):
                if rec["kind"] == "book":
                    books += 1
                    if first_book_ts is None:
                        first_book_ts = rec["ts"]
                # each (re)subscription opens with a backlog of older trades
                elif first_book_ts is None or rec["ts"] < first_book_ts:
                    continue
                out.write(json.dumps(rec) + "\n")
    return books
