"""Summarise an arch_experiment CSV and draw the slide charts from the data.

    python deploy/spark_cluster/analyze_arch_experiment.py --csv data/exports/bigdata/arch_experiment_sql.csv \
        --label "Spark SQL + Catalyst"

Per cluster shape, at the shape's OWN best partition count:

* wall clock -- median of the measured full-corpus runs (warm-up already dropped);
* overhead   -- median of the skeleton runs (same plan and task count, 12 documents);
* compute    -- wall clock minus overhead;
* thread-s   -- compute x SMT threads granted: hardware time spent on the work;
* efficiency -- best thread-s / this shape's thread-s.

Writes ``<csv stem>_summary.json`` next to the CSV and two English, white-ground
SVGs in ``docs/assets``: ``<csv stem>_efficiency.svg`` and ``<csv stem>_time.svg``.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SHAPES = {"A": "5+5", "B": "2+2+2+2+2", "C": "2+2+2+2", "D": "4+4+4"}

INK, INK2, INK3 = "#101114", "#5A5F66", "#8A9099"
RULE, RULE_STRONG = "#E4E7EB", "#C4C9D0"
BAR, OVERHEAD, COMPUTE = "#24282E", "#6E747C", "#16181D"
ACCENT = "#1F5FE0"
FONT = "Helvetica Neue, Helvetica, Arial, sans-serif"
MONO = "Consolas, Menlo, monospace"


def summarise(csv_path: Path) -> list:
    rows = []
    with open(csv_path, encoding="utf-8-sig") as handle:
        for r in csv.DictReader(handle):
            if r.get("seconds"):
                r["seconds"] = float(str(r["seconds"]).replace(",", "."))
                rows.append(r)
    runs = defaultdict(list)
    for r in rows:
        runs[(r["arch"], r["kind"], int(r["partitions"]))].append(r["seconds"])
    median = {k: statistics.median(v) for k, v in runs.items()}
    out = []
    for arch in sorted({r["arch"] for r in rows}):
        meta = next(r for r in rows if r["arch"] == arch)
        parts = sorted({p for (a, kind, p) in median if a == arch and kind == "full"})
        best = min(parts, key=lambda p: median[(arch, "full", p)])
        full, skeleton = median[(arch, "full", best)], median.get((arch, "skeleton", best))
        threads = int(meta["total_cores"])
        compute = full - skeleton if skeleton is not None else None
        out.append({
            "arch": arch, "shape": SHAPES.get(arch, arch), "executors": int(meta["workers"]),
            "cores_per_exec": int(meta["cores_per_exec"]), "threads": threads,
            "heap_mb_per_exec": int(meta["heap_mb_per_exec"]), "partitions": best,
            "wall_clock": round(full, 2), "overhead": round(skeleton, 2) if skeleton is not None else None,
            "compute": round(compute, 2) if compute is not None else None,
            "thread_seconds": round(compute * threads, 1) if compute is not None else None,
            "full_runs": runs[(arch, "full", best)], "skeleton_runs": runs.get((arch, "skeleton", best), []),
            "n_docs": sorted({r["n_docs"] for r in rows if r["arch"] == arch and r["kind"] == "full"}),
            "vocabulary": sorted({r["vocab"] for r in rows if r["arch"] == arch and r["kind"] == "full"}),
        })
    best_cost = min(o["thread_seconds"] for o in out if o["thread_seconds"])
    for o in out:
        o["efficiency"] = round(100.0 * best_cost / o["thread_seconds"], 1) if o["thread_seconds"] else None
    return sorted(out, key=lambda o: -(o["efficiency"] or 0))


def efficiency_svg(summary: list, label: str) -> str:
    X0, BARW, H, STEP, TOP = 215, 600, 36, 56, 92
    height = TOP + STEP * len(summary) + 40
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 960 {height}" width="960" height="{height}" '
        f'font-family="{FONT}">',
        f'  <rect x="0" y="0" width="960" height="{height}" fill="#FFFFFF"/>',
        f'  <text x="40" y="38" font-size="21" font-weight="700" fill="{INK}">Hardware efficiency by cluster shape'
        f' — {label}</text>',
        f'  <text x="40" y="60" font-size="13" fill="{INK2}">Thread-seconds spent on identical work — lower is '
        f'better; 100% = best shape</text>',
    ]
    axis_bottom = TOP + STEP * len(summary) - 20
    for pct in (25, 50, 75, 100):
        x = X0 + BARW * pct // 100
        svg.append(f'  <line x1="{x}" y1="72" x2="{x}" y2="{axis_bottom}" stroke="{RULE}" stroke-width="1"/>')
    svg.append(f'  <line x1="{X0}" y1="72" x2="{X0}" y2="{axis_bottom}" stroke="{RULE_STRONG}" stroke-width="1"/>')
    for i, o in enumerate(summary):
        y = TOP + i * STEP
        width = round(BARW * (o["efficiency"] or 0) / 100)
        svg.append(f'  <text x="178" y="{y+16}" font-size="15" font-weight="600" text-anchor="end" fill="{INK}" '
                   f'font-family="{MONO}">{o["shape"]}</text>')
        svg.append(f'  <text x="178" y="{y+32}" font-size="11" text-anchor="end" fill="{INK3}">'
                   f'{o["threads"]} threads / {o["executors"]} executors</text>')
        if i == 0:
            svg.append(f'  <circle cx="197" cy="{y+18}" r="4" fill="{ACCENT}"/>')
        svg.append(f'  <rect x="{X0}" y="{y}" width="{width}" height="{H}" rx="3" fill="{BAR}"/>')
        svg.append(f'  <text x="{X0+width+12}" y="{y+23}" font-size="14" font-weight="600" fill="{INK}" '
                   f'font-family="{MONO}">{o["efficiency"]:.1f}%</text>')
    for pct in (0, 25, 50, 75, 100):
        x = X0 + BARW * pct // 100
        svg.append(f'  <text x="{x}" y="{axis_bottom+20}" font-size="11" text-anchor="middle" fill="{INK3}">{pct}</text>')
    svg.append(f'  <text x="{X0+BARW+52}" y="{axis_bottom+20}" font-size="11" text-anchor="middle" fill="{INK3}">%</text>')
    svg.append("</svg>")
    return "\n".join(svg) + "\n"


def time_svg(summary: list, label: str) -> str:
    X0, BARW, H, STEP, TOP = 215, 600, 36, 56, 92
    rows = sorted(summary, key=lambda o: o["wall_clock"])
    vmax = max(o["wall_clock"] for o in rows)
    ceiling = next(c for c in (5, 10, 15, 20, 25, 30, 40, 50, 60, 80, 100, 150, 200) if c >= vmax * 1.05)
    scale = BARW / ceiling
    height = TOP + STEP * len(rows) + 40
    axis_bottom = TOP + STEP * len(rows) - 20
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 960 {height}" width="960" height="{height}" '
        f'font-family="{FONT}">',
        f'  <rect x="0" y="0" width="960" height="{height}" fill="#FFFFFF"/>',
        f'  <text x="40" y="38" font-size="21" font-weight="700" fill="{INK}">Where the time goes — {label}</text>',
        f'  <text x="40" y="60" font-size="13" fill="{INK2}">Wall clock split into fixed overhead (12-document run, '
        f'same plan) and compute</text>',
        f'  <rect x="700" y="30" width="11" height="11" rx="2" fill="{OVERHEAD}"/>',
        f'  <text x="718" y="40" font-size="12" fill="{INK2}">Overhead</text>',
        f'  <rect x="800" y="30" width="11" height="11" rx="2" fill="{COMPUTE}"/>',
        f'  <text x="818" y="40" font-size="12" fill="{INK2}">Compute</text>',
    ]
    ticks = [ceiling * i / 5 for i in range(6)]
    for t in ticks[1:]:
        x = round(X0 + t * scale)
        svg.append(f'  <line x1="{x}" y1="72" x2="{x}" y2="{axis_bottom}" stroke="{RULE}" stroke-width="1"/>')
    svg.append(f'  <line x1="{X0}" y1="72" x2="{X0}" y2="{axis_bottom}" stroke="{RULE_STRONG}" stroke-width="1"/>')
    fastest = rows[0]["arch"]
    for i, o in enumerate(rows):
        y = TOP + i * STEP
        wo = max(2, round((o["overhead"] or 0) * scale))
        wc = max(2, round((o["compute"] or 0) * scale) - 2)
        svg.append(f'  <text x="178" y="{y+16}" font-size="15" font-weight="600" text-anchor="end" fill="{INK}" '
                   f'font-family="{MONO}">{o["shape"]}</text>')
        svg.append(f'  <text x="178" y="{y+32}" font-size="11" text-anchor="end" fill="{INK3}">'
                   f'{o["threads"]} threads / {o["executors"]} executors</text>')
        if o["arch"] == fastest:
            svg.append(f'  <circle cx="197" cy="{y+18}" r="4" fill="{ACCENT}"/>')
        svg.append(f'  <rect x="{X0}" y="{y}" width="{wo}" height="{H}" rx="3" fill="{OVERHEAD}"/>')
        svg.append(f'  <rect x="{X0+wo+2}" y="{y}" width="{wc}" height="{H}" rx="3" fill="{COMPUTE}"/>')
        svg.append(f'  <text x="{X0+wo+wc+14}" y="{y+23}" font-size="14" font-weight="600" fill="{INK}" '
                   f'font-family="{MONO}">{o["wall_clock"]:.1f} s<tspan fill="{INK3}" font-weight="400">'
                   f'  ({o["overhead"]:.1f} + {o["compute"]:.1f})</tspan></text>')
    for t in ticks:
        x = round(X0 + t * scale)
        svg.append(f'  <text x="{x}" y="{axis_bottom+20}" font-size="11" text-anchor="middle" fill="{INK3}">{t:g}</text>')
    svg.append(f'  <text x="{X0+BARW+52}" y="{axis_bottom+20}" font-size="11" text-anchor="middle" fill="{INK3}">seconds</text>')
    svg.append("</svg>")
    return "\n".join(svg) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", type=Path, default=ROOT / "data" / "exports" / "bigdata" / "arch_experiment_sql.csv")
    parser.add_argument("--label", default="Spark SQL + Catalyst")
    args = parser.parse_args()
    summary = summarise(args.csv)
    stem = args.csv.stem
    args.csv.with_name(f"{stem}_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    assets = ROOT / "docs" / "assets"
    (assets / f"{stem}_efficiency.svg").write_text(efficiency_svg(summary, args.label), encoding="utf-8", newline="\n")
    (assets / f"{stem}_time.svg").write_text(time_svg(summary, args.label), encoding="utf-8", newline="\n")
    print(f"{'Config':<7}{'Shape':<12}{'Cores/exec':>11}{'SMT':>5}{'Heap/exec':>11}{'Parts':>6}{'Wall':>8}"
          f"{'Overhead':>10}{'Compute':>9}{'Thread-s':>10}{'Efficiency':>12}")
    for o in summary:
        print(f"{o['arch']:<7}{o['shape']:<12}{o['cores_per_exec']:>11}{o['threads']:>5}{o['heap_mb_per_exec']:>9} MB"
              f"{o['partitions']:>6}{o['wall_clock']:>7.1f}s{o['overhead']:>9.1f}s{o['compute']:>8.1f}s"
              f"{o['thread_seconds']:>10.0f}{o['efficiency']:>11.1f}%")
    print(f"\n-> {args.csv.with_name(stem + '_summary.json')}\n-> {assets / (stem + '_efficiency.svg')}"
          f"\n-> {assets / (stem + '_time.svg')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
