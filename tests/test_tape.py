from __future__ import annotations

import json

import pytest

from jev_trader.gatekeeper.tape import Tape, merged_records


def book(tape: Tape, ts: float, mid: float, bid_size: float = 10.0, ask_size: float = 10.0):
    tape.on_book(ts, [[mid - 0.01, bid_size]], [[mid + 0.01, ask_size]])


def test_missing_history_means_none():
    tape = Tape("AAVE")
    book(tape, 0.0, 100.0)
    book(tape, 60.0, 101.0)
    assert tape.change(5) is None  # only 1 minute of history
    assert tape.volume_ratio() is None
    assert tape.bids_stronger_for(180) is None
    assert tape.change(1) == pytest.approx(0.01)


def test_changes_low_and_volume_ratio():
    tape = Tape("AAVE")
    for minute in range(66):  # 66 minutes, price +0.1 per minute, $100 of volume per minute
        ts = minute * 60.0
        book(tape, ts, 100.0 + 0.1 * minute)
        tape.on_trade(ts + 30.0, 1.0, 100.0, "buy" if minute % 4 else "sell")  # mid-minute
    for second in range(0, 300, 10):  # $1,500 extra in the last 5 minutes
        tape.on_trade(65 * 60.0 - 295 + second, 1.0, 50.0, "buy")
    assert tape.change(5) == pytest.approx(106.5 / 106.0 - 1.0)
    assert tape.change(60) == pytest.approx(106.5 / 100.5 - 1.0)
    assert tape.low(15) == pytest.approx(105.0)
    # last 5 min: 5 x $100 + $1,500 = $2,000; each prior 5-min period: $500
    assert tape.volume_ratio() == pytest.approx(4.0)
    assert 0.5 < tape.buy_share(5) <= 1.0


def test_bids_stronger_for_three_minutes():
    tape = Tape("AAVE")
    for k in range(10):  # snapshots every 30 s; bids stronger only in the last 3 minutes
        book(tape, k * 30.0, 100.0, bid_size=20.0 if k >= 4 else 5.0)
    assert tape.bids_stronger_for(150) is True
    assert tape.bids_stronger_for(240) is False


def test_merged_records_interleave_coins_in_time_order(tmp_path):
    aave, btc = tmp_path / "aave.jsonl", tmp_path / "btc.jsonl"
    aave.write_text("".join(json.dumps({"kind": "book", "ts": t}) + "\n" for t in (1, 3, 5)))
    btc.write_text("".join(json.dumps({"kind": "book", "ts": t}) + "\n" for t in (2, 4)))
    order = [(coin, rec["ts"]) for coin, rec in merged_records({"AAVE": aave, "BTC": btc})]
    assert order == [("AAVE", 1), ("BTC", 2), ("AAVE", 3), ("BTC", 4), ("AAVE", 5)]
