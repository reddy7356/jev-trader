from __future__ import annotations

import gzip
import json
import time
from pathlib import Path

import pytest

from jev_trader.feeds.hyperliquid import to_records
from jev_trader.feeds.replay import ReplayFeed, read_records


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


def test_read_records_accepts_plain_and_gzipped_files(tmp_path):
    records = [
        {"kind": "book", "ts": 10.0, "bids": [[100.0, 1.0]], "asks": [[100.02, 1.0]]},
        {"kind": "trade", "ts": 10.2, "px": 100.0, "sz": 0.5, "side": "buy"},
    ]
    payload = "\n".join(json.dumps(r) for r in records) + "\n\n"  # trailing blank line
    plain = tmp_path / "AAVE_20261020-0000.jsonl"
    plain.write_text(payload)
    archived = tmp_path / "AAVE_20261021-0000.jsonl.gz"
    archived.write_bytes(gzip.compress(payload.encode()))

    assert list(read_records(plain)) == records
    assert list(read_records(archived)) == records


def test_read_records_drops_a_partial_last_line(tmp_path):
    complete = {"kind": "book", "ts": 10.0, "bids": [[100.0, 1.0]], "asks": [[100.1, 1.0]]}
    path = tmp_path / "AAVE_20261020-0000.jsonl"
    # a recorder killed mid-write leaves half a record at the end
    path.write_text(json.dumps(complete) + "\n" + '{"kind": "book", "ts": 11.0, "bi')

    assert list(read_records(path)) == [complete]


def test_join_days_keeps_only_complete_lines(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    import slow_replay

    folder = tmp_path / "rec"
    folder.mkdir()
    complete = {"kind": "book", "ts": 1.0, "bids": [[100.0, 1.0]], "asks": [[100.1, 1.0]]}
    (folder / "AAVE_20261020-0000.jsonl").write_text(
        json.dumps(complete) + "\n" + '{"kind": "book", "ts": 2.0, "bi'  # killed mid-write
    )
    out = tmp_path / "joined.jsonl"

    assert slow_replay.join_days(folder, "AAVE", "99999999", out) == ["20261020"]
    assert out.read_text() == json.dumps(complete) + "\n"


async def test_replay_feed_reads_a_gzipped_recording(tmp_path):
    def book(ts, bid):
        return {"kind": "book", "ts": ts, "bids": [[bid, 1.0]], "asks": [[bid + 0.02, 1.0]]}

    payload = "\n".join(json.dumps(r) for r in [book(10.0, 100.0), book(11.0, 99.99)]) + "\n"
    plain = tmp_path / "rec.jsonl"
    plain.write_text(payload)
    archived = tmp_path / "rec.jsonl.gz"
    archived.write_bytes(gzip.compress(payload.encode()))

    plain_events = [e async for e in ReplayFeed(plain).events()]
    gz_events = [e async for e in ReplayFeed(archived).events()]
    assert [(e.block, e.book.best_bid.price) for e in gz_events] == [
        (e.block, e.book.best_bid.price) for e in plain_events
    ]
    assert gz_events[1].book.best_ask.price == pytest.approx(100.01)


async def test_live_stream_reconnects_after_the_server_refuses(monkeypatch):
    import websockets.exceptions

    from jev_trader.feeds import hyperliquid

    attempts = []

    async def flaky(coin, url, levels):
        attempts.append(1)
        if len(attempts) == 1:  # first connection: the server answers HTTP 502
            raise websockets.exceptions.InvalidStatus(type("R", (), {"status_code": 502})())
        yield {"kind": "book", "ts": 1.0}

    async def no_sleep(_):
        return None

    monkeypatch.setattr(hyperliquid, "_stream_once", flaky)
    monkeypatch.setattr(hyperliquid.asyncio, "sleep", no_sleep)
    stream = hyperliquid.stream("AAVE")
    assert (await anext(stream))["kind"] == "book"
    assert len(attempts) == 2
    await stream.aclose()
