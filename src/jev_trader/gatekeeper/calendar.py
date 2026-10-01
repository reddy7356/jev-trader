"""US event calendar: when the gatekeeper must not open a new long.

A hand-kept stand-in for a news feed. Blocked = inside an event window, or within
LEAD_MIN minutes before one opens (the rule: no new trade if an event "is active or
occurs within 30 minutes").

- Every weekday 8:15-10:30 ET: 8:30 releases (CPI, jobs, PPI, retail sales), the
  9:30 stock open, 10:00 releases (ISM, JOLTS, consumer sentiment).
- FOMC decision days 13:30-15:30 ET: decision at 14:00, press conference at 14:30.
  Dates from federalreserve.gov (fetched 2026-10-01; each is tentative until confirmed
  at the meeting before it). Add 2028 when the Fed publishes it.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
LEAD_MIN = 30
DAILY_WINDOW = (time(8, 15), time(10, 30), "US data releases and stock open")
FOMC_WINDOW = (time(13, 30), time(15, 30), "FOMC decision and press conference")
FOMC_DECISION_DAYS = {  # second day of each meeting
    date(2026, 1, 28), date(2026, 3, 18), date(2026, 4, 29), date(2026, 6, 17),
    date(2026, 7, 29), date(2026, 9, 16), date(2026, 10, 28), date(2026, 12, 9),
    date(2027, 1, 27), date(2027, 3, 17), date(2027, 4, 28), date(2027, 6, 9),
    date(2027, 7, 28), date(2027, 9, 15), date(2027, 10, 27), date(2027, 12, 8),
}  # fmt: skip


def event_block(ts: float) -> str | None:
    """Why a new long is blocked at unix time `ts`, or None if no event is near."""
    now = datetime.fromtimestamp(ts, ET)
    windows = []
    if now.weekday() < 5:
        windows.append(DAILY_WINDOW)
    if now.date() in FOMC_DECISION_DAYS:
        windows.append(FOMC_WINDOW)
    for start, end, name in windows:
        opens = datetime.combine(now.date(), start, ET) - timedelta(minutes=LEAD_MIN)
        if opens <= now < datetime.combine(now.date(), end, ET):
            return f"{name} ({start:%H:%M}-{end:%H:%M} ET, blocked from {opens:%H:%M})"
    return None
