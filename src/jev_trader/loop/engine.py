from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jev_trader.calibration.log import CalibrationLogger
from jev_trader.config import Settings
from jev_trader.domain import Action, ActionKind, Fill, JudgmentSet, Order
from jev_trader.execution.base import OrderRejected
from jev_trader.feeds.base import Feed
from jev_trader.judgment.fallback import HeuristicJudge
from jev_trader.loop.alerts import Alerts
from jev_trader.loop.performance import performance
from jev_trader.policy.engine import compose_action
from jev_trader.pricing.avellaneda_stoikov import compute_quote
from jev_trader.risk.limits import RiskEngine, RiskState, Verdict
from jev_trader.state.features import FeatureEngine
from jev_trader.state.snapshot import MarketState, build_market_state

logger = logging.getLogger(__name__)

QUOTING_ACTIONS = (ActionKind.QUOTE_BOTH_SIDES, ActionKind.QUOTE_WIDE, ActionKind.WIDEN)
MARKOUT_HORIZONS = (0, 1, 5, 10, 30)


@dataclass
class MarkoutTracker:
    """Adverse-selection meter: mid move after each fill, from our side of the trade.

    Positive = the market moved our way after we filled; falling with horizon =
    we are being picked off by informed flow.
    """

    horizons: tuple[int, ...] = MARKOUT_HORIZONS
    pnl: dict[int, float] = field(default_factory=dict)
    notional: dict[int, float] = field(default_factory=dict)
    hits: dict[int, int] = field(default_factory=dict)
    counts: dict[int, int] = field(default_factory=dict)
    _open: deque[tuple[int, Fill]] = field(default_factory=deque)

    def on_block(self, block: int, mid: float, fills: tuple[Fill, ...] = ()) -> None:
        self._open.extend((block, fill) for fill in fills)
        for filled_at, fill in self._open:
            age = block - filled_at
            if age in self.horizons:
                sign = 1.0 if fill.side == "buy" else -1.0
                edge = sign * (mid - fill.price) * fill.size
                self.pnl[age] = self.pnl.get(age, 0.0) + edge
                self.hits[age] = self.hits.get(age, 0) + (edge > 0)
                self.counts[age] = self.counts.get(age, 0) + 1
                self.notional[age] = self.notional.get(age, 0.0) + fill.price * fill.size
        while self._open and block - self._open[0][0] >= max(self.horizons):
            self._open.popleft()

    def summary(self) -> dict[str, dict[str, float]]:
        return {
            "markout_usd": {f"{h}b": round(self.pnl.get(h, 0.0), 4) for h in self.horizons},
            "markout_bps": {
                f"{h}b": round(self.pnl[h] / self.notional[h] * 10_000.0, 3)
                if self.notional.get(h)
                else 0.0
                for h in self.horizons
            },
            "hit_rate": {
                f"{h}b": round(self.hits.get(h, 0) / n, 3) if (n := self.counts.get(h)) else 0.0
                for h in self.horizons
            },
        }


@dataclass
class RiskMemory:
    """Loss-limit baselines that must survive a restart.

    Without this, a crash-restart loop would reset the daily loss and drawdown
    limits every time. Saved each block to `path` (None = in memory, for paper).
    The daily baseline resets at midnight UTC; the equity peak never resets.
    """

    day: str
    day_start_equity: float
    peak_equity: float
    path: Path | None = None

    @classmethod
    def load(cls, path: Path | None, equity: float, now: float) -> RiskMemory:
        today = _utc_day(now)
        memory = cls(today, equity, equity, path)
        if path is not None and path.exists():
            saved = json.loads(path.read_text())
            memory.peak_equity = max(saved["peak_equity"], equity)
            if saved["day"] == today:
                memory.day_start_equity = saved["day_start_equity"]
            logger.info("risk memory restored from %s: %s", path, saved)
        return memory

    def update(self, equity: float, now: float) -> None:
        today = _utc_day(now)
        if today != self.day:
            self.day, self.day_start_equity = today, equity
        self.peak_equity = max(self.peak_equity, equity)
        if self.path is not None:
            tmp = self.path.with_suffix(".tmp")
            data = {k: v for k, v in asdict(self).items() if k != "path"}
            tmp.write_text(json.dumps(data))
            os.replace(tmp, self.path)  # atomic: a crash mid-write leaves the old file


def _utc_day(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).date().isoformat()


@dataclass
class LoopStats:
    blocks: int = 0
    decisions: int = 0
    jev_decisions: int = 0
    fallback_decisions: int = 0
    timeouts: int = 0
    jev_errors: int = 0
    holds: int = 0
    kills: int = 0
    rejects: int = 0
    fills: int = 0
    fees: float = 0.0
    gas: float = 0.0
    model_mismatches: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    realized: float = 0.0
    unrealized: float = 0.0
    equity: float = 0.0
    actions: dict[str, int] = field(default_factory=dict)
    latencies_ms: list[float] = field(default_factory=list)
    markouts: MarkoutTracker = field(default_factory=MarkoutTracker)
    starting_cash: float = 0.0
    usd_per_mtok: float = 0.0
    equity_curve: list[tuple[float, float]] = field(default_factory=list)
    alerts: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        latencies = sorted(self.latencies_ms)

        def pct(q: float) -> float:
            return latencies[min(len(latencies) - 1, int(q * len(latencies)))]

        if latencies:
            average = sum(latencies) / len(latencies)
            p50, p95, p99 = pct(0.50), pct(0.95), pct(0.99)
            maximum = latencies[-1]
        else:
            average = p50 = p95 = p99 = maximum = 0.0
        jev = self.jev_decisions
        quoting = sum(self.actions.get(kind.value, 0) for kind in QUOTING_ACTIONS)
        perf = performance(self.equity_curve, self.starting_cash)
        return {
            "blocks": self.blocks,
            "decisions": self.decisions,
            "jev_decisions": self.jev_decisions,
            "fallback_decisions": self.fallback_decisions,
            "timeouts": self.timeouts,
            "jev_errors": self.jev_errors,
            "model_mismatches": self.model_mismatches,
            "holds": self.holds,
            "kills": self.kills,
            "rejects": self.rejects,
            "fills": self.fills,
            "actions": dict(sorted(self.actions.items())),
            "avg_latency_ms": round(average, 3),
            "p50_latency_ms": round(p50, 3),
            "p95_latency_ms": round(p95, 3),
            "p99_latency_ms": round(p99, 3),
            "max_latency_ms": round(maximum, 3),
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "tokens_per_jev_decision": round((self.input_tokens + self.output_tokens) / jev, 1)
            if jev
            else 0.0,
            "jev_cost_usd": round(self.input_tokens * self.usd_per_mtok / 1e6, 6),
            "cost_per_1m_decisions_usd": round(
                self.input_tokens * self.usd_per_mtok / jev, 2
            )
            if jev
            else 0.0,
            "realized": round(self.realized, 4),
            "unrealized": round(self.unrealized, 4),
            "fees": round(self.fees, 4),
            "gas": round(self.gas, 4),
            "equity": round(self.equity, 4),
            "sharpe": round(perf["sharpe"], 3),
            "sortino": round(perf["sortino"], 3),
            "max_drawdown": round(perf["max_drawdown"], 6),
            "coverage": round(quoting / self.blocks, 4) if self.blocks else 0.0,
            "alerts": len(self.alerts),
            **self.markouts.summary(),
        }


class TradingLoop:
    """The nine-stage 24/7 loop with the block-deadline rule and fallback ladder."""

    def __init__(
        self,
        *,
        feed: Feed,
        venue: Any,
        judge: Any,
        fallback: HeuristicJudge,
        settings: Settings,
        calibration: CalibrationLogger | None = None,
        allow_fallback: bool = True,
        risk_memory_path: Path | None = None,
        alerts: Alerts | None = None,
    ) -> None:
        self._feed = feed
        self._venue = venue
        self._judge = judge
        self._fallback = fallback
        self._settings = settings
        self._calibration = calibration
        self._allow_fallback = allow_fallback
        self._risk = RiskEngine(settings.risk)
        self._features = FeatureEngine()
        self._stats = LoopStats(
            starting_cash=settings.starting_cash, usd_per_mtok=settings.jev_usd_per_mtok
        )
        self._alerts = alerts or Alerts(settings.risk, settings.alert_webhook_url)
        self._memory = RiskMemory.load(risk_memory_path, settings.starting_cash, time.time())
        self._api_errors = 0
        self._last_latency_ms = 0.0
        self._pending: deque[tuple[str, int, float]] = deque()
        self._killed = False

    @property
    def killed(self) -> bool:
        return self._killed

    async def run(self) -> LoopStats:
        try:
            async for event in self._feed.events():
                if self._killed:
                    break
                await self._tick(event)
        finally:
            await self._venue.cancel_all()
            pnl = self._venue.pnl()
            self._stats.realized = pnl.realized
            self._stats.unrealized = pnl.unrealized
            self._stats.fees = pnl.fees
            self._stats.gas = pnl.gas
            self._stats.equity = self._venue.equity()
            self._stats.alerts = self._alerts.sent
        return self._stats

    async def _tick(self, event: Any) -> None:
        stats = self._stats
        stats.blocks += 1

        self._venue.on_block(event)
        await self._venue.sync()
        fills = self._venue.drain_fills()
        stats.fills += len(fills)
        if event.book.two_sided:
            stats.markouts.on_block(event.block, event.book.mid, fills)

        now = time.time()
        data_age_s = max(0.0, now - event.ts)
        pnl = self._venue.pnl()
        equity = self._venue.equity()
        stats.equity_curve.append((event.ts, equity))
        memory = self._memory
        memory.update(equity, event.ts)
        peak = memory.peak_equity
        drawdown = (peak - equity) / peak if peak > 0 else 0.0

        features = self._features.update(event)
        health = self._venue.health()
        state = build_market_state(
            event,
            features,
            inventory=self._venue.inventory(),
            unrealized_pnl=pnl.unrealized,
            drawdown=drawdown,
            position_age_s=self._venue.position_age_s(event.ts),
            fill_ratio=health.fill_ratio,
            reject_rate=health.reject_rate,
            slippage_bps=health.slippage_bps,
            last_decision_latency_ms=self._last_latency_ms,
        )

        errors_before = self._api_errors
        action, judgments, source, latency_ms = await self._decide(state)
        self._alerts.check(
            failed=source == "timeout" or self._api_errors > errors_before,
            consecutive_errors=self._api_errors,
            latency_ms=latency_ms,
            drawdown=drawdown,
            now=event.ts,
        )
        self._last_latency_ms = latency_ms
        stats.decisions += 1
        stats.latencies_ms.append(latency_ms)
        if source == "jev":
            stats.jev_decisions += 1
            if judgments is not None:
                stats.input_tokens += judgments.input_tokens
                stats.output_tokens += judgments.output_tokens
                if judgments.model != self._settings.jev_model:
                    stats.model_mismatches += 1
        elif source == "fallback":
            stats.fallback_decisions += 1

        verdict = self._risk.check(
            RiskState(
                position=self._venue.inventory(),
                daily_pnl=equity - memory.day_start_equity,
                drawdown=drawdown,
                inventory_age_s=self._venue.position_age_s(event.ts),
                data_age_s=data_age_s,
                decision_latency_ms=latency_ms,
                api_errors=self._api_errors,
            )
        )

        if verdict.verdict is Verdict.KILL:
            stats.kills += 1
            self._killed = True
            logger.critical("kill switch: %s", "; ".join(verdict.breaches))
            self._alerts.fire_now("kill", "; ".join(verdict.breaches))
            await self._venue.flatten()
            return

        reduce_only = verdict.verdict is Verdict.REDUCE_ONLY
        if verdict.verdict is Verdict.HOLD:
            stats.holds += 1
            action = Action(ActionKind.HOLD, reason="; ".join(verdict.breaches))
        elif reduce_only:
            action = Action(
                action.kind,
                size_scale=action.size_scale * self._settings.policy.low_confidence_scale,
                skew=action.skew,
                reason=f"{action.reason} | reduce_only: {'; '.join(verdict.breaches)}",
            )

        await self._execute(action, state, reduce_only)

        stats.actions[action.kind.value] = stats.actions.get(action.kind.value, 0) + 1

        if self._calibration is not None:
            decision_id = self._calibration.record_decision(
                block=event.block,
                ts=event.ts,
                mid=state.mid,
                state=state.to_state_dict(),
                judgments=judgments,
                action=action,
                latency_ms=latency_ms,
                source=source,
            )
            if judgments is not None:
                self._pending.append((decision_id, event.block, state.mid))
            self._label_matured(event.block, state.mid)

    async def _decide(self, state: MarketState) -> tuple[Action, JudgmentSet | None, str, float]:
        started = time.perf_counter()
        try:
            judgments = await asyncio.wait_for(
                self._judge.judge(state),
                timeout=self._settings.decision_timeout_s,
            )
        except asyncio.TimeoutError:
            latency_ms = (time.perf_counter() - started) * 1000.0
            self._stats.timeouts += 1
            action = Action(ActionKind.HOLD, reason="decision past block deadline")
            return action, None, "timeout", latency_ms
        except Exception:
            latency_ms = (time.perf_counter() - started) * 1000.0
            self._api_errors += 1
            self._stats.jev_errors += 1
            if not self._allow_fallback:
                logger.exception("Jev unavailable and fallback disabled, holding")
                action = Action(ActionKind.HOLD, reason="jev error, fallback disabled")
                return action, None, "error", latency_ms
            logger.exception("Jev unavailable, falling back to deterministic judgments")
            judgments = await self._fallback.judge(state)
            action = compose_action(judgments, state, self._settings.policy, self._settings.risk)
            return action, judgments, "fallback", latency_ms

        latency_ms = (time.perf_counter() - started) * 1000.0
        self._api_errors = 0  # limit is on consecutive errors: one blip must not kill 24/7
        action = compose_action(judgments, state, self._settings.policy, self._settings.risk)
        return action, judgments, judgments.source, latency_ms

    async def _execute(self, action: Action, state: MarketState, reduce_only: bool) -> None:
        await self._reconcile(self._target_orders(action, state, reduce_only))

    async def _reconcile(self, targets: list[Order]) -> None:
        """Leave resting quotes that are still on target; cancel the rest, place what's missing.

        Keeping an unchanged quote preserves queue position and saves a cancel and a place.
        ponytail: size changes alone don't trigger a requote; the resting size stays.
        """
        pricing = self._settings.pricing
        tolerance = (pricing.requote_ticks + 0.5) * pricing.tick_size
        missing = list(targets)
        for oid, resting in self._venue.open_orders().items():
            match = next(
                (
                    t
                    for t in missing
                    if t.side == resting.side and abs(t.price - resting.price) < tolerance
                ),
                None,
            )
            if match is None:
                await self._venue.cancel(oid)
            else:
                missing.remove(match)
        for order in missing:
            try:
                await self._venue.place(order)
            except OrderRejected:
                self._stats.rejects += 1

    def _target_orders(self, action: Action, state: MarketState, reduce_only: bool) -> list[Order]:
        if action.kind not in QUOTING_ACTIONS:
            return []
        if action.kind in (ActionKind.QUOTE_WIDE, ActionKind.WIDEN):
            multiplier = self._settings.policy.wide_spread_multiplier
        else:
            multiplier = 1.0
        max_position = max(self._settings.risk.max_position, 1e-9)
        quote = compute_quote(
            mid=state.mid,
            inventory=state.inventory / max_position,
            sigma=max(state.realized_vol_short, 1e-9),
            tau=self._settings.pricing.horizon_s,
            gamma=self._settings.pricing.gamma,
            kappa=self._settings.pricing.kappa,
            min_half_spread=state.mid * self._settings.pricing.min_half_spread_bps / 10_000.0,
            skew=action.skew,
            half_spread_multiplier=multiplier,
            tick_size=self._settings.pricing.tick_size,
        )
        size = self._settings.risk.max_order_size * action.size_scale
        if size <= 0:
            return []
        orders: list[Order] = []
        if not reduce_only or state.inventory < 0:
            orders.append(Order(side="buy", price=quote.bid, size=size))
        if not reduce_only or state.inventory > 0:
            orders.append(Order(side="sell", price=quote.ask, size=size))
        return orders

    def _label_matured(self, block: int, mid: float) -> None:
        if self._calibration is None:
            return
        horizon = self._settings.calibration_horizon_blocks
        while self._pending and block - self._pending[0][1] >= horizon:
            decision_id, _, mid_then = self._pending.popleft()
            ret = mid / mid_then - 1.0 if mid_then > 0 else 0.0
            if ret > 1e-4:
                outcome = "up"
            elif ret < -1e-4:
                outcome = "down"
            else:
                outcome = "neutral"
            self._calibration.record_outcome(
                decision_id=decision_id,
                horizon_blocks=horizon,
                mid_then=mid_then,
                mid_now=mid,
                outcome=outcome,
            )
