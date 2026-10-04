"""Phase 7 gates: the user's rules as code. Jev only adds judgment on top.

The same rules run on two clocks (`Style`): FAST, the original minutes-scale version,
and SLOW, an hours-scale version where fees and slippage are a small share of the move.

Flow every `decide_every_s` while flat: `entry_blockers` (code) -> if none, ask Jev
-> `jev_blockers` -> if none, LONG ELIGIBLE with the `TradePlan`. The default is
NO TRADE: any missing or stale input blocks. `btc_risk_off` is shared with the
position monitor (forced exit).
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
MIN_BUY_SHARE = 0.55
# risk and plan
RISK_PCT = 0.0025  # max loss per trade, share of equity
DAILY_LOSS_USD = 100.0
STOP_BUFFER = 0.0005  # stop 5 bps under the recent low
TAKER_FEE, MAKER_FEE = 0.00045, 0.00015
MIN_REWARD_TO_RISK = 1.5
MAX_BOOK_SHARE = 0.5  # buy at most half of the offers visible in DEPTH_LEVELS levels
MIN_TRADE_USD = 100.0  # below this the visible book is too thin to bother
# Jev (probabilities; the user's 0.8 confidence rule on the deciding judgment)
DEMAND_ACCEPTED_MIN = 0.8
ABSORPTION_MAX = 0.2
BTC_SELL_PRESSURE_MAX = 0.3


@dataclass(frozen=True, slots=True)
class Style:
    """The clock a strategy runs on: same rules, different time windows."""

    name: str
    decide_every_s: float  # entry checks while flat; Jev monitor questions while in a trade
    flow_min: float  # window for volume vs median, aggressive buying, price acceptance
    trend_min: float | None  # also require price above this many minutes ago (None = off)
    bids_stronger_s: float  # look-back for "bids stronger than asks" ...
    bids_stronger_share: float  # ... in at least this share of 30 s snapshots (1.0 = all)
    low_min: float  # stop goes under the low of this window
    min_risk_pct: float  # stops closer than this are noise
    max_risk_pct: float  # farther than this are too slow
    time_stop_s: float  # exit if T1 not reached in time
    collapse_exit: bool  # exit when bids collapse and sellers dominate
    timeline_step_s: float  # Jev's timeline: step length ...
    timeline_slices: int  # ... and number of steps


FAST = Style("fast", 30, 5, None, 180, 1.0, 15, 0.0025, 0.02, 3600, True, 30, 10)
SLOW = Style("slow", 300, 15, 60, 900, 0.7, 60, 0.005, 0.04, 4 * 3600, False, 300, 12)


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


def plan_trade(
    aave: Tape, account: Account, style: Style = FAST
) -> tuple[TradePlan | None, list[str]]:
    """Market buy walking the offers, stop under the recent low, targets 1.5R / 3R net of fees.

    Size is capped at half the visible offers, and the entry price is the average the
    buy would really pay through those levels (slippage), not just the best ask.
    """
    low = aave.low(style.low_min)
    if low is None:
        return None, [f"AAVE {style.low_min:.0f}-min low unavailable"]
    stop = low * (1 - STOP_BUFFER)
    visible = sum(px * sz for px, sz in aave.asks[:DEPTH_LEVELS])
    first_risk = aave.ask - stop + aave.ask * 2 * TAKER_FEE
    cap = min(MAX_BOOK_SHARE * visible, account.equity) / aave.ask  # no leverage either
    size = min(account.equity * RISK_PCT / first_risk, cap) if first_risk > 0 else 0.0
    if size * aave.ask < MIN_TRADE_USD:
        return None, [f"AAVE book too thin (${visible:,.0f} of offers in {DEPTH_LEVELS} levels)"]
    entry = fill_price(aave.asks, size)
    risk = entry - stop
    if not style.min_risk_pct <= risk / entry <= style.max_risk_pct:
        allowed = f"{style.min_risk_pct:.2%}-{style.max_risk_pct:.0%}"
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
    aave: Tape, btc: Tape, now: float, account: Account, style: Style = FAST
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
    flow = style.flow_min
    ratio = aave.volume_ratio(period_s=flow * 60)
    if ratio is None or ratio < MIN_VOLUME_RATIO:
        reasons.append(
            f"AAVE {flow:.0f}-min volume {ratio or 0:.2f}x median, needs {MIN_VOLUME_RATIO}x"
        )
    strength = aave.bids_stronger_share(style.bids_stronger_s)
    if strength is None or strength < style.bids_stronger_share:
        reasons.append(
            f"AAVE bids not stronger than asks for {style.bids_stronger_s / 60:.0f} min"
        )
    share, accepting = aave.buy_share(flow), aave.change(flow)
    if share is None or share < MIN_BUY_SHARE:
        reasons.append(f"AAVE aggressive buying {share or 0:.0%} < {MIN_BUY_SHARE:.0%}")
    if accepting is None or accepting <= 0:
        reasons.append(f"AAVE price not accepting higher ({flow:.0f}-min change <= 0)")
    if style.trend_min and ((trend := aave.change(style.trend_min)) is None or trend <= 0):
        reasons.append(f"AAVE not in an uptrend ({style.trend_min:.0f}-min change <= 0)")
    if reasons:
        return None, reasons
    return plan_trade(aave, account, style)


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
