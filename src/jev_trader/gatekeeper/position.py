"""Phase 7 position monitor and long-only paper fills (the user's prompt 3).

Spot-like: long only, no leverage. Entry is a market buy priced through the offers
(see gates.plan_trade). Every AAVE book update runs `Position.check`; stops and forced
exits are market sells walking the bids (taker), targets sell at the target (maker).
The stop only ever moves up. Never averages down.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from jev_trader.gatekeeper.gates import (
    FAST,
    MAKER_FEE,
    TAKER_FEE,
    Style,
    TradePlan,
    btc_risk_off,
)
from jev_trader.gatekeeper.tape import Tape, fill_price

THESIS_FAILED_MIN = 0.8  # Jev probability that the entry thesis failed
# "bids collapse and sellers dominate": the last COLLAPSE_SNAPSHOTS 30 s snapshots all
# show bid depth under BID_COLLAPSE x the typical (median) depth of the 5 minutes before
# entry, while aggressive buyers are under SELLERS_DOMINATE of the last SELLING_MIN minutes.
# (Comparing to the entry moment itself, when bids were unusually strong, exited 10 of 11
# replay trades on ordinary noise within minutes.)
BID_COLLAPSE = 0.5
COLLAPSE_SNAPSHOTS = 2
SELLERS_DOMINATE = 0.25
SELLING_MIN = 2.0


@dataclass
class Position:
    plan: TradePlan
    opened: float
    entry_bid_depth: float
    style: Style = FAST
    quantity: float = 0.0
    stop: float = 0.0
    t1_done: bool = False
    cash: float = 0.0  # $ flow: negative after buying, back up as we sell
    exits: list[tuple[float, str, float, float]] = field(default_factory=list)

    @classmethod
    def open(cls, plan: TradePlan, ts: float, aave: Tape, style: Style = FAST) -> Position:
        before = [s.bid_depth for s in aave.snapshots if s.ts >= ts - 300.0]
        depth = statistics.median(before) if before else 0.0
        position = cls(plan, ts, depth, style, plan.quantity, plan.stop)
        position.cash = -plan.entry * plan.quantity * (1 + TAKER_FEE)
        return position

    @property
    def closed(self) -> bool:
        return self.quantity <= 1e-12

    @property
    def pnl(self) -> float:
        """Realized $ result, fees included (final once closed)."""
        return self.cash

    @property
    def r_multiple(self) -> float:
        return self.pnl / self.plan.planned_loss

    def _sell(self, ts: float, reason: str, price: float, quantity: float, fee: float) -> None:
        self.cash += price * quantity * (1 - fee)
        self.quantity -= quantity
        self.exits.append((ts, reason, price, quantity))

    def close(self, ts: float, reason: str, aave: Tape) -> None:
        """Sell everything left with a market order walking the bids (taker fee)."""
        self._sell(ts, reason, fill_price(aave.bids, self.quantity), self.quantity, TAKER_FEE)

    def check(
        self, aave: Tape, btc: Tape, now: float, thesis_failed: float | None = None
    ) -> str | None:
        """Apply the monitor rules once. Returns what happened, if anything."""
        bid, plan = aave.bid, self.plan
        exit_all = None
        if bid <= self.stop:
            exit_all = "stop" if not self.t1_done else "stop at entry after T1"
        elif risk := btc_risk_off(btc, now):
            exit_all = f"BTC risk-off: {'; '.join(risk)}"
        elif thesis_failed is not None and thesis_failed >= THESIS_FAILED_MIN:
            exit_all = f"Jev: entry thesis failed p={thesis_failed:.2f}"
        elif self.style.collapse_exit and self._liquidity_collapsed(aave):
            exit_all = "AAVE bids collapsed and sellers dominate"
        elif not self.t1_done and now - self.opened >= self.style.time_stop_s:
            exit_all = f"time stop: no T1 within {self.style.time_stop_s / 60:.0f} min"
        if exit_all:
            self.close(now, exit_all, aave)
            return exit_all
        if not self.t1_done and bid >= plan.target1:
            self._sell(now, "T1: sold 50%", plan.target1, self.quantity / 2, MAKER_FEE)
            self.t1_done = True
            self.stop = max(self.stop, plan.entry)  # never below entry, never widened
            return "T1"
        if self.t1_done and bid >= plan.target2:
            self._sell(now, "T2", plan.target2, self.quantity, MAKER_FEE)
            return "T2"
        return None

    def _liquidity_collapsed(self, aave: Tape) -> bool:
        recent = [s for s in aave.snapshots if s.ts > self.opened][-COLLAPSE_SNAPSHOTS:]
        if len(recent) < COLLAPSE_SNAPSHOTS or self.entry_bid_depth <= 0:
            return False
        thin = all(s.bid_depth < BID_COLLAPSE * self.entry_bid_depth for s in recent)
        share = aave.buy_share(SELLING_MIN)
        return thin and share is not None and share < SELLERS_DOMINATE
