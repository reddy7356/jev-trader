"""Jev's part of Phase 7: order-flow judgments the code rules can't make.

Asked only after every code rule has passed (entry) or every 30 s while in a trade
(monitor), so calls are rare and cheap. Answers are probabilities; the thresholds that
turn them into decisions live in gates.py and position.py.
"""

from __future__ import annotations

from typing import Any

from typesafe_sdk import Noul

from jev_trader.gatekeeper.gates import TradePlan
from jev_trader.gatekeeper.tape import Tape

BTC_SELL_PRESSURE = Noul(
    instructions="Does `btc` show persistent sell pressure: bids being pulled, offers"
    " replenishing, and aggressive sellers dominating?",
    criteria={
        "true": "Sellers are in control of BTC and bids keep stepping back",
        "false": "BTC selling is absent, balanced, or being absorbed by firm bids",
    },
)
ENTRY_QUESTIONS = {
    "demand_accepted": Noul(
        instructions="Is aggressive buying in `aave` being accepted by price: buyers lifting"
        " offers, price holding or rising, and bid depth supporting the move?",
        criteria={
            "true": "Buyers are lifting offers and price is accepting higher with bid support",
            "false": "Buying is not lifting price, or the rise lacks bid support",
        },
    ),
    "absorption": Noul(
        instructions="In `aave`, are bids pulling, offers repeatedly replenishing, or"
        " aggressive buying failing to move the price higher?",
        criteria={
            "true": "Offers absorb the buying or bids are stepping back",
            "false": "Offers give way to buyers and bids hold",
        },
    ),
    "btc_sell_pressure": BTC_SELL_PRESSURE,
}
MONITOR_QUESTIONS = {
    "thesis_failed": Noul(
        instructions="For the open long in `position`, has the entry thesis failed: AAVE"
        " buyers no longer accepted by price, bids no longer supporting, or sellers taking"
        " control?",
        criteria={
            "true": "The reasons for being long AAVE no longer hold",
            "false": "Buyers and bid support that justified the long are still present",
        },
    ),
    "btc_sell_pressure": BTC_SELL_PRESSURE,
}


def _pct(value: float | None) -> float | None:
    return None if value is None else round(value * 100, 3)


def tape_state(tape: Tape) -> dict[str, Any]:
    """Compact numbers for Jev: % changes, flow, and the last 10 depth snapshots ($k)."""
    return {
        "mid": round(tape.mid, 6),
        "change_pct": {f"{m}m": _pct(tape.change(m)) for m in (1, 5, 15, 60)},
        "spread_pct": round(tape.spread_pct, 4),
        "volume_vs_median_5m": None if (r := tape.volume_ratio()) is None else round(r, 2),
        "aggressive_buy_share": {
            f"{m}m": None if (s := tape.buy_share(m)) is None else round(s, 2) for m in (1, 5)
        },
        "depth_30s_snapshots_usd_k": [
            [round(s.bid_depth / 1000, 1), round(s.ask_depth / 1000, 1)]
            for s in list(tape.snapshots)[-10:]
        ],
    }


def entry_state(aave: Tape, btc: Tape) -> dict[str, Any]:
    return {"aave": tape_state(aave), "btc": tape_state(btc)}


def monitor_state(aave: Tape, btc: Tape, plan: TradePlan, opened: float) -> dict[str, Any]:
    return {
        **entry_state(aave, btc),
        "position": {
            "side": "long",
            "entry": plan.entry,
            "stop": plan.stop,
            "target1": plan.target1,
            "minutes_open": round((aave.ts - opened) / 60, 1),
            "result_in_r": round((aave.bid - plan.entry) / (plan.entry - plan.stop), 2),
        },
    }


async def ask(client: Any, state: dict[str, Any], questions: dict[str, Noul]) -> dict[str, Any]:
    """One Jev call; returns {question: probability} plus input tokens and latency."""
    import time

    started = time.perf_counter()
    response = await client.system_one(state=state, questions=questions)
    answers: dict[str, Any] = {key: response.nouls[key].noul for key in questions}
    usage = getattr(response, "usage", None)
    answers["input_tokens"] = (usage.input_tokens or 0) if usage else 0
    answers["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
    return answers
