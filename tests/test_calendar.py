from __future__ import annotations

from datetime import datetime

from jev_trader.gatekeeper.calendar import ET, event_block


def at(*args: int) -> float:
    return datetime(*args, tzinfo=ET).timestamp()


def test_weekday_morning_window_with_30_minute_lead():
    assert event_block(at(2026, 10, 1, 7, 44)) is None  # Thursday, before the lead
    assert event_block(at(2026, 10, 1, 7, 45)) is not None  # 30 min before 8:15
    assert event_block(at(2026, 10, 1, 10, 29)) is not None
    assert event_block(at(2026, 10, 1, 10, 30)) is None
    assert event_block(at(2026, 10, 3, 9, 0)) is None  # Saturday: no US releases


def test_fomc_decision_day_afternoon():
    assert "FOMC" in event_block(at(2026, 10, 28, 14, 0))
    assert event_block(at(2026, 10, 28, 12, 59)) is None
    assert event_block(at(2026, 10, 27, 14, 0)) is None  # first meeting day: no decision
