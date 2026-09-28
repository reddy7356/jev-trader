from __future__ import annotations

from jev_trader.config import RiskConfig
from jev_trader.loop.alerts import COOLDOWN_S, WINDOW_BLOCKS, Alerts

RISK = RiskConfig(max_decision_latency_ms=250.0, max_drawdown=0.05)


def check(alerts: Alerts, now: float, **overrides: float) -> None:
    values = {"failed": False, "consecutive_errors": 0, "latency_ms": 100.0, "drawdown": 0.0}
    alerts.check(**{**values, **overrides}, now=now)


def kinds(alerts: Alerts) -> list[str]:
    return [text.split("[")[1].split("]")[0] for text in alerts.sent]


def test_quiet_when_healthy():
    alerts = Alerts(RISK)
    for block in range(WINDOW_BLOCKS * 2):
        check(alerts, now=block * 0.3)
    assert alerts.sent == []


def test_each_condition_fires_once_per_cooldown():
    alerts = Alerts(RISK)
    for _ in range(WINDOW_BLOCKS):
        check(alerts, now=0.0, latency_ms=300.0, failed=True, consecutive_errors=3, drawdown=0.03)
    assert sorted(kinds(alerts)) == ["drawdown", "error_rate", "jev_down", "latency"]

    check(alerts, now=COOLDOWN_S - 1, consecutive_errors=5)
    assert kinds(alerts).count("jev_down") == 1  # still cooling down
    check(alerts, now=COOLDOWN_S, consecutive_errors=5)
    assert kinds(alerts).count("jev_down") == 2


def test_webhook_failure_never_raises():
    alerts = Alerts(RISK, webhook_url="http://127.0.0.1:9/nowhere")
    alerts._post("test")  # connection refused is logged, not raised
