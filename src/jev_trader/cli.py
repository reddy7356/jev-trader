from __future__ import annotations

import argparse
import asyncio
import json
from contextlib import AsyncExitStack
from pathlib import Path

from jev_trader import __version__
from jev_trader.calibration.log import CalibrationLogger
from jev_trader.calibration.metrics import (
    apply_platt,
    brier_score,
    expected_calibration_error,
    fit_platt,
    load_pairs,
    log_loss,
    reliability_curve,
)
from jev_trader.config import Settings
from jev_trader.execution.paper import PaperVenue
from jev_trader.feeds.replay import ReplayFeed
from jev_trader.feeds.synthetic import SyntheticFeed
from jev_trader.judgment.client import JevClient
from jev_trader.judgment.fallback import HeuristicJudge
from jev_trader.loop.engine import TradingLoop


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jev-trader",
        description="24/7 market-making system around Jev System One decisions.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    paper = sub.add_parser("paper", help="run the paper-trading loop")
    paper.add_argument("--blocks", type=int, default=None, help="default 2000 (all, with --replay)")
    paper.add_argument("--replay", type=Path, default=None, help="recorded JSONL to replay")
    paper.add_argument("--seed", type=int, default=7)
    paper.add_argument("--block-ms", type=int, default=None)
    paper.add_argument("--realtime", action="store_true", help="pace blocks in real time")
    paper.add_argument("--log", type=Path, default=None)
    paper.add_argument("--no-log", action="store_true")
    paper.add_argument("--ack-ms", type=float, default=None, help="simulated order ack latency")
    paper.add_argument("--cancel-ms", type=float, default=None, help="simulated cancel latency")
    paper.add_argument("--gas-place", type=float, default=None, help="$ gas per order placed")
    paper.add_argument("--gas-cancel", type=float, default=None, help="$ gas per order cancelled")
    paper.add_argument(
        "--live-only",
        action="store_true",
        help="require a TypeSafe key; on Jev errors hold instead of using heuristics",
    )

    calibrate = sub.add_parser("calibrate", help="calibration report from logged triples")
    calibrate.add_argument("--log", type=Path, default=None)
    calibrate.add_argument(
        "--source",
        choices=["all", "jev", "fallback"],
        default="all",
        help="filter decisions by judgment source",
    )

    sub.add_parser("models", help="list available TypeSafe models")

    live = sub.add_parser("live", help="quote on Hyperliquid TESTNET (needs HL_* env vars)")
    live.add_argument("--coin", default="SOL")
    live.add_argument("--order-size", type=float, required=True, help="per-quote size in coin")
    live.add_argument("--max-position", type=float, default=None, help="default 4x order size")
    live.add_argument("--judge", choices=["heuristic", "always"], default="heuristic")
    live.add_argument("--blocks", type=int, default=None, help="stop after N blocks")
    live.add_argument("--log", type=Path, default=Path("data/live_calibration.jsonl"))
    live.add_argument(
        "--risk-memory",
        type=Path,
        default=Path("data/live_risk.json"),
        help="daily-loss and peak-equity baselines kept across restarts",
    )

    kill = sub.add_parser("kill", help="TESTNET kill switch: cancel all orders, flatten")
    kill.add_argument("--coin", default="SOL")

    rec = sub.add_parser("record", help="record Hyperliquid book + trades for replay")
    rec.add_argument("--coin", default="SOL")
    rec.add_argument("--minutes", type=float, default=60.0)
    rec.add_argument("--out", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "paper":
        return asyncio.run(_run_paper(args))
    if args.command == "calibrate":
        return _run_calibrate(args)
    if args.command == "models":
        return _run_models()
    if args.command == "record":
        return _run_record(args)
    if args.command == "live":
        return asyncio.run(_run_live(args))
    if args.command == "kill":
        return asyncio.run(_run_kill(args))
    return 1


async def _run_paper(args: argparse.Namespace) -> int:
    settings = Settings()
    block_ms = args.block_ms or settings.block_ms
    if args.replay is not None:
        feed = ReplayFeed(args.replay, max_blocks=args.blocks, realtime=args.realtime)
        source = f"replay {args.replay}"
    else:
        blocks = args.blocks if args.blocks is not None else 2000
        feed = SyntheticFeed(
            seed=args.seed, block_ms=block_ms, realtime=args.realtime, max_blocks=blocks
        )
        source = f"synthetic {blocks} blocks"
    venue = PaperVenue(
        starting_cash=settings.starting_cash,
        fee_bps=settings.fee_bps,
        tick_size=settings.pricing.tick_size,
        max_order_size=settings.risk.max_order_size,
        ack_ms=args.ack_ms if args.ack_ms is not None else settings.order_ack_ms,
        cancel_ms=args.cancel_ms if args.cancel_ms is not None else settings.cancel_ms,
        gas_per_place=args.gas_place if args.gas_place is not None else settings.gas_per_place,
        gas_per_cancel=(
            args.gas_cancel if args.gas_cancel is not None else settings.gas_per_cancel
        ),
    )
    log_path = args.log if args.log is not None else settings.log_path
    calibration = CalibrationLogger(log_path, enabled=not args.no_log)
    fallback = HeuristicJudge(max_position=settings.risk.max_position)

    if args.live_only and not settings.has_api_key:
        print("--live-only needs TYPESAFE_API_KEY")
        return 1

    async with AsyncExitStack() as stack:
        if settings.has_api_key:
            judge = await stack.enter_async_context(JevClient(settings))
            mode = f"jev ({settings.jev_model})"
        else:
            judge = fallback
            mode = "heuristic fallback (no TYPESAFE_API_KEY)"
        print(f"paper run: {source}, judgments={mode}")
        loop = TradingLoop(
            feed=feed,
            venue=venue,
            judge=judge,
            fallback=fallback,
            settings=settings,
            calibration=calibration,
            allow_fallback=not args.live_only,
        )
        stats = await loop.run()

    calibration.close()
    print(json.dumps(stats.summary(), indent=2))
    if not args.no_log:
        print(f"calibration log: {log_path}")
    return 0


def _run_calibrate(args: argparse.Namespace) -> int:
    settings = Settings()
    path = args.log if args.log is not None else settings.log_path
    if not path.exists():
        print(f"no calibration log at {path}; run `jev-trader paper` first")
        return 1
    source = None if args.source == "all" else args.source
    probabilities, outcomes = load_pairs(path, source=source)
    if len(probabilities) < 10:
        print(f"only {len(probabilities)} labeled pairs; need at least 10")
        return 1

    print(f"pairs: {len(probabilities)}  (source: {args.source})")
    print(f"brier:    {brier_score(probabilities, outcomes):.4f}")
    print(f"log loss: {log_loss(probabilities, outcomes):.4f}")
    print(f"ece:      {expected_calibration_error(probabilities, outcomes):.4f}")
    print("reliability curve:")
    print("  bin              n     predicted  empirical")
    for entry in reliability_curve(probabilities, outcomes):
        if entry.count == 0:
            continue
        print(
            f"  {entry.lower:.1f}-{entry.upper:.1f}"
            f"  {entry.count:6d}     {entry.mean_predicted:.3f}      {entry.empirical:.3f}"
        )

    a, b = fit_platt(probabilities, outcomes)
    calibrated = [apply_platt(p, a, b) for p in probabilities]
    print(
        f"platt: a={a:.4f} b={b:.4f}  brier "
        f"{brier_score(probabilities, outcomes):.4f} -> "
        f"{brier_score(calibrated, outcomes):.4f}"
    )
    return 0


def _run_models() -> int:
    from typesafe_sdk import TypeSafeClient

    settings = Settings()
    if not settings.has_api_key:
        print("TYPESAFE_API_KEY is not set")
        return 1
    with TypeSafeClient(api_key=settings.typesafe_api_key.get_secret_value()) as client:
        models = client.models.list()
    for model in models.models:
        print(f"{model.name:24s} {model.release_date}  {model.description}")
    return 0




def _connect_testnet(settings: Settings, coin: str):  # -> HyperliquidVenue | None
    from jev_trader.execution.hyperliquid import HyperliquidVenue

    if settings.hl_private_key is None or not settings.hl_account_address:
        print("set HL_PRIVATE_KEY (API wallet key) and HL_ACCOUNT_ADDRESS (main account)")
        return None
    return HyperliquidVenue.connect(
        coin=coin,
        private_key=settings.hl_private_key.get_secret_value(),
        account_address=settings.hl_account_address,
    )


async def _run_live(args: argparse.Namespace) -> int:
    from jev_trader.backtest import JUDGES
    from jev_trader.feeds.hyperliquid import HyperliquidFeed

    settings = Settings()
    venue = _connect_testnet(settings, args.coin)
    if venue is None:
        return 1
    await venue.sync()
    max_position = args.max_position or 4 * args.order_size
    settings = settings.model_copy(
        update={
            "starting_cash": venue.equity(),
            "risk": settings.risk.model_copy(
                update={"max_order_size": args.order_size, "max_position": max_position}
            ),
        }
    )
    fallback = HeuristicJudge(max_position=max_position)
    calibration = CalibrationLogger(args.log)
    async with AsyncExitStack() as stack:
        if settings.has_api_key:
            judge = await stack.enter_async_context(JevClient(settings))
            mode = f"jev ({settings.jev_model})"
        else:
            judge = JUDGES[args.judge](max_position=max_position)
            mode = args.judge
        print(
            f"LIVE TESTNET {args.coin}: equity {venue.equity():.2f}, order {args.order_size},"
            f" max position {max_position}, judgments={mode}. Ctrl-C cancels all orders."
        )
        loop = TradingLoop(
            feed=HyperliquidFeed(args.coin, testnet=True, max_blocks=args.blocks),
            venue=venue,
            judge=judge,
            fallback=fallback,
            settings=settings,
            calibration=calibration,
            risk_memory_path=args.risk_memory,
        )
        try:
            stats = await loop.run()
        finally:
            calibration.close()
    print(json.dumps(stats.summary(), indent=2))
    return 0


async def _run_kill(args: argparse.Namespace) -> int:
    venue = _connect_testnet(Settings(), args.coin)
    if venue is None:
        return 1
    fill = await venue.flatten()
    print(f"all {args.coin} orders cancelled; position: {fill or 'already flat'}")
    return 0

def _run_record(args: argparse.Namespace) -> int:
    from datetime import datetime

    from jev_trader.feeds.hyperliquid import record

    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    out = args.out or Path(f"data/hl_{args.coin}_{stamp}.jsonl")
    print(f"recording {args.coin} for {args.minutes:g} min -> {out}")
    books = asyncio.run(record(args.coin, out, args.minutes * 60.0))
    print(f"done: {books} books")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())


if __name__ == "__main__":
    raise SystemExit(main())
