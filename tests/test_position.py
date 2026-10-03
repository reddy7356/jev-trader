from __future__ import annotations

import pytest

from jev_trader.gatekeeper.gates import MAKER_FEE, TAKER_FEE, TradePlan
from jev_trader.gatekeeper.position import Position
from jev_trader.gatekeeper.tape import Tape

PLAN = TradePlan(entry=100.0, stop=99.0, quantity=10.0, target1=101.6, target2=103.2)
T0 = 4000.0


def tapes(btc_drop: float = 0.0) -> tuple[Tape, Tape]:
    aave, btc = Tape("AAVE"), Tape("BTC")
    for second in range(0, 4001, 10):
        mid = 80_000.0 * (1 + btc_drop * second / 4000)
        btc.on_book(second, [[mid - 1, 1.0]], [[mid + 1, 1.0]])
        btc.on_trade(second, mid, 0.01, "sell" if btc_drop else "buy")
        btc.on_trade(second, mid, 0.01, "buy")
    aave.on_book(T0, [[99.99, 50.0]], [[100.0, 50.0]])
    return aave, btc


def move(aave: Tape, btc: Tape, ts: float, bid: float) -> None:
    aave.on_book(ts, [[bid, 50.0]], [[bid + 0.01, 50.0]])
    btc.on_book(ts, [[79_999.0, 1.0]], [[80_001.0, 1.0]])


def test_stop_sells_everything_at_the_bid():
    aave, btc = tapes()
    pos = Position.open(PLAN, T0, aave)
    move(aave, btc, T0 + 5, 98.9)
    assert pos.check(aave, btc, T0 + 5) == "stop"
    assert pos.closed
    expected = -100.0 * 10 * (1 + TAKER_FEE) + 98.9 * 10 * (1 - TAKER_FEE)
    assert pos.pnl == pytest.approx(expected)


def test_t1_sells_half_and_lifts_the_stop_to_entry_then_t2():
    aave, btc = tapes()
    pos = Position.open(PLAN, T0, aave)
    move(aave, btc, T0 + 5, 101.7)
    assert pos.check(aave, btc, T0 + 5) == "T1"
    assert pos.quantity == pytest.approx(5.0) and pos.stop == 100.0
    move(aave, btc, T0 + 6, 103.3)
    assert pos.check(aave, btc, T0 + 6) == "T2"
    expected = -1000 * (1 + TAKER_FEE) + (101.6 * 5 + 103.2 * 5) * (1 - MAKER_FEE)
    assert pos.pnl == pytest.approx(expected)


def test_after_t1_the_rest_exits_at_entry_not_below():
    aave, btc = tapes()
    pos = Position.open(PLAN, T0, aave)
    move(aave, btc, T0 + 5, 101.7)
    pos.check(aave, btc, T0 + 5)
    move(aave, btc, T0 + 6, 99.99)
    assert pos.check(aave, btc, T0 + 6) == "stop at entry after T1"
    assert pos.closed and pos.pnl > 0


def test_time_stop_jev_thesis_and_btc_risk_off():
    aave, btc = tapes()
    pos = Position.open(PLAN, T0, aave)
    move(aave, btc, T0 + 3599, 100.1)
    assert pos.check(aave, btc, T0 + 3599, thesis_failed=0.79) is None
    move(aave, btc, T0 + 3600, 100.1)
    assert pos.check(aave, btc, T0 + 3600).startswith("time stop")

    pos = Position.open(PLAN, T0, aave)
    assert pos.check(aave, btc, T0 + 3600, thesis_failed=0.85).startswith("Jev")

    aave, btc = tapes(btc_drop=-0.04)
    pos = Position.open(PLAN, T0, aave)
    assert pos.check(aave, btc, T0).startswith("BTC risk-off")


def test_bid_collapse_exit_needs_a_lasting_collapse_and_heavy_selling():
    _, btc = tapes()
    aave = Tape("AAVE")
    for k in range(11):  # the 5 minutes before entry: bids around $5,000
        aave.on_book(T0 - 300 + 30 * k, [[99.99, 50.0]], [[100.0, 50.0]])
    pos = Position.open(PLAN, T0, aave)
    assert pos.entry_bid_depth == pytest.approx(99.99 * 50)

    def snap(ts, bid_size, sold):
        aave.on_book(ts, [[99.99, bid_size]], [[100.0, 50.0]])
        btc.on_book(ts, [[79_999.0, 1.0]], [[80_001.0, 1.0]])
        aave.on_trade(ts, 99.99, sold, "sell")

    snap(T0 + 30, 10.0, 5.0)  # one thin snapshot with selling: not yet
    assert pos.check(aave, btc, T0 + 30) is None
    snap(T0 + 60, 50.0, 5.0)  # bids come back: the streak resets
    assert pos.check(aave, btc, T0 + 60) is None
    snap(T0 + 90, 10.0, 5.0)
    snap(T0 + 120, 10.0, 5.0)  # two thin snapshots in a row + sellers dominate
    assert pos.check(aave, btc, T0 + 120) == "AAVE bids collapsed and sellers dominate"
