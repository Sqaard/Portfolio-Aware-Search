"""Slide chart for baseline_vs_spark.json: overhead + compute per architecture.

    python deploy/spark_cluster/plot_baseline_vs_spark.py

White ground, ITMO monochrome, English -> docs/assets/baseline_vs_spark.svg
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data" / "exports" / "bigdata" / "baseline_vs_spark.json"
SVG = ROOT / "docs" / "assets" / "baseline_vs_spark.svg"

INK, INK2, INK3 = "#101114", "#5A5F66", "#8A9099"
RULE, RULE_STRONG = "#E4E7EB", "#C4C9D0"
OVERHEAD, COMPUTE = "#6E747C", "#16181D"
ACCENT = "#1F5FE0"
FONT = "Helvetica Neue, Helvetica, Arial, sans-serif"
MONO = "Consolas, Menlo, monospace"


def main() -> int:
    data = json.loads(DATA.read_text(encoding="utf-8"))
    rows = data["rows"]
    n_docs, vocab, tokens = data["output"][0]
    X0, BARW, H, STEP, TOP = 290, 520, 44, 72, 92
    vmax = max(r["wall_clock"] for r in rows)
    ceiling = next(c for c in (10, 20, 25, 30, 40, 50, 60, 80, 100) if c >= vmax * 1.08)
    scale = BARW / ceiling
    height = TOP + STEP * len(rows) + 64
    axis_bottom = TOP + STEP * len(rows) - 16
    fastest = min(rows, key=lambda r: r["wall_clock"])["architecture"]
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 960 {height}" width="960" height="{height}" '
        f'font-family="{FONT}">',
        f'  <rect x="0" y="0" width="960" height="{height}" fill="#FFFFFF"/>',
        f'  <text x="40" y="38" font-size="21" font-weight="700" fill="{INK}">'
        f'Spark cluster vs. the pre-Big-Data implementation</text>',
        f'  <text x="40" y="60" font-size="13" fill="{INK2}">Identical corpus and identical output — '
        f'{n_docs:,} documents, {vocab:,} terms, {tokens:,} tokens</text>',
        f'  <rect x="700" y="30" width="11" height="11" rx="2" fill="{OVERHEAD}"/>',
        f'  <text x="718" y="40" font-size="12" fill="{INK2}">Overhead</text>',
        f'  <rect x="800" y="30" width="11" height="11" rx="2" fill="{COMPUTE}"/>',
        f'  <text x="818" y="40" font-size="12" fill="{INK2}">Compute</text>',
    ]
    ticks = [ceiling * i / 5 for i in range(6)]
    for t in ticks[1:]:
        x = round(X0 + t * scale)
        svg.append(f'  <line x1="{x}" y1="76" x2="{x}" y2="{axis_bottom}" stroke="{RULE}" stroke-width="1"/>')
    svg.append(f'  <line x1="{X0}" y1="76" x2="{X0}" y2="{axis_bottom}" stroke="{RULE_STRONG}" stroke-width="1"/>')
    for i, r in enumerate(rows):
        y = TOP + i * STEP
        name, _, shape = r["architecture"].partition(" — ")
        meta = r["parallelism"] + (f" · {shape}" if shape else " · single process")
        wo = max(2, round(r["overhead"] * scale))
        wc = max(2, round(r["compute"] * scale) - 2)
        svg.append(f'  <text x="{X0-37}" y="{y+20}" font-size="14" font-weight="600" text-anchor="end" '
                   f'fill="{INK}">{name}</text>')
        svg.append(f'  <text x="{X0-37}" y="{y+37}" font-size="11" text-anchor="end" fill="{INK3}">{meta}</text>')
        if r["architecture"] == fastest:
            svg.append(f'  <circle cx="{X0-18}" cy="{y+H//2}" r="4" fill="{ACCENT}"/>')
        svg.append(f'  <rect x="{X0}" y="{y}" width="{wo}" height="{H}" rx="3" fill="{OVERHEAD}"/>')
        svg.append(f'  <rect x="{X0+wo+2}" y="{y}" width="{wc}" height="{H}" rx="3" fill="{COMPUTE}"/>')
        svg.append(f'  <text x="{X0+wo+wc+14}" y="{y+22}" font-size="15" font-weight="700" fill="{INK}" '
                   f'font-family="{MONO}">{r["wall_clock"]:.1f} s</text>')
        svg.append(f'  <text x="{X0+wo+wc+14}" y="{y+39}" font-size="11" fill="{INK3}" font-family="{MONO}">'
                   f'{r["thread_seconds"]:.0f} thread-s · {r["efficiency"]:.0f}%</text>')
    for t in ticks:
        x = round(X0 + t * scale)
        svg.append(f'  <text x="{x}" y="{axis_bottom+18}" font-size="11" text-anchor="middle" fill="{INK3}">{t:g}</text>')
    svg.append(f'  <text x="{X0+BARW+52}" y="{axis_bottom+18}" font-size="11" text-anchor="middle" fill="{INK3}">seconds</text>')
    svg.append("</svg>")
    SVG.write_text("\n".join(svg) + "\n", encoding="utf-8", newline="\n")
    print(f"-> {SVG}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
