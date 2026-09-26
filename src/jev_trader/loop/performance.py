from __future__ import annotations

import math
from itertools import pairwise

SECONDS_PER_YEAR = 365.0 * 24 * 3600


def performance(curve: list[tuple[float, float]], starting_cash: float) -> dict[str, float]:
    """Sharpe, Sortino and max drawdown from a per-block (ts, equity) curve.

    Returns are per block, as a fraction of starting cash, annualized by the
    curve's own block rate (so replayed and synthetic runs compare fairly).
    """
    if len(curve) < 3 or starting_cash <= 0:
        return {"sharpe": 0.0, "sortino": 0.0, "max_drawdown": 0.0}
    returns = [(b - a) / starting_cash for (_, a), (_, b) in pairwise(curve)]
    elapsed = curve[-1][0] - curve[0][0]
    per_year = len(returns) / elapsed * SECONDS_PER_YEAR if elapsed > 0 else 0.0
    mean = sum(returns) / len(returns)
    std = math.sqrt(sum((r - mean) ** 2 for r in returns) / (len(returns) - 1))
    downside = math.sqrt(sum(min(r, 0.0) ** 2 for r in returns) / len(returns))
    scale = math.sqrt(per_year)

    peak = curve[0][1]
    max_drawdown = 0.0
    for _, equity in curve:
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, (peak - equity) / peak if peak > 0 else 0.0)
    return {
        "sharpe": mean / std * scale if std > 0 else 0.0,
        "sortino": mean / downside * scale if downside > 0 else 0.0,
        "max_drawdown": max_drawdown,
    }
