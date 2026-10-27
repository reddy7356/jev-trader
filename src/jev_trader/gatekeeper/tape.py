"""Per-coin rolling market memory for the Phase 7 gatekeeper, plus a two-coin replay.

The gatekeeper decides on 30-second book snapshots and 5- to 60-minute windows, not
on every block, so the tape keeps ~2 hours of mids, trades and depth snapshots and
answers the rules' questions exactly. Anything without enough history returns None:
missing data means NO TRADE.
"""

from __future__ import annotations

import heapq
import itertools
import statistics
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from jev_trader.feeds.replay import read_records

HISTORY_S = 4 * 3600.0  # SLOW style: 13 x 15-min volume periods = 3.25 h
SNAPSHOT_EVERY_S = 30.0
DEPTH_LEVELS = 10  # the recorder keeps 10; AAVE's top 5 alone flip on noise


@dataclass(frozen=True, slots=True)
class Snapshot:
    ts: float
    bid_depth: float  # $ notional, top DEPTH_LEVELS levels
    ask_depth: float
    mid: float = 0.0
    best_bid_usd: float = 0.0  # $ resting at the best bid / best ask
    best_ask_usd: float = 0.0


class Tape:
    def __init__(self, coin: str) -> None:
        self.coin = coin
        self.ts = 0.0
        self.bid = self.ask = 0.0
        self.bids: list = []  # latest book levels, [[price, size], ...] best first
        self.asks: list = []
        self._mids: deque[tuple[float, float]] = deque()
        self._trades: deque[tuple[float, float, bool]] = deque()  # ts, $ notional, is_buy
        self.snapshots: deque[Snapshot] = deque()

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0

    @property
    def spread_pct(self) -> float:
        return (self.ask - self.bid) / self.mid * 100.0 if self.mid else 0.0

    def on_book(self, ts: float, bids: list, asks: list) -> None:
        self.ts, self.bid, self.ask = ts, bids[0][0], asks[0][0]
        self.bids, self.asks = bids, asks
        self._mids.append((ts, self.mid))
        if not self.snapshots or ts - self.snapshots[-1].ts >= SNAPSHOT_EVERY_S:
            depth = [sum(px * sz for px, sz in side[:DEPTH_LEVELS]) for side in (bids, asks)]
            best = [side[0][0] * side[0][1] for side in (bids, asks)]
            self.snapshots.append(Snapshot(ts, *depth, self.mid, *best))
        cutoff = ts - HISTORY_S
        while self._mids and self._mids[0][0] < cutoff:
            self._mids.popleft()
        while self._trades and self._trades[0][0] < cutoff:
            self._trades.popleft()
        while self.snapshots and self.snapshots[0].ts < cutoff:
            self.snapshots.popleft()

    def on_trade(self, ts: float, price: float, size: float, side: str) -> None:
        self._trades.append((ts, price * size, side == "buy"))

    def _covers(self, seconds: float) -> bool:
        return bool(self._mids) and self.ts - self._mids[0][0] >= seconds

    def change(self, minutes: float) -> float | None:
        """Fractional mid change over the last `minutes` (0.01 = +1%)."""
        if not self._covers(minutes * 60.0):
            return None
        target = self.ts - minutes * 60.0
        past = next(mid for ts, mid in reversed(self._mids) if ts <= target)
        return self.mid / past - 1.0

    def low(self, minutes: float) -> float | None:
        if not self._covers(minutes * 60.0):
            return None
        cutoff = self.ts - minutes * 60.0
        return min(mid for ts, mid in self._mids if ts >= cutoff)

    def volume(self, start: float, end: float) -> float:
        return sum(usd for ts, usd, _ in self._trades if start <= ts < end)

    def volume_ratio(self, periods: int = 12, period_s: float = 300.0) -> float | None:
        """Last 5-min $ volume / median of the `periods` 5-min volumes before it."""
        if not self._covers((periods + 1) * period_s):
            return None
        now = self.ts
        prior = [
            self.volume(now - (k + 1) * period_s, now - k * period_s)
            for k in range(1, periods + 1)
        ]
        median = statistics.median(prior)
        return self.volume(now - period_s, now + 1e-9) / median if median > 0 else None

    def buy_share(self, minutes: float = 5.0) -> float | None:
        """Share of aggressive (market) $ volume that was buying, last `minutes`."""
        cutoff = self.ts - minutes * 60.0
        recent = [(usd, buy) for ts, usd, buy in self._trades if ts >= cutoff]
        total = sum(usd for usd, _ in recent)
        return sum(usd for usd, buy in recent if buy) / total if total > 0 else None

    def timeline(self, slices: int = 10, step_s: float = 30.0) -> list[dict[str, float]]:
        """The last `slices` steps of `step_s`, oldest first: what price, flow and book did.

        Lets a judge see relationships a window total hides, e.g. heavy buying while
        price stays flat (absorption) versus buying that lifts price (acceptance).
        """
        snaps: list[Snapshot] = []
        for snap in reversed(self.snapshots):  # newest first, one per step
            if not snaps or snaps[-1].ts - snap.ts >= step_s - 1e-6:
                snaps.append(snap)
                if len(snaps) == slices + 1:
                    break
        snaps.reverse()
        steps = []
        for before, after in itertools.pairwise(snaps):
            trades = [(usd, buy) for ts, usd, buy in self._trades if before.ts <= ts < after.ts]
            steps.append(
                {
                    "mid_change_bps": round((after.mid / before.mid - 1) * 1e4, 2),
                    "bought_usd_k": round(sum(u for u, buy in trades if buy) / 1e3, 1),
                    "sold_usd_k": round(sum(u for u, buy in trades if not buy) / 1e3, 1),
                    "bid_depth_usd_k": round(after.bid_depth / 1e3, 1),
                    "ask_depth_usd_k": round(after.ask_depth / 1e3, 1),
                    "best_bid_usd_k": round(after.best_bid_usd / 1e3, 1),
                    "best_ask_usd_k": round(after.best_ask_usd / 1e3, 1),
                }
            )
        return steps

    def bids_stronger_share(self, seconds: float) -> float | None:
        """Share of the 30 s snapshots in the last `seconds` with more bid than ask depth."""
        if not self._covers(seconds):
            return None
        recent = [s for s in self.snapshots if s.ts >= self.ts - seconds]
        return sum(s.bid_depth > s.ask_depth for s in recent) / len(recent) if recent else None


def fill_price(levels: list, quantity: float) -> float:
    """Average price of a market order of `quantity` walking `levels` (best first).

    Anything beyond the recorded levels is assumed to fill at the last one; callers
    cap orders well inside visible depth, so that tail should not occur.
    """
    left, cost = quantity, 0.0
    for price, size in levels:
        take = min(left, size)
        cost += take * price
        left -= take
        if left <= 0:
            break
    cost += max(left, 0.0) * levels[-1][0]
    return cost / quantity if quantity > 0 else levels[0][0]


def merged_records(paths: dict[str, Path]) -> Iterator[tuple[str, dict]]:
    """(coin, record) from several recordings, merged in time order.

    Streams line by line (a 3-day recording is ~300 MB) and reads `.jsonl.gz`
    archives as well as plain files. Each file is in arrival order, which is time
    order to within milliseconds: fine for 30 s / 5 min rules.
    """

    def one(coin: str, path: Path) -> Iterator[tuple[float, str, dict]]:
        for record in read_records(path):
            yield record["ts"], coin, record

    streams = [one(coin, path) for coin, path in paths.items()]
    for _, coin, record in heapq.merge(*streams, key=lambda row: row[0]):
        yield coin, record
