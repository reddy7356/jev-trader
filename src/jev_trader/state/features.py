from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

from jev_trader.state.events import BlockEvent, Trade


@dataclass(frozen=True, slots=True)
class MarketFeatures:
    ret_1m: float
    ret_5m: float
    ret_30m: float
    realized_vol_short: float
    realized_vol_medium: float
    vol_ratio: float
    aggressive_buy_ratio: float
    trade_intensity: float
    cancel_intensity: float


class FeatureEngine:
    """Deterministic feature computation over a rolling window of block events."""

    def __init__(
        self,
        *,
        window_s: float = 1800.0,
        short_vol_s: float = 30.0,
        medium_vol_s: float = 300.0,
        flow_s: float = 60.0,
        intensity_s: float = 30.0,
    ) -> None:
        self._window_s = window_s
        self._short_vol_s = short_vol_s
        self._medium_vol_s = medium_vol_s
        self._flow_s = flow_s
        self._intensity_s = intensity_s
        self._mids: deque[tuple[float, float]] = deque()
        self._trades: deque[Trade] = deque()
        self._updates: deque[tuple[float, int]] = deque()

    def _prune(self, now: float) -> None:
        cutoff = now - self._window_s
        while self._mids and self._mids[0][0] < cutoff:
            self._mids.popleft()
        while self._trades and self._trades[0].ts < cutoff:
            self._trades.popleft()
        while self._updates and self._updates[0][0] < cutoff:
            self._updates.popleft()

    def _mid_ago(self, now: float, seconds: float) -> float | None:
        target = now - seconds
        for ts, mid in reversed(self._mids):
            if ts <= target:
                return mid
        return None

    def _ret(self, now: float, seconds: float, mid: float) -> float:
        past = self._mid_ago(now, seconds)
        if past is None or past <= 0:
            return 0.0
        return mid / past - 1.0

    def _realized_vol(self, now: float, seconds: float) -> float:
        cutoff = now - seconds
        points = [mid for ts, mid in self._mids if ts >= cutoff and mid > 0]
        if len(points) < 3:
            return 0.0
        returns = [
            math.log(points[i] / points[i - 1])
            for i in range(1, len(points))
        ]
        if len(returns) < 2:
            return 0.0
        mean = sum(returns) / len(returns)
        var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
        return math.sqrt(var) * math.sqrt(len(returns) / seconds)

    def _buy_ratio(self, now: float) -> float:
        cutoff = now - self._flow_s
        buy = sell = 0.0
        for trade in self._trades:
            if trade.ts < cutoff:
                continue
            if trade.side == "buy":
                buy += trade.size
            else:
                sell += trade.size
        total = buy + sell
        if total <= 0:
            return 0.5
        return buy / total

    def _intensity(self, now: float, count: int) -> float:
        return count / self._intensity_s

    def update(self, event: BlockEvent) -> MarketFeatures:
        book = event.book
        if book.two_sided and book.mid > 0:
            self._mids.append((event.ts, book.mid))
        self._trades.extend(event.trades)
        self._updates.append((event.ts, event.book_updates))
        self._prune(event.ts)

        mid = book.mid if book.two_sided else 0.0
        short_vol = self._realized_vol(event.ts, self._short_vol_s)
        medium_vol = self._realized_vol(event.ts, self._medium_vol_s)
        vol_ratio = short_vol / medium_vol if medium_vol > 0 else 1.0

        recent_trades = sum(1 for trade in self._trades if trade.ts >= event.ts - self._intensity_s)
        recent_updates = sum(
            updates for ts, updates in self._updates if ts >= event.ts - self._intensity_s
        )

        return MarketFeatures(
            ret_1m=self._ret(event.ts, 60.0, mid),
            ret_5m=self._ret(event.ts, 300.0, mid),
            ret_30m=self._ret(event.ts, 1800.0, mid),
            realized_vol_short=short_vol,
            realized_vol_medium=medium_vol,
            vol_ratio=vol_ratio,
            aggressive_buy_ratio=self._buy_ratio(event.ts),
            trade_intensity=self._intensity(event.ts, recent_trades),
            cancel_intensity=self._intensity(event.ts, recent_updates),
        )
