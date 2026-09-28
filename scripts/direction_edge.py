"""Phase 6: does the battery's `direction` answer carry edge after costs?

Reads the P(up)/P(down) calibration logs written by scripts/backtest.py (test set)
and, per judge, pretends to trade the lean: long when P(up) - P(down) > t, short
when < -t, exit 10 blocks later. Reports the mean move captured in bps against
round-trip costs (Hyperliquid base tier: maker 1.5 bps, taker 4.5 bps per side).

    uv run python scripts/direction_edge.py
"""

from __future__ import annotations

import json
import math
from pathlib import Path

LOGS = {
    "hand rules": "data/backtest_calibration_rules.jsonl",
    "frontier LLM": "data/backtest_calibration_llm.jsonl",
    "Jev": "data/backtest_calibration_jev.jsonl",
}
THRESHOLDS = (0.0, 0.1, 0.2, 0.3)
MAKER_RT_BPS, TAKER_RT_BPS = 3.0, 9.0


def signals(path: Path) -> list[tuple[float, float]]:
    """(lean = P(up) - P(down), 10-block move in bps) per labeled decision."""
    leans, moves = {}, {}
    for row in map(json.loads, path.read_text().splitlines()):
        if row["kind"] == "decision" and row["judgments"]:
            p = row["judgments"]["direction_probabilities"]
            leans[row["id"]] = p.get("up", 0.0) - p.get("down", 0.0)
        elif row["kind"] == "outcome":
            moves[row["id"]] = (row["mid_now"] / row["mid_then"] - 1.0) * 10_000.0
    return [(leans[i], moves[i]) for i in leans if i in moves]


def edge(pairs: list[tuple[float, float]], t: float) -> tuple[int, float, float]:
    """(trades, mean captured bps, t-stat). Overlapping 10-block windows inflate t."""
    captured = [move if lean > 0 else -move for lean, move in pairs if abs(lean) > t]
    n = len(captured)
    if n < 2:
        return n, 0.0, 0.0
    mean = sum(captured) / n
    sd = math.sqrt(sum((c - mean) ** 2 for c in captured) / (n - 1))
    return n, mean, mean / (sd / math.sqrt(n)) if sd else 0.0


def main() -> None:
    print(f"| judge | lean > | trades | mean bps | t | net of maker {MAKER_RT_BPS:g}"
          f" | net of taker {TAKER_RT_BPS:g} |")
    print("|---|---|---|---|---|---|---|")
    for name, log in LOGS.items():
        pairs = signals(Path(log))
        for t in THRESHOLDS:
            n, mean, tstat = edge(pairs, t)
            print(
                f"| {name} | {t:g} | {n} | {mean:+.2f} | {tstat:+.1f}"
                f" | {mean - MAKER_RT_BPS:+.2f} | {mean - TAKER_RT_BPS:+.2f} |"
            )


if __name__ == "__main__":
    main()
