"""Phase 7 position monitor and long-only paper fills (the user's prompt 3).

Spot-like: long only, no leverage. Entry is a market buy priced through the offers
(see gates.plan_trade). Every AAVE book update runs `Position.check`; stops and forced
exits are market sells walking the bids (taker), targets sell at the target (maker).
The stop only ever moves up. Never averages down.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from jev_trader.gatekeeper.gates import MAKER_FEE, TAKER_FEE, TradePlan, btc_risk_off
from jev_trader.gatekeeper.tape import Tape, fill_price

TIME_STOP_S = 3600.0
THESIS_FAILED_MIN = 0.8  # Jev probability that the entry thesis failed
BID_COLLAPSE = 0.5  # bid depth below half of what it was at entry ...
SELLERS_DOMINATE = 0.25  # ... while aggressive buyers are under 25% of the last minute


@dataclass
class Position:
    plan: TradePlan
    opened: float
    entry_bid_depth: float
    quantity: float = 0.0
    stop: float = 0.0
    t1_done: bool = False
    cash: float = 0.0  # $ flow: negative after buying, back up as we sell
    exits: list[tuple[float, str, float, float]] = field(default_factory=list)

    @classmethod
    def open(cls, plan: TradePlan, ts: float, aave: Tape) -> Position:
        depth = aave.snapshots[-1].bid_depth if aave.snapshots else 0.0
        position = cls(plan, ts, depth, plan.quantity, plan.stop)
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
        elif self._liquidity_collapsed(aave):
            exit_all = "AAVE bids collapsed and sellers dominate"
        elif not self.t1_done and now - self.opened >= TIME_STOP_S:
            exit_all = "time stop: no T1 within 60 min"
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
        if not aave.snapshots or self.entry_bid_depth <= 0:
            return False
        share = aave.buy_share(1)
        thin = aave.snapshots[-1].bid_depth < BID_COLLAPSE * self.entry_bid_depth
        return thin and share is not None and share < SELLERS_DOMINATE
