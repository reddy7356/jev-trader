from __future__ import annotations

from dataclasses import asdict, dataclass

from jev_trader.state.events import BlockEvent
from jev_trader.state.features import MarketFeatures


@dataclass(frozen=True, slots=True)
class MarketState:
    ts: float
    mid: float
    microprice: float
    spread_bps: float
    ret_1m: float
    ret_5m: float
    ret_30m: float
    depth_bid_3: float
    depth_ask_3: float
    imbalance: float
    aggressive_buy_ratio: float
    trade_intensity: float
    cancel_intensity: float
    realized_vol_short: float
    realized_vol_medium: float
    vol_ratio: float
    inventory: float
    unrealized_pnl: float
    drawdown: float
    position_age_s: float
    fill_ratio: float
    reject_rate: float
    slippage_bps: float
    last_decision_latency_ms: float

    def to_state_dict(self) -> dict[str, float]:
        """Dense numeric snapshot sent to Jev. Keep it compact; input tokens cost money."""
        return {key: round(value, 6) for key, value in asdict(self).items()}


def build_market_state(
    event: BlockEvent,
    features: MarketFeatures,
    *,
    inventory: float,
    unrealized_pnl: float,
    drawdown: float,
    position_age_s: float,
    fill_ratio: float,
    reject_rate: float,
    slippage_bps: float,
    last_decision_latency_ms: float,
) -> MarketState:
    book = event.book
    depth_bid, depth_ask = book.depth(3)
    return MarketState(
        ts=event.ts,
        mid=book.mid,
        microprice=book.microprice,
        spread_bps=book.spread_bps,
        ret_1m=features.ret_1m,
        ret_5m=features.ret_5m,
        ret_30m=features.ret_30m,
        depth_bid_3=depth_bid,
        depth_ask_3=depth_ask,
        imbalance=book.imbalance(3),
        aggressive_buy_ratio=features.aggressive_buy_ratio,
        trade_intensity=features.trade_intensity,
        cancel_intensity=features.cancel_intensity,
        realized_vol_short=features.realized_vol_short,
        realized_vol_medium=features.realized_vol_medium,
        vol_ratio=features.vol_ratio,
        inventory=inventory,
        unrealized_pnl=unrealized_pnl,
        drawdown=drawdown,
        position_age_s=position_age_s,
        fill_ratio=fill_ratio,
        reject_rate=reject_rate,
        slippage_bps=slippage_bps,
        last_decision_latency_ms=last_decision_latency_ms,
    )
