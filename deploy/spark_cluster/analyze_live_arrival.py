"""Summarise a live-arrival CSV and draw the slide chart straight from the data.

    python deploy/spark_cluster/analyze_live_arrival.py [--csv data/exports/bigdata/live_arrival.csv]

Busy time is taken on the same footing for every consumer:

* Structured Streaming (every ``ss*`` arm) -- Spark's ``triggerExecution`` for the
  batch (listing the inbox, planning, the sink, the offset and commit logs),
  joined from ``<csv stem>_<arm>_progress.jsonl`` on the batch id. The sink-only
  figure is kept alongside as ``sink_median`` to show how much is Spark machinery.
* incremental_update -- the tick's own ``seconds`` (load state, list, read, build
  the engine, compute, stop it, merge).

Writes ``<csv stem>_summary.json`` next to the CSV and ``docs/assets/<csv stem>.svg``
(white ground, ITMO monochrome, English).
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CSV = ROOT / "data" / "exports" / "bigdata" / "live_arrival.csv"

ORDER = [
    ("ss",                  "Structured Streaming", "Docker · 3×4 · full analytics"),
    ("ss-sql",              "Structured Streaming", "Catalyst SQL · 4+4+4 cluster · bind mount"),
    ("ss-sql-local",        "Structured Streaming", "Catalyst SQL · local[4] · bind mount"),
    ("ss-sql-local-native", "Structured Streaming", "Catalyst SQL · local[4] · container disk"),
    ("inc-local",           "incremental_update",   "Windows · local engine"),
    ("inc-spark",           "incremental_update",   "Windows · Spark local[12]"),
]

INK, INK2, INK3 = "#101114", "#5A5F66", "#8A9099"
RULE, RULE_STRONG = "#E4E7EB", "#C4C9D0"
WAIT, BUSY = "#6E747C", "#16181D"
ACCENT = "#1F5FE0"
FONT = "Helvetica Neue, Helvetica, Arial, sans-serif"
MONO = "Consolas, Menlo, monospace"


def load_progress(csv_path: Path, tag: str) -> dict:
    path = csv_path.with_name(f"{csv_path.stem}_{tag}_progress.jsonl")
    trigger = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").split("\n"):
            if line.strip():
                row = json.loads(line)
                if row.get("trigger_ms") is not None and (row.get("input_rows") or 0) > 0:
                    trigger[int(row["batch_id"])] = row["trigger_ms"] / 1000.0
    return trigger


def summarise(csv_path: Path) -> dict:
    rows = list(csv.DictReader(open(csv_path, encoding="utf-8")))
    out: dict = {}
    for tag, _name, _meta in ORDER:
        trigger = load_progress(csv_path, tag) if tag.startswith("ss") else {}
        for size in sorted({int(r["size"]) for r in rows}):
            sel = [r for r in rows if r["consumer"] == tag and int(r["size"]) == size]
            if not sel:
                continue
            lat = [float(r["latency_s"]) for r in sel]
            sink = [float(r["processing_s"]) for r in sel if r["processing_s"]]
            if tag.startswith("ss"):
                busy = [trigger[int(r["batch_key"])] for r in sel
                        if r["batch_key"] not in ("", "None") and int(r["batch_key"]) in trigger]
                busy_source = "spark triggerExecution" if len(busy) == len(sel) else "sink (triggerExecution incomplete)"
                if len(busy) != len(sel):
                    busy = sink
            else:
                busy, busy_source = sink, "tick seconds"
            med_busy = statistics.median(busy) if busy else None
            out.setdefault(tag, {})[size] = {
                "n": len(sel),
                "latency_median": round(statistics.median(lat), 3),
                "latency_min": round(min(lat), 3),
                "latency_max": round(max(lat), 3),
                "busy_median": round(med_busy, 3) if med_busy is not None else None,
                "busy_min": round(min(busy), 3) if busy else None,
                "busy_max": round(max(busy), 3) if busy else None,
                "busy_source": busy_source,
                "sink_median": round(statistics.median(sink), 3) if (tag.startswith("ss") and sink) else None,
                "wait_median": round(statistics.median(lat) - med_busy, 3) if med_busy is not None else None,
                "docs_per_busy_second": round(size / med_busy, 1) if med_busy else None,
                "documents_verified": sorted({int(r["new_documents"]) for r in sel if r["new_documents"]}),
                "python": sorted({r.get("python", "") for r in sel}),
                "host": sorted({r.get("host", "") for r in sel}),
                "gap_s": sorted({r.get("gap_s", "") for r in sel}),
                "interval_s": sorted({r.get("interval_s", "") for r in sel}),
            }
    return out


def nice_ceiling(value: float) -> float:
    exp = 10 ** math.floor(math.log10(value))
    for step in (1, 2, 2.5, 5, 10):
        if value <= step * exp:
            return step * exp
    return 10 * exp


def draw(summary: dict) -> str:
    sizes = sorted({size for per in summary.values() for size in per})
    gaps = sorted({g for per in summary.values() for s in per.values() for g in s["gap_s"] if g})
    runs = min(s["n"] for per in summary.values() for s in per.values())
    gap_txt = f"{float(gaps[0]):g} s apart" if len(gaps) == 1 else "spaced arrivals"
    X0, BARW, H, GAP = 290, 500, 28, 10
    LBL, DOT = 253, 272
    panels, y = [], 88
    for size in sizes:
        present = [(t, n, m) for t, n, m in ORDER if size in summary.get(t, {})]
        panels.append((size, present, y))
        y += 34 + len(present) * (H + GAP) + 46
    height = y + 4
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 960 {height}" '
        f'width="960" height="{height}" font-family="{FONT}">',
        f'  <rect x="0" y="0" width="960" height="{height}" fill="#FFFFFF"/>',
        f'  <text x="40" y="36" font-size="21" font-weight="700" fill="{INK}">'
        f'How long a live fetch keeps the site busy</text>',
        f'  <text x="40" y="58" font-size="13" fill="{INK2}">'
        f'One simulated live_incremental_fetch batch into a running, idle consumer — median of {runs} arrivals, '
        f'{gap_txt}</text>',
        f'  <rect x="640" y="26" width="11" height="11" rx="2" fill="{BUSY}"/>',
        f'  <text x="658" y="36" font-size="12" fill="{INK2}">Busy (processing)</text>',
        f'  <rect x="790" y="26" width="11" height="11" rx="2" fill="{WAIT}"/>',
        f'  <text x="808" y="36" font-size="12" fill="{INK2}">Waiting for trigger</text>',
    ]
    for size, present, top in panels:
        label = "light load" if size == min(sizes) else "heavy load"
        svg.append(f'  <text x="40" y="{top+14}" font-size="14" font-weight="700" fill="{INK}">{size} documents</text>')
        svg.append(f'  <text x="{40 + 12 + 9 * len(str(size)) + 88}" y="{top+14}" font-size="12" fill="{INK3}">{label}</text>')
        vmax = max(summary[t][size]["latency_median"] for t, _n, _m in present)
        ceil = nice_ceiling(vmax * 1.05)
        scale = BARW / ceil
        rows_top = top + 28
        axis_bot = rows_top + len(present) * (H + GAP) - GAP + 6
        ticks = [ceil * i / 4 for i in range(5)]
        for tv in ticks[1:]:
            x = round(X0 + tv * scale)
            svg.append(f'  <line x1="{x}" y1="{rows_top-6}" x2="{x}" y2="{axis_bot}" stroke="{RULE}" stroke-width="1"/>')
        svg.append(f'  <line x1="{X0}" y1="{rows_top-6}" x2="{X0}" y2="{axis_bot}" stroke="{RULE_STRONG}" stroke-width="1"/>')
        best = min(summary[t][size]["busy_median"] or math.inf for t, _n, _m in present)
        for i, (tag, name, meta) in enumerate(present):
            s = summary[tag][size]
            ry = rows_top + i * (H + GAP)
            busy = min(s["busy_median"] or 0.0, s["latency_median"])
            wait = max(0.0, s["latency_median"] - busy)
            wb = max(2, round(busy * scale))
            ww = max(0, round(wait * scale) - 2)
            svg.append(f'  <text x="{LBL}" y="{ry+12}" font-size="13" font-weight="600" text-anchor="end" fill="{INK}">{name}</text>')
            svg.append(f'  <text x="{LBL}" y="{ry+26}" font-size="10.5" text-anchor="end" fill="{INK3}">{meta}</text>')
            if s["busy_median"] == best:
                svg.append(f'  <circle cx="{DOT}" cy="{ry+H//2}" r="4" fill="{ACCENT}"/>')
            svg.append(f'  <rect x="{X0}" y="{ry}" width="{wb}" height="{H}" rx="3" fill="{BUSY}"/>')
            if ww > 0:
                svg.append(f'  <rect x="{X0+wb+2}" y="{ry}" width="{ww}" height="{H}" rx="3" fill="{WAIT}"/>')
            end = X0 + wb + (ww + 2 if ww > 0 else 0)
            svg.append(f'  <text x="{end+12}" y="{ry+19}" font-size="13" font-weight="600" fill="{INK}" '
                       f'font-family="{MONO}">{s["latency_median"]:.2f} s'
                       f'<tspan fill="{INK3}" font-weight="400"> · busy {s["busy_median"] or 0:.2f} s</tspan></text>')
        for tv in ticks:
            x = round(X0 + tv * scale)
            svg.append(f'  <text x="{x}" y="{axis_bot+16}" font-size="11" text-anchor="middle" fill="{INK3}">{tv:g}</text>')
        svg.append(f'  <text x="{X0+BARW+44}" y="{axis_bot+16}" font-size="11" text-anchor="middle" fill="{INK3}">seconds</text>')
    svg.append('</svg>')
    return "\n".join(svg) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    args = parser.parse_args()
    summary_path = args.csv.with_name(f"{args.csv.stem}_summary.json")
    svg_path = ROOT / "docs" / "assets" / f"{args.csv.stem}.svg"

    summary = summarise(args.csv)
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    svg_path.write_text(draw(summary), encoding="utf-8", newline="\n")
    print(f"{'consumer':<21}{'size':>5}{'n':>3}{'latency':>10}{'range':>16}{'busy':>8}{'busy range':>14}"
          f"{'sink':>7}{'wait':>7}{'docs/s':>8}  busy source / python / host")
    for tag, _n, _m in ORDER:
        for size, s in sorted(summary.get(tag, {}).items()):
            rng = f"{s['latency_min']:.2f}-{s['latency_max']:.2f}"
            brng = f"{s['busy_min']:.2f}-{s['busy_max']:.2f}" if s["busy_min"] is not None else "-"
            sink = f"{s['sink_median']:.2f}s" if s["sink_median"] is not None else "  -  "
            print(f"{tag:<21}{size:>5}{s['n']:>3}{s['latency_median']:>9.2f}s{rng:>16}"
                  f"{(s['busy_median'] or 0):>7.2f}s{brng:>14}{sink:>7}{(s['wait_median'] or 0):>6.2f}s"
                  f"{(s['docs_per_busy_second'] or 0):>8.1f}  {s['busy_source']} / {s['python']} / {s['host']}"
                  f"  docs={s['documents_verified']}")
    print(f"\n-> {summary_path}\n-> {svg_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
