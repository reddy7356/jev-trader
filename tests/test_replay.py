from __future__ import annotations

import json
import time

import pytest

from jev_trader.feeds.hyperliquid import to_records
from jev_trader.feeds.replay import ReplayFeed


def test_hyperliquid_messages_become_replay_records():
    book = {
        "channel": "l2Book",
        "data": {
            "coin": "SOL",
            "time": 1_000_500,
            "levels": [
                [{"px": "121.01", "sz": "5.5", "n": 3}, {"px": "121.0", "sz": "9", "n": 1}],
                [{"px": "121.02", "sz": "2", "n": 1}],
            ],
        },
    }
    trades = {
        "channel": "trades",
        "data": [
            {"coin": "SOL", "side": "B", "px": "121.02", "sz": "0.5", "time": 1_000_400},
            {"coin": "SOL", "side": "A", "px": "121.01", "sz": "1", "time": 1_000_450},
        ],
    }
    assert to_records(book, levels=1) == [
        {"kind": "book", "ts": 1000.5, "bids": [[121.01, 5.5]], "asks": [[121.02, 2.0]]}
    ]
    assert [(r["side"], r["px"], r["sz"]) for r in to_records(trades)] == [
        ("buy", 121.02, 0.5),
        ("sell", 121.01, 1.0),
    ]
    assert to_records({"channel": "subscriptionResponse", "data": {}}) == []


async def test_replay_groups_trades_into_the_following_block(tmp_path):
    def book(ts, bid):
        return {"kind": "book", "ts": ts, "bids": [[bid, 1.0]], "asks": [[bid + 0.02, 1.0]]}

    def trade(ts, side):
        return {"kind": "trade", "ts": ts, "px": 100.0, "sz": 1.0, "side": side}

    # written out of order on purpose: replay sorts by timestamp
    records = [book(10.0, 100.0), trade(10.7, "sell"), trade(10.2, "buy"), book(10.5, 100.01)]
    records += [trade(10.5, "buy"), book(11.0, 99.99)]
    path = tmp_path / "rec.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")

    before = time.time()
    events = [e async for e in ReplayFeed(path).events()]
    assert [e.block for e in events] == [0, 1, 2]
    assert [len(e.trades) for e in events] == [0, 2, 1]
    assert [t.side for t in events[1].trades] == ["buy", "buy"]
    assert events[2].trades[0].side == "sell"
    # shifted so the first block is "now", spacing preserved
    assert events[0].ts == pytest.approx(before, abs=1.0)
    assert events[2].ts - events[0].ts == pytest.approx(1.0)
    assert events[1].book.best_bid.price == 100.01

    assert len([e async for e in ReplayFeed(path, max_blocks=2).events()]) == 2
