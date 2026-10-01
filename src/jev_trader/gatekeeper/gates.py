"""Phase 7 gates: the user's rules as code. Jev only adds judgment on top.

Flow every 30 s while flat: `entry_blockers` (code) -> if none, ask Jev -> `jev_blockers`
-> if none, LONG ELIGIBLE with the `TradePlan`. The default is NO TRADE: any missing
or stale input blocks. `btc_risk_off` is shared with the position monitor (forced exit).
"""

from __future__ import annotations

from dataclasses import dataclass

from jev_trader.gatekeeper.calendar import event_block
from jev_trader.gatekeeper.tape import DEPTH_LEVELS, Tape, fill_price

STALE_S = 10.0  # a tape not updated for this long is stale
# BTC (prompt 2)
BTC_DROP_15M = -0.01
BTC_DROP_60M = -0.025
BTC_STABLE_15M = -0.0025  # "stable or rising": not down more than 0.25% in 15 min
SELLING_SHARE = 0.45  # aggressive buy share below this, with price falling = accelerating selling
# AAVE (prompt 1)
MAX_SPREAD_PCT = 0.35
MIN_VOLUME_RATIO = 1.5
BIDS_STRONGER_S = 180.0
MIN_BUY_SHARE = 0.55
# risk and plan
RISK_PCT = 0.0025  # max loss per trade, share of equity
DAILY_LOSS_USD = 100.0
STOP_BUFFER = 0.0005  # stop 5 bps under the 15-min low
MIN_RISK_PCT, MAX_RISK_PCT = 0.0025, 0.02  # closer stops are noise; farther are too slow
TAKER_FEE, MAKER_FEE = 0.00045, 0.00015
MIN_REWARD_TO_RISK = 1.5
MAX_BOOK_SHARE = 0.5  # buy at most half of the offers visible in DEPTH_LEVELS levels
MIN_TRADE_USD = 100.0  # below this the visible book is too thin to bother
# Jev (probabilities; the user's 0.8 confidence rule on the deciding judgment)
DEMAND_ACCEPTED_MIN = 0.8
ABSORPTION_MAX = 0.2
BTC_SELL_PRESSURE_MAX = 0.3


@dataclass(frozen=True, slots=True)
class Account:
    equity: float
    realized_today: float  # negative = losses


@dataclass(frozen=True, slots=True)
class TradePlan:
    entry: float
    stop: float
    quantity: float
    target1: float
    target2: float

    @property
    def planned_loss(self) -> float:
        """$ lost at the stop, fees included."""
        exit_value = self.stop * (1 - TAKER_FEE)
        return (self.entry * (1 + TAKER_FEE) - exit_value) * self.quantity


def stale(tape: Tape, now: float) -> bool:
    return not tape.ts or now - tape.ts > STALE_S


def btc_risk_off(btc: Tape, now: float) -> list[str]:
    """Market-based BTC conditions that block entries and force an open long out."""
    if stale(btc, now):
        return ["BTC data stale"]
    c5, c15, c60, share = btc.change(5), btc.change(15), btc.change(60), btc.buy_share(5)
    if None in (c5, c15, c60):
        return ["BTC history incomplete (needs 60 min)"]
    reasons = []
    selling = share is not None and share < SELLING_SHARE  # no trades = no selling pressure
    if c15 < BTC_DROP_15M and selling and c5 < 0:
        reasons.append(f"BTC {c15:+.2%} in 15 min with accelerating selling")
    if c60 < BTC_DROP_60M:
        reasons.append(f"BTC {c60:+.2%} in 60 min")
    return reasons


def plan_trade(aave: Tape, account: Account) -> tuple[TradePlan | None, list[str]]:
    """Market buy walking the offers, stop under the 15-min low, targets 1.5R / 3R net of fees.

    Size is capped at half the visible offers, and the entry price is the average the
    buy would really pay through those levels (slippage), not just the best ask.
    """
    low = aave.low(15)
    if low is None:
        return None, ["AAVE 15-min low unavailable"]
    stop = low * (1 - STOP_BUFFER)
    visible = sum(px * sz for px, sz in aave.asks[:DEPTH_LEVELS])
    first_risk = aave.ask - stop + aave.ask * 2 * TAKER_FEE
    cap = min(MAX_BOOK_SHARE * visible, account.equity) / aave.ask  # no leverage either
    size = min(account.equity * RISK_PCT / first_risk, cap) if first_risk > 0 else 0.0
    if size * aave.ask < MIN_TRADE_USD:
        return None, [f"AAVE book too thin (${visible:,.0f} of offers in {DEPTH_LEVELS} levels)"]
    entry = fill_price(aave.asks, size)
    risk = entry - stop
    if not MIN_RISK_PCT <= risk / entry <= MAX_RISK_PCT:
        allowed = f"{MIN_RISK_PCT:.2%}-{MAX_RISK_PCT:.0%}"
        return None, [f"stop {risk / entry:.2%} below entry (allowed {allowed})"]
    # net of fees: losing costs entry + stop fees, winning pays entry + target fees
    risk_net = risk + entry * 2 * TAKER_FEE
    target_cost = entry * (TAKER_FEE + MAKER_FEE)
    target1 = entry + MIN_REWARD_TO_RISK * risk_net + target_cost
    target2 = entry + 2 * MIN_REWARD_TO_RISK * risk_net + target_cost
    quantity = min(account.equity * RISK_PCT / risk_net, size)  # entry priced for `size`
    plan = TradePlan(entry, stop, quantity, target1, target2)
    reasons = []
    if account.realized_today - plan.planned_loss < -DAILY_LOSS_USD:
        reasons.append(
            f"daily loss would exceed ${DAILY_LOSS_USD:.0f}"
            f" (today {account.realized_today:+.2f}, trade risks {plan.planned_loss:.2f})"
        )
    return (None if reasons else plan), reasons


def entry_blockers(
    aave: Tape, btc: Tape, now: float, account: Account
) -> tuple[TradePlan | None, list[str]]:
    """All code rules for a new long. Returns (plan, []) only when every rule passes."""
    reasons = btc_risk_off(btc, now)
    if event := event_block(now):
        reasons.append(f"scheduled event: {event}")
    if not reasons and btc.change(15) < BTC_STABLE_15M:
        reasons.append(f"BTC not stable or rising ({btc.change(15):+.2%} in 15 min)")
    if stale(aave, now):
        return None, [*reasons, "AAVE data stale"]
    if aave.spread_pct > MAX_SPREAD_PCT:
        reasons.append(f"AAVE spread {aave.spread_pct:.3f}% > {MAX_SPREAD_PCT}%")
    ratio = aave.volume_ratio()
    if ratio is None or ratio < MIN_VOLUME_RATIO:
        reasons.append(f"AAVE 5-min volume {ratio or 0:.2f}x median, needs {MIN_VOLUME_RATIO}x")
    if not aave.bids_stronger_for(BIDS_STRONGER_S):
        reasons.append(f"AAVE bids not stronger than asks for {BIDS_STRONGER_S / 60:.0f} min")
    share, c5 = aave.buy_share(5), aave.change(5)
    if share is None or share < MIN_BUY_SHARE:
        reasons.append(f"AAVE aggressive buying {share or 0:.0%} < {MIN_BUY_SHARE:.0%}")
    if c5 is None or c5 <= 0:
        reasons.append("AAVE price not accepting higher (5-min change <= 0)")
    if reasons:
        return None, reasons
    return plan_trade(aave, account)


def jev_blockers(judgments: dict[str, float]) -> list[str]:
    """Jev's order-flow judgments, as probabilities, against code-owned thresholds."""
    reasons = []
    if judgments["demand_accepted"] < DEMAND_ACCEPTED_MIN:
        reasons.append(f"Jev: demand accepted by price p={judgments['demand_accepted']:.2f}")
    if judgments["absorption"] > ABSORPTION_MAX:
        reasons.append(f"Jev: bids pulling / offers replenishing p={judgments['absorption']:.2f}")
    if judgments["btc_sell_pressure"] > BTC_SELL_PRESSURE_MAX:
        reasons.append(f"Jev: BTC sell pressure p={judgments['btc_sell_pressure']:.2f}")
    return reasons
