from __future__ import annotations

from collections.abc import Callable

import pytest

from jev_trader.domain import JudgmentSet
from jev_trader.state.book import Level, OrderBook
from jev_trader.state.events import BlockEvent, Trade
from jev_trader.state.snapshot import MarketState


@pytest.fixture
def make_book() -> Callable[..., OrderBook]:
    def _make(
        mid: float = 100.0,
        *,
        spread: float = 0.02,
        size: float = 10.0,
        levels: int = 3,
        tick: float = 0.01,
        ts: float = 0.0,
    ) -> OrderBook:
        bid = mid - spread / 2.0
        ask = mid + spread / 2.0
        bids = tuple(Level(bid - i * tick, size) for i in range(levels))
        asks = tuple(Level(ask + i * tick, size) for i in range(levels))
        return OrderBook(ts=ts, bids=bids, asks=asks)

    return _make


@pytest.fixture
def make_event(make_book: Callable[..., OrderBook]) -> Callable[..., BlockEvent]:
    def _make(
        block: int = 0,
        ts: float = 0.0,
        mid: float = 100.0,
        *,
        trades: tuple[Trade, ...] = (),
        book_updates: int = 1,
        **book_kwargs: float,
    ) -> BlockEvent:
        return BlockEvent(
            block=block,
            ts=ts,
            book=make_book(mid, ts=ts, **book_kwargs),
            trades=trades,
            book_updates=book_updates,
        )

    return _make


@pytest.fixture
def make_state() -> Callable[..., MarketState]:
    def _make(**overrides: float) -> MarketState:
        values: dict[str, float] = {
            "ts": 0.0,
            "mid": 100.0,
            "microprice": 100.0,
            "spread_bps": 2.0,
            "ret_1m": 0.0,
            "ret_5m": 0.0,
            "ret_30m": 0.0,
            "depth_bid_3": 30.0,
            "depth_ask_3": 30.0,
            "imbalance": 0.0,
            "aggressive_buy_ratio": 0.5,
            "trade_intensity": 0.0,
            "cancel_intensity": 0.0,
            "realized_vol_short": 0.001,
            "realized_vol_medium": 0.001,
            "vol_ratio": 1.0,
            "inventory": 0.0,
            "unrealized_pnl": 0.0,
            "drawdown": 0.0,
            "position_age_s": 0.0,
            "fill_ratio": 0.0,
            "reject_rate": 0.0,
            "slippage_bps": 0.0,
            "last_decision_latency_ms": 0.0,
        }
        values.update(overrides)
        return MarketState(**values)

    return _make


@pytest.fixture
def make_judgments() -> Callable[..., JudgmentSet]:
    def _make(**overrides: float) -> JudgmentSet:
        values: dict[str, float] = {
            "regime": "mean_reverting",
            "regime_confidence": 0.8,
            "direction": "neutral",
            "direction_confidence": 0.6,
            "direction_probabilities": {"up": 0.3, "down": 0.3, "neutral": 0.4},
            "toxic_flow": 0.1,
            "liquidity_stressed": 0.1,
            "quote_environment": 2.5,
            "quote_environment_confidence": 0.9,
            "inventory_pressure": 0.0,
            "inventory_pressure_confidence": 0.9,
            "model": "test",
            "latency_ms": 80.0,
            "request_id": "req_test",
            "source": "jev",
        }
        values.update(overrides)
        return JudgmentSet(**values)

    return _make
