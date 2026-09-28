from __future__ import annotations

import json

from jev_trader.dashboard import write


def test_dashboard_renders_every_panel(tmp_path):
    log = tmp_path / "cal.jsonl"
    rows = []
    for i in range(1000):
        rows.append(
            {
                "kind": "decision",
                "id": str(i),
                "ts": 1_790_000_000.0 + i * 0.3,
                "state": {"inventory": i % 7 - 3},
                "judgments": {"direction_probabilities": {"up": 0.4}},
                "action": {"kind": "quote_wide" if i % 3 else "hold"},
                "latency_ms": 150.0 + i % 50,
                "source": "jev",
                "equity": 10_000.0 - i * 0.01,
            }
        )
        rows.append({"kind": "outcome", "id": str(i), "outcome": "up" if i % 2 else "down"})
    log.write_text("".join(json.dumps(r) + "\n" for r in rows))
    out = tmp_path / "dash.html"

    write(log, out)

    page = out.read_text()
    for panel in ("Equity", "Inventory", "Decision latency", "Action mix", "Calibration drift"):
        assert panel in page
    assert "not enough data" not in page
    assert "-9.99" in page  # net equity change over the run
