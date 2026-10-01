"""Phase 7 replay: run the AAVE gatekeeper over recorded AAVE + BTC data.

    uv run python scripts/gatekeeper_replay.py data/rec_AAVE_3d.jsonl data/rec_BTC_3d.jsonl

Runs twice on the same data, code rules only and then code + Jev, and writes
docs/gatekeeper_replay.md. Jev answers are cached in data/cache_gate_jev.jsonl
(keyed by question set and time), so re-running costs nothing.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

from typesafe_sdk import AsyncTypeSafeClient

from jev_trader.config import Settings
from jev_trader.gatekeeper import jev
from jev_trader.gatekeeper.calendar import ET
from jev_trader.gatekeeper.gates import Account, entry_blockers, jev_blockers
from jev_trader.gatekeeper.position import Position
from jev_trader.gatekeeper.tape import SNAPSHOT_EVERY_S, Tape, merged_records

STARTING_EQUITY = 10_000.0
CACHE = Path("data/cache_gate_jev.jsonl")


class CachedJev:
    """Jev calls, remembered by (kind, ts) so a replay can be repeated for free."""

    def __init__(self, client: AsyncTypeSafeClient | None) -> None:
        self.client = client
        self.calls = self.tokens = 0
        self.cache: dict[str, dict] = {}
        if CACHE.exists():
            for line in CACHE.read_text().splitlines():
                row = json.loads(line)
                self.cache[row["key"]] = row["answers"]

    async def ask(self, kind: str, ts: float, state: dict, questions: dict) -> dict:
        key = f"{kind}:{ts:.3f}"
        if key not in self.cache:
            answers = await jev.ask(self.client, state, questions)
            self.calls += 1
            self.tokens += answers["input_tokens"]
            self.cache[key] = answers
            with CACHE.open("a") as f:
                f.write(json.dumps({"key": key, "answers": answers}) + "\n")
        return self.cache[key]


async def replay(paths: dict[str, Path], judge: CachedJev | None) -> dict:
    tapes = {coin: Tape(coin) for coin in paths}
    aave, btc = tapes["AAVE"], tapes["BTC"]
    realized: dict[str, float] = {}  # ET date -> $ realized
    blocked: Counter[str] = Counter()
    trades: list[dict] = []
    checks = jev_rejections = 0
    position: Position | None = None
    last_check = last_monitor = 0.0

    def day(ts: float) -> str:
        return datetime.fromtimestamp(ts, ET).date().isoformat()

    for coin, rec in merged_records(paths):
        tape = tapes[coin]
        if rec["kind"] == "trade":
            tape.on_trade(rec["ts"], rec["px"], rec["sz"], rec["side"])
            continue
        tape.on_book(rec["ts"], rec["bids"], rec["asks"])
        if coin != "AAVE":
            continue
        now = rec["ts"]
        if position is not None:
            thesis = None
            if judge and now - last_monitor >= SNAPSHOT_EVERY_S:
                last_monitor = now
                state = jev.monitor_state(aave, btc, position.plan, position.opened)
                thesis = (await judge.ask("monitor", now, state, jev.MONITOR_QUESTIONS))[
                    "thesis_failed"
                ]
            position.check(aave, btc, now, thesis)
            if position.closed:
                realized[day(now)] = realized.get(day(now), 0.0) + position.pnl
                trades.append(trade_row(position))
                position = None
            continue
        if now - last_check < SNAPSHOT_EVERY_S:
            continue
        last_check = now
        checks += 1
        equity = STARTING_EQUITY + sum(realized.values())
        plan, reasons = entry_blockers(
            aave, btc, now, Account(equity, realized.get(day(now), 0.0))
        )
        if judge and not reasons:
            answers = await judge.ask("entry", now, jev.entry_state(aave, btc), jev.ENTRY_QUESTIONS)
            if reasons := jev_blockers(answers):
                jev_rejections += 1
        for reason in set(reasons):  # count each rule once, ignoring its numbers
            blocked[re.sub(r"[-+]?\d[\d.,]*%?x?", "#", reason.split(" (")[0])] += 1
        if not reasons:
            position = Position.open(plan, now, aave)
            last_monitor = now
    if position is not None:  # end of data: close at the last bid, like a time stop
        position.close(aave.ts, "end of data", aave)
        realized[day(aave.ts)] = realized.get(day(aave.ts), 0.0) + position.pnl
        trades.append(trade_row(position))
    return {
        "trades": trades,
        "realized": realized,
        "blocked": blocked,
        "checks": checks,
        "jev_rejections": jev_rejections,
    }


def trade_row(p: Position) -> dict:
    return {
        "opened": datetime.fromtimestamp(p.opened, ET).strftime("%a %m-%d %H:%M"),
        "entry": p.plan.entry,
        "stop": p.plan.stop,
        "t1": p.plan.target1,
        "exits": "; ".join(reason for _, reason, _, _ in p.exits),
        "minutes": round((p.exits[-1][0] - p.opened) / 60, 1),
        "pnl": p.pnl,
        "r": p.r_multiple,
    }


def section(name: str, result: dict) -> list[str]:
    trades = result["trades"]
    wins = [t for t in trades if t["pnl"] > 0]
    total = sum(t["pnl"] for t in trades)
    lines = [
        f"## {name}",
        "",
        f"- Entry checks (every 30 s while flat): {result['checks']:,}",
        f"- Trades: {len(trades)} ({len(wins)} wins, {len(trades) - len(wins)} losses)",
        f"- Net result after fees: **{total:+.2f}** on ${STARTING_EQUITY:,.0f}",
    ]
    if trades:
        lines.append(
            f"- Average R: {sum(t['r'] for t in trades) / len(trades):+.2f};"
            f" worst trade {min(t['r'] for t in trades):+.2f}R"
        )
    if result["jev_rejections"]:
        lines.append(f"- Passed every code rule but Jev said no: {result['jev_rejections']}")
    lines += ["", "| day (ET) | result |", "|---|---|"]
    lines += [f"| {d} | {v:+.2f} |" for d, v in sorted(result["realized"].items())]
    if trades:
        lines += ["", "| opened (ET) | entry | stop | T1 | exit | minutes | $ | R |",
                  "|---|---|---|---|---|---|---|---|"]
        lines += [
            f"| {t['opened']} | {t['entry']:.2f} | {t['stop']:.2f} | {t['t1']:.2f} |"
            f" {t['exits']} | {t['minutes']} | {t['pnl']:+.2f} | {t['r']:+.2f} |"
            for t in trades
        ]
    lines += ["", "Why it said NO TRADE (share of entry checks, a check can have several):", ""]
    lines += [
        f"- {reason}: {count / result['checks']:.0%}"
        for reason, count in result["blocked"].most_common(12)
    ]
    return [*lines, ""]


async def main() -> None:
    paths = {"AAVE": Path(sys.argv[1]), "BTC": Path(sys.argv[2])}
    code_only = await replay(paths, None)
    settings = Settings()
    async with AsyncTypeSafeClient(
        api_key=settings.typesafe_api_key.get_secret_value(), model=settings.jev_model
    ) as client:
        judge = CachedJev(client)
        with_jev = await replay(paths, judge)
    cost = judge.tokens * settings.jev_usd_per_mtok / 1e6
    lines = [
        "# AAVE gatekeeper replay",
        "",
        f"Generated {datetime.now(ET):%Y-%m-%d %H:%M} ET by `scripts/gatekeeper_replay.py` from"
        f" `{paths['AAVE']}` + `{paths['BTC']}`. Paper only: long only, no leverage, risk"
        " 0.25% per trade, $100 daily loss limit, taker fees on entry and stops, maker on"
        " targets.",
        "",
        f"New Jev calls this run: {judge.calls} (${cost:.4f}); cached answers are reused.",
        "",
        *section("Code rules only", code_only),
        *section("Code rules + Jev", with_jev),
    ]
    out = Path("docs/gatekeeper_replay.md")
    out.write_text("\n".join(lines))
    print("\n".join(lines))


if __name__ == "__main__":
    asyncio.run(main())
