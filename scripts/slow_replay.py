"""SLOW-style gatekeeper replay over a folder of daily AAVE/BTC recordings.

    uv run python scripts/slow_replay.py code data/rec2w            # code rules only
    uv run python scripts/slow_replay.py jev  data/rec2w            # code rules + Jev
    uv run python scripts/slow_replay.py jev  data/rec2w 20261020   # last day to include

Joins the daily files (AAVE_YYYYMMDD[-HHMM].jsonl / BTC_YYYYMMDD[-HHMM].jsonl, plain or
`.gz` as the recorder archives them) in date order, runs only the SLOW style, and writes
docs/gatekeeper_replay_2w_slow_<mode>.md. Leaves docs/gatekeeper_replay.md (the 3-day
report) untouched. Jev answers are cached as usual.
"""

from __future__ import annotations

import asyncio
import gzip
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import gatekeeper_replay as g


def day_of(path: Path) -> str:
    """`AAVE_20261020.jsonl.gz` or `AAVE_20261020-0412.jsonl` -> `20261020`."""
    return path.name.split("_", 1)[1].split(".")[0].split("-")[0]


def join_days(folder: Path, coin: str, last_day: str, out: Path) -> list[str]:
    """Concatenate the days in time order into one plain JSONL, gzipped ones included.

    Files are copied line by line and only complete lines (ending in a newline) are
    kept: a recording read while it is still being appended, or one whose recorder was
    killed mid-write, must not poison the joined file with half a JSON record.
    """
    days = sorted(
        (p for p in folder.glob(f"{coin}_*.jsonl*") if day_of(p) <= last_day),
        key=lambda p: (day_of(p), p.name),
    )
    with out.open("wb") as dst:
        for p in days:
            opener = gzip.open if p.suffix == ".gz" else open
            with opener(p, "rb") as src:
                for line in src:
                    if line.endswith(b"\n"):
                        dst.write(line)
    return [day_of(p) for p in days]


async def main() -> None:
    mode, folder = sys.argv[1], Path(sys.argv[2])
    last_day = sys.argv[3] if len(sys.argv) > 3 else "99999999"
    started = time.time()
    with tempfile.TemporaryDirectory() as tmp:
        paths = {c: Path(tmp) / f"{c}.jsonl" for c in ("AAVE", "BTC")}
        days = [join_days(folder, c, last_day, p) for c, p in paths.items()]
        assert days[0] == days[1], f"AAVE and BTC days differ: {days}"
        span = f"Files: {days[0][0]} to {days[0][-1]}"
        head = [f"{span} ({len(days[0])} per coin) from `{folder}`", ""]
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
    runtime = f"Runtime {time.time() - started:.0f} s"
    text = "\n".join([*head, *g.section(title, result), runtime, ""])
    out = Path(f"docs/gatekeeper_replay_2w_slow_{mode}.md")
    out.write_text(text)
    print(text)
    print(f"wrote {out}")


if __name__ == "__main__":
    asyncio.run(main())
