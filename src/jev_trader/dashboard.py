"""Static HTML dashboard from a calibration log (the JSONL the loop writes).

    jev-trader dashboard --log data/live_calibration.jsonl            # once
    jev-trader dashboard --log data/live_calibration.jsonl --every 30 # keep refreshing

One self-contained file, no server, no dependencies: open it in a browser; it
reloads itself every 30 s. Charts: equity, inventory, decision latency, action
mix, and calibration drift (rolling Brier score of Jev's P(up)).
"""

from __future__ import annotations

import html
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

MAX_POINTS = 600
LATENCY_WINDOW = 100
BRIER_WINDOW = 500


def load(log: Path) -> tuple[list[dict], dict[str, str]]:
    decisions, outcomes = [], {}
    for line in log.read_text().splitlines():
        row = json.loads(line)
        if row["kind"] == "decision":
            decisions.append(row)
        else:
            outcomes[row["id"]] = row["outcome"]
    return decisions, outcomes


def _thin(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    step = max(1, len(points) // MAX_POINTS)
    return points[::step]


def _windows(values: list[float], size: int) -> list[list[float]]:
    return [values[i : i + size] for i in range(0, len(values) - size + 1, max(1, size // 4))]


def chart(title: str, series: dict[str, list[tuple[float, float]]], unit: str = "") -> str:
    """Inline SVG line chart; x is time, one polyline per series."""
    points = [p for s in series.values() for p in s]
    if len(points) < 2:
        return f"<section><h2>{title}</h2><p class=muted>not enough data yet</p></section>"
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    if y1 == y0:
        y0, y1 = y0 - 1, y1 + 1
    w, h = 600, 160

    def xy(p: tuple[float, float]) -> str:
        return f"{(p[0] - x0) / ((x1 - x0) or 1) * w:.1f},{h - (p[1] - y0) / (y1 - y0) * h:.1f}"

    lines = "".join(
        f'<polyline class="s{i}" points="{" ".join(xy(p) for p in _thin(s))}"/>'
        for i, s in enumerate(series.values())
    )
    legend = " ".join(
        f'<span class="k s{i}">{html.escape(name)}</span>' for i, name in enumerate(series)
    )
    return (
        f"<section><h2>{title}</h2><div class=legend>{legend}"
        f"<span class=muted>{y0:,.4g}{unit} to {y1:,.4g}{unit}</span></div>"
        f'<svg viewBox="0 0 {w} {h}" preserveAspectRatio="none" role="img"'
        f' aria-label="{title}">{lines}</svg></section>'
    )


def render(log: Path) -> str:
    decisions, outcomes = load(log)
    if not decisions:
        return "<p>no decisions logged yet</p>"
    ts = [d["ts"] for d in decisions]
    equity = [(d["ts"], d["equity"]) for d in decisions if d.get("equity") is not None]
    inventory = [(d["ts"], d["state"]["inventory"]) for d in decisions]
    latency = [d["latency_ms"] for d in decisions]
    lat_windows = _windows(latency, LATENCY_WINDOW)
    lat_ts = _windows(ts, LATENCY_WINDOW)
    p50 = [(t[-1], sorted(v)[len(v) // 2]) for t, v in zip(lat_ts, lat_windows, strict=True)]
    p99 = [(t[-1], sorted(v)[int(0.99 * len(v))]) for t, v in zip(lat_ts, lat_windows, strict=True)]

    labeled = [
        (d["ts"], d["judgments"]["direction_probabilities"].get("up", 0.5), outcomes[d["id"]])
        for d in decisions
        if d["id"] in outcomes and d["judgments"]
    ]
    brier = [
        (w[-1][0], sum((p - (o == "up")) ** 2 for _, p, o in w) / len(w))
        for w in _windows(labeled, BRIER_WINDOW)  # type: ignore[arg-type]
    ]

    actions = Counter(d["action"]["kind"] for d in decisions)
    sources = Counter(d["source"] for d in decisions)
    first_equity = equity[0][1] if equity else 0.0
    last_equity = equity[-1][1] if equity else 0.0
    peak, max_dd = first_equity, 0.0
    for _, e in equity:
        peak = max(peak, e)
        max_dd = max(max_dd, (peak - e) / peak if peak else 0.0)
    sorted_latency = sorted(latency)
    tiles = {
        "blocks": f"{len(decisions):,}",
        "equity": f"{last_equity:,.2f}" if equity else "n/a",
        "net": f"{last_equity - first_equity:+,.2f}" if equity else "n/a",
        "max drawdown": f"{max_dd:.2%}" if equity else "n/a",
        "latency p50 / p99": f"{sorted_latency[len(latency) // 2]:.0f} / "
        f"{sorted_latency[int(0.99 * len(latency))]:.0f} ms",
        "Jev share": f"{sources.get('jev', 0) / len(decisions):.1%}",
        "last block": datetime.fromtimestamp(ts[-1]).strftime("%Y-%m-%d %H:%M:%S"),
    }
    tile_html = "".join(
        f"<div class=tile><div class=muted>{k}</div><div class=v>{v}</div></div>"
        for k, v in tiles.items()
    )

    def bars(title: str, counts: Counter[str]) -> str:
        total = sum(counts.values())
        rows = "".join(
            f"<div class=bar><span>{html.escape(k)}</span>"
            f'<span class=track><span class=fill style="width:{n / total:.1%}"></span></span>'
            f"<span class=muted>{n:,} ({n / total:.0%})</span></div>"
            for k, n in counts.most_common()
        )
        return f"<section><h2>{title}</h2>{rows}</section>"

    return (
        f"<div class=tiles>{tile_html}</div>"
        + chart("Equity", {"equity": equity}, " $")
        + chart("Inventory", {"position": inventory})
        + chart(
            f"Decision latency (rolling {LATENCY_WINDOW} blocks)", {"p50": p50, "p99": p99}, " ms"
        )
        + bars("Action mix", actions)
        + bars("Decision source", sources)
        + chart(
            f"Calibration drift: Brier of P(up), rolling {BRIER_WINDOW} (lower is better)",
            {"Brier": brier},
        )
    )


PAGE = """<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<meta http-equiv=refresh content=30><title>Jev Trader Dashboard</title><style>
:root{{--bg:#fafaf9;--fg:#1c1917;--muted:#78716c;--card:#fff;--line:#e7e5e4;
--s0:#2563eb;--s1:#dc2626}}
@media (prefers-color-scheme:dark){{:root{{--bg:#0c0a09;--fg:#e7e5e4;--muted:#a8a29e;
--card:#1c1917;--line:#292524;--s0:#60a5fa;--s1:#f87171}}}}
body{{background:var(--bg);color:var(--fg);font:14px/1.4 system-ui,sans-serif;
margin:0 auto;padding:16px;max-width:960px}}
h1{{font-size:20px;margin:0 0 4px}}h2{{font-size:14px;margin:0 0 8px}}
.muted{{color:var(--muted)}}
.tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:8px;
margin:16px 0}}
.tile,section{{background:var(--card);border:1px solid var(--line);border-radius:8px;
padding:10px}}
.v{{font-size:18px;font-variant-numeric:tabular-nums}}section{{margin-bottom:12px}}
svg{{width:100%;height:160px;display:block}}
polyline{{fill:none;stroke-width:1.5;vector-effect:non-scaling-stroke}}
polyline.s0{{stroke:var(--s0)}}polyline.s1{{stroke:var(--s1)}}
.legend{{display:flex;gap:12px;flex-wrap:wrap;font-size:12px;margin-bottom:4px}}
.k::before{{content:"";display:inline-block;width:10px;height:2px;margin-right:4px;
vertical-align:middle;background:currentColor}}.k.s0{{color:var(--s0)}}
.k.s1{{color:var(--s1)}}
.bar{{display:grid;grid-template-columns:110px 1fr 110px;gap:8px;align-items:center;
margin:3px 0}}.track{{background:var(--line);border-radius:3px;height:8px}}
.fill{{display:block;height:8px;border-radius:3px;background:var(--s0)}}
</style></head><body><h1>Jev Trader</h1><div class=muted>{source}, generated {now}</div>
{body}</body></html>"""


def write(log: Path, out: Path) -> None:
    page = PAGE.format(
        source=html.escape(str(log)),
        now=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        body=render(log),
    )
    tmp = out.with_suffix(".tmp")
    tmp.write_text(page)
    tmp.replace(out)  # the browser never reloads a half-written file
