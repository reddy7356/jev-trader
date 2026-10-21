"""SLOW-style gatekeeper replay over a folder of daily AAVE/BTC recordings.

    uv run python scripts/slow_replay.py code data/rec2w            # code rules only
    uv run python scripts/slow_replay.py jev  data/rec2w            # code rules + Jev
    uv run python scripts/slow_replay.py jev  data/rec2w 20261020   # last day to include

Joins the daily files (AAVE_YYYYMMDD.jsonl / BTC_YYYYMMDD.jsonl) in date order, runs only
the SLOW style, and writes docs/gatekeeper_replay_2w_slow_<mode>.md. Leaves
docs/gatekeeper_replay.md (the 3-day report) untouched. Jev answers are cached as usual.
"""

from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import gatekeeper_replay as g  # noqa: E402


def join_days(folder: Path, coin: str, last_day: str, out: Path) -> list[str]:
    days = sorted(p for p in folder.glob(f"{coin}_*.jsonl") if p.stem.split("_")[1] <= last_day)
    with out.open("wb") as dst:
        for p in days:
            with p.open("rb") as src:
                shutil.copyfileobj(src, dst)
    return [p.stem.split("_")[1] for p in days]


async def main() -> None:
    mode, folder = sys.argv[1], Path(sys.argv[2])
    last_day = sys.argv[3] if len(sys.argv) > 3 else "99999999"
    started = time.time()
    with tempfile.TemporaryDirectory() as tmp:
        paths = {c: Path(tmp) / f"{c}.jsonl" for c in ("AAVE", "BTC")}
        days = [join_days(folder, c, last_day, p) for c, p in paths.items()]
        assert days[0] == days[1], f"AAVE and BTC days differ: {days}"
        head = [f"Files: {days[0][0]} to {days[0][-1]} ({len(days[0])} per coin) from `{folder}`", ""]
        if mode == "code":
            result = await g.replay(paths, None, g.SLOW)
            title = "SLOW (every 300 s): code rules only"
        else:
            from typesafe_sdk import AsyncTypeSafeClient

            from jev_trader.config import Settings

            s = Settings()
            async with AsyncTypeSafeClient(
                api_key=s.typesafe_api_key.get_secret_value(), model=s.jev_model
            ) as client:
                judge = g.CachedJev(client)
                result = await g.replay(paths, judge, g.SLOW)
            title = "SLOW (every 300 s): code rules + Jev"
            cost = judge.tokens * s.jev_usd_per_mtok / 1e6
            head += [f"New Jev calls this run: {judge.calls} (${cost:.4f})", ""]
    text = "\n".join([*head, *g.section(title, result), f"Runtime {time.time() - started:.0f} s", ""])
    out = Path(f"docs/gatekeeper_replay_2w_slow_{mode}.md")
    out.write_text(text)
    print(text)
    print(f"wrote {out}")


if __name__ == "__main__":
    asyncio.run(main())
