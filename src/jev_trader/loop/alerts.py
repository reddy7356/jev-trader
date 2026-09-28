"""Operator alerts: latency, error rate, drawdown, Jev unavailability.

Checked once per block over a rolling window. Every alert is logged; with a
webhook URL it is also POSTed as {"text", "content"} (Slack and Discord both
accept that). Each kind fires at most once per cooldown so a bad hour is one
message, not ten thousand.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.request
from collections import deque

from jev_trader.config import RiskConfig

logger = logging.getLogger(__name__)

WINDOW_BLOCKS = 200  # ~1 minute at 300 ms blocks
ERROR_RATE = 0.10
JEV_DOWN_STREAK = 3
COOLDOWN_S = 600.0


class Alerts:
    def __init__(self, risk: RiskConfig, webhook_url: str | None = None) -> None:
        self._risk = risk
        self._webhook_url = webhook_url
        self._latencies: deque[float] = deque(maxlen=WINDOW_BLOCKS)
        self._failed: deque[bool] = deque(maxlen=WINDOW_BLOCKS)
        self._last_sent: dict[str, float] = {}
        self.sent: list[str] = []  # everything fired, for tests and the run summary

    def check(
        self,
        *,
        failed: bool,
        consecutive_errors: int,
        latency_ms: float,
        drawdown: float,
        now: float,
    ) -> None:
        """`failed`: this block's decision missed the deadline or the judge errored."""
        self._latencies.append(latency_ms)
        self._failed.append(failed)

        if len(self._latencies) == WINDOW_BLOCKS:
            p95 = sorted(self._latencies)[int(0.95 * WINDOW_BLOCKS)]
            if p95 > self._risk.max_decision_latency_ms:
                self._fire("latency", f"decision latency p95 {p95:.0f} ms", now)
            rate = sum(self._failed) / WINDOW_BLOCKS
            if rate > ERROR_RATE:
                self._fire("error_rate", f"{rate:.0%} of last {WINDOW_BLOCKS} blocks failed", now)
        if drawdown > self._risk.max_drawdown / 2:
            self._fire(
                "drawdown",
                f"drawdown {drawdown:.2%} (kill at {self._risk.max_drawdown:.2%})",
                now,
            )
        if consecutive_errors >= JEV_DOWN_STREAK:
            self._fire("jev_down", f"Jev failed {JEV_DOWN_STREAK} blocks in a row", now)

    def fire_now(self, kind: str, message: str) -> None:
        """For events that always deserve a message (the kill switch)."""
        self._fire(kind, message, time.time(), cooldown=False)

    def _fire(self, kind: str, message: str, now: float, cooldown: bool = True) -> None:
        if cooldown and now - self._last_sent.get(kind, -COOLDOWN_S) < COOLDOWN_S:
            return
        self._last_sent[kind] = now
        text = f"jev-trader ALERT [{kind}] {message}"
        self.sent.append(text)
        logger.warning(text)
        if self._webhook_url:
            asyncio.get_running_loop().run_in_executor(None, self._post, text)

    def _post(self, text: str) -> None:
        body = json.dumps({"text": text, "content": text}).encode()
        request = urllib.request.Request(
            self._webhook_url or "", data=body, headers={"Content-Type": "application/json"}
        )
        try:
            urllib.request.urlopen(request, timeout=5).close()
        except Exception:  # an alert must never take the trading loop down
            logger.exception("alert webhook failed")
