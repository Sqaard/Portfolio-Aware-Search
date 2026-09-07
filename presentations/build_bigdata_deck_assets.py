"""Render the diagram assets used by presentations/BigData_ITMO.pptx.

The deck itself is edited with python-pptx; this script only produces the PNGs
that sit on slides 5, 7, 8 and 12, so they can be regenerated when the numbers
change instead of being redrawn by hand.

    python presentations/build_bigdata_deck_assets.py

Every figure uses the deck's existing palette (sampled from the slides that were
already in it) so new diagrams sit next to the old ones without a style break.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "presentations" / "assets" / "bigdata_itmo"

# -- palette (sampled from the deck's existing figures) ---------------------
DEEP = "#4C1D95"      # deep purple: structure, headings
VIOLET = "#7C3AED"    # violet: active elements
MAGENTA = "#C026D3"   # magenta: results / emphasis
INK = "#1B1B1F"       # body text
GREY = "#5B6270"      # secondary text
FILL_A = "#EDE7F6"    # light lilac fill
FILL_B = "#F3E8FF"    # lighter lilac fill
WHITE = "#FFFFFF"

SANS = "DejaVu Sans"
MONO = "DejaVu Sans Mono"


def _box(ax, x, y, w, h, *, fill=WHITE, edge=DEEP, lw=2.0, r=0.9, z=2):
    ax.add_patch(
        FancyBboxPatch(
            (x, y), w, h,
            boxstyle=f"round,pad=0,rounding_size={r}",
            facecolor=fill, edgecolor=edge, linewidth=lw, zorder=z,
        )
    )


def _text(ax, x, y, s, *, size=9, color=INK, weight="normal", family=SANS,
          ha="center", va="center", z=4, style="normal", plate=False):
    bbox = None
    if plate:
        # Opaque plate: these labels sit on top of shuffle arrows and frame borders.
        bbox = dict(facecolor=WHITE, edgecolor="none", pad=2.0)
    ax.text(x, y, s, fontsize=size, color=color, fontweight=weight,
            fontfamily=family, ha=ha, va=va, zorder=z, style=style, bbox=bbox)


def _arrow(ax, p0, p1, *, color=DEEP, lw=2.0, style="-|>", ms=11, z=3,
           connection="arc3,rad=0"):
    ax.add_patch(
        FancyArrowPatch(
            p0, p1, arrowstyle=style, mutation_scale=ms, linewidth=lw,
            color=color, zorder=z, connectionstyle=connection,
            shrinkA=0, shrinkB=0,
        )
    )


def _canvas(w_in, h_in, dpi=200):
    fig = plt.figure(figsize=(w_in, h_in), dpi=dpi)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 100)
    ax.axis("off")
    return fig, ax


def _save(fig, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, transparent=True)
    plt.close(fig)
    print(f"  {path.name}: {path.stat().st_size:,} bytes")
    return path


# ---------------------------------------------------------------- slide 8 --
def build_pipeline(out_dir: Path) -> Path:
    """End-to-end chain: crawl -> JSON -> map -> shuffle -> reduce, on 2 workers."""

    fig, ax = _canvas(15.5, 7.6)

    def stage_label(y, text):
        """Rotated stage marker in the left margin, clear of the worker frames."""
        ax.text(3.2, y, text, fontsize=10.5, color=GREY, fontweight="bold",
                fontfamily=SANS, ha="center", va="center", rotation=90, zorder=4)

    # --- 1. ingest, before Spark -----------------------------------------
    stage_label(90.5, "1 · INGEST")
    ingest = [
        ("trusted URL registry", "ticker · canonical URL", False),
        ("fetch + cache", "index.html", False),
        ("regex extract", "title · body · dates", False),
        ("documents.jsonl", "352 MB · 26,368 docs", True),
    ]
    bw, bh, gap = 19.0, 8.0, 4.6
    x = 10.0
    centers = []
    for head, sub, last in ingest:
        _box(ax, x, 86.5, bw, bh, fill=FILL_B if last else WHITE,
             edge=MAGENTA if last else DEEP)
        _text(ax, x + bw / 2, 92.2, head, size=10.5,
              color=MAGENTA if last else DEEP, weight="bold")
        _text(ax, x + bw / 2, 88.8, sub, size=8.6, color=GREY, family=MONO)
        centers.append(x + bw / 2)
        x += bw + gap
    for i in range(3):
        _arrow(ax, (centers[i] + bw / 2 + 0.6, 90.5),
               (centers[i + 1] - bw / 2 - 0.6, 90.5), color=GREY, lw=1.8)

    # elbow: corpus -> centre -> split across the two workers
    ax.plot([centers[3], centers[3], 50.0], [86.1, 81.4, 81.4],
            color=DEEP, lw=2.0, solid_capstyle="round", zorder=3)
    _text(ax, 50, 84.4, "sc.textFile(...)  →  12 partitions", size=9.6,
          color=INK, family=MONO, plate=True)
    _arrow(ax, (50, 81.4), (30.5, 78.6), color=DEEP, lw=2.0,
           connection="arc3,rad=0.12")
    _arrow(ax, (50, 81.4), (73.5, 78.6), color=DEEP, lw=2.0,
           connection="arc3,rad=-0.12")

    # --- worker frames ----------------------------------------------------
    frames = ((9.0, 41.5, "WORKER 1 · 2 cores"), (52.5, 41.5, "WORKER 2 · 2 cores"))
    for fx, fw, label in frames:
        ax.add_patch(
            FancyBboxPatch(
                (fx, 10.0), fw, 67.0,
                boxstyle="round,pad=0,rounding_size=1.2",
                facecolor="none", edgecolor=VIOLET, linewidth=1.6,
                linestyle=(0, (6, 4)), zorder=1,
            )
        )
        _text(ax, fx + fw / 2, 74.4, label, size=10.0, color=VIOLET, weight="bold")

    # --- 2. map -----------------------------------------------------------
    stage_label(63.0, "2 · MAP")
    map_cells = [
        (11.0, 'doc_00841  "...crude oil price..."',
         "→ (oil, 1) (price, 1) (opec, 1)", "len = 612"),
        (54.5, 'doc_19277  "...oil, oil, price..."',
         "→ (oil, 2) (price, 1) (wti, 1)", "len = 448"),
    ]
    for x0, doc, pairs, ln in map_cells:
        _box(ax, x0, 57.0, 37.5, 13.5, fill=WHITE, edge=DEEP)
        _text(ax, x0 + 18.75, 67.6, doc, size=8.8, color=INK, family=MONO)
        _text(ax, x0 + 18.75, 63.6, pairs, size=9.6, color=VIOLET,
              family=MONO, weight="bold")
        _text(ax, x0 + 18.75, 59.5, ln, size=8.4, color=GREY, family=MONO)

    _text(ax, 50, 54.0,
          "map-side combine inside each partition:   (oil,1) + (oil,2)  →  (oil,3)",
          size=9.4, color=INK, family=MONO, plate=True)

    for x0, s_ in ((11.0, "(oil, 1,875)    (price, 3,402)"),
                   (54.5, "(oil, 2,062)    (price, 3,701)")):
        _box(ax, x0 + 2.0, 45.5, 33.5, 5.6, fill=FILL_A, edge=VIOLET, lw=1.6)
        _text(ax, x0 + 18.75, 48.3, s_, size=9.4, color=DEEP,
              family=MONO, weight="bold")

    # --- 3. shuffle -------------------------------------------------------
    stage_label(40.0, "3 · SHUFFLE")
    _text(ax, 50, 42.0, "partition = hash(term) % numPartitions", size=10.0,
          color=MAGENTA, family=MONO, weight="bold", plate=True, z=5)
    _text(ax, 50, 38.8, "same key lands on the same reducer, wherever it was produced",
          size=8.6, color=GREY, style="italic", plate=True, z=5)

    # oil -> partition 0 (worker 1); price -> partition 1 (worker 2).
    # The two diagonals crossing in the middle ARE the network shuffle.
    _arrow(ax, (17.0, 45.1), (17.0, 33.0), color=VIOLET, lw=2.0)
    _arrow(ax, (83.0, 45.1), (83.0, 33.0), color=MAGENTA, lw=2.0)
    _arrow(ax, (40.0, 45.1), (66.0, 33.0), color=MAGENTA, lw=2.0,
           connection="arc3,rad=-0.10")
    _arrow(ax, (60.0, 45.1), (34.0, 33.0), color=VIOLET, lw=2.0,
           connection="arc3,rad=-0.10")

    # --- 4. reduce --------------------------------------------------------
    stage_label(20.0, "4 · REDUCE")
    for x0, part, calc, total in (
        (11.0, "partition 0", "(oil, 1,875) + (oil, 2,062)", "(oil, 3,937)"),
        (54.5, "partition 1", "(price, 3,402) + (price, 3,701)", "(price, 7,103)"),
    ):
        _box(ax, x0, 16.5, 37.5, 13.5, fill=WHITE, edge=MAGENTA)
        _text(ax, x0 + 18.75, 27.0, part, size=9.0, color=GREY, weight="bold")
        _text(ax, x0 + 18.75, 23.2, calc, size=9.0, color=INK, family=MONO)
        _text(ax, x0 + 18.75, 19.2, total, size=12.5, color=MAGENTA,
              family=MONO, weight="bold")

    # --- output -----------------------------------------------------------
    _arrow(ax, (29.7, 16.1), (44.0, 8.6), color=DEEP, lw=2.0,
           connection="arc3,rad=-0.10")
    _arrow(ax, (73.3, 16.1), (59.0, 8.6), color=DEEP, lw=2.0,
           connection="arc3,rad=0.10")
    _box(ax, 27.0, 1.0, 46.0, 7.2, fill=FILL_B, edge=MAGENTA)
    _text(ax, 50, 5.6, "inverted index  +  BM25 statistics", size=11.5,
          color=DEEP, weight="bold")
    _text(ax, 50, 2.6, "document_frequencies · document_lengths · avgdl · n_docs",
          size=8.6, color=GREY, family=MONO)

    return _save(fig, out_dir / "slide08_pipeline.png")


# ---------------------------------------------------------------- slide 7 --
def build_architecture(out_dir: Path) -> Path:
    """One PySpark codebase, two deployments: Windows local[*] vs Docker cluster."""

    fig, ax = _canvas(13.6, 6.1)

    # --- shared code, the thing that does NOT change ----------------------
    _box(ax, 18.0, 84.0, 64.0, 13.0, fill=FILL_B, edge=DEEP)
    _text(ax, 50, 93.4, "one job, one codebase", size=12.5, color=DEEP, weight="bold")
    _text(ax, 50, 89.6, "bigdata/jobs/inverted_index.py   map / flatMap / reduceByKey",
          size=9.2, color=INK, family=MONO)
    _text(ax, 50, 86.3, "tokenizer reused verbatim from finportfolio_ir.text_utils",
          size=8.6, color=GREY, style="italic")

    _text(ax, 50, 79.6, "only  SparkConf.setMaster(...)  differs", size=9.6,
          color=MAGENTA, family=MONO, weight="bold")
    _arrow(ax, (50, 83.6), (26.0, 74.6), color=DEEP, lw=2.2, connection="arc3,rad=0.14")
    _arrow(ax, (50, 83.6), (74.0, 74.6), color=DEEP, lw=2.2, connection="arc3,rad=-0.14")

    # --- the two deployments ---------------------------------------------
    columns = [
        (3.0, VIOLET, "PySpark  ·  local[*]", "Windows laptop",
         [("master", "local[*]"),
          ("JVMs / cores", "1 JVM  ·  12 cores"),
          ("per-task worker", "new python.exe (no fork)"),
          ("shuffle", "in-process, no network")],
         "66.8 s", "macro corpus · 12 partitions"),
        (52.0, MAGENTA, "PySpark  ·  standalone", "Docker cluster",
         [("master", "spark://spark-master:7077"),
          ("JVMs / cores", "master + 2 workers · 4 cores"),
          ("per-task worker", "fork() (Linux)"),
          ("shuffle", "over the container network")],
         "4.0 s", "same code · same 12 partitions"),
    ]
    for x0, accent, head, sub, rows, big, cap in columns:
        _box(ax, x0, 17.0, 45.0, 57.0, fill=WHITE, edge=accent, lw=2.2)
        _text(ax, x0 + 22.5, 69.6, head, size=12.0, color=accent, weight="bold")
        _text(ax, x0 + 22.5, 65.8, sub, size=9.4, color=GREY)
        y = 58.0
        for key, val in rows:
            _text(ax, x0 + 2.5, y, key, size=8.4, color=GREY, ha="left")
            _text(ax, x0 + 42.5, y, val, size=8.6, color=INK, family=MONO, ha="right")
            if (key, val) != rows[-1]:
                ax.plot([x0 + 2.5, x0 + 42.5], [y - 2.8, y - 2.8],
                        color="#E3DCEF", lw=1.0, zorder=2)
            y -= 8.2
        _box(ax, x0 + 6.0, 19.5, 33.0, 11.5, fill=FILL_A, edge=accent, lw=1.6)
        _text(ax, x0 + 22.5, 26.2, big, size=21.0, color=accent,
              weight="bold", family=MONO)
        _text(ax, x0 + 22.5, 21.6, cap, size=8.2, color=GREY)

    # --- identical result -------------------------------------------------
    _arrow(ax, (25.5, 16.6), (42.0, 11.4), color=DEEP, lw=2.2, connection="arc3,rad=-0.10")
    _arrow(ax, (74.5, 16.6), (58.0, 11.4), color=DEEP, lw=2.2, connection="arc3,rad=0.10")
    _box(ax, 17.0, 0.8, 66.0, 10.2, fill=FILL_B, edge=MAGENTA)
    _text(ax, 50, 7.6, "byte-identical output", size=12.5, color=MAGENTA, weight="bold")
    _text(ax, 50, 3.6, "document_frequencies · document_lengths · avgdl · n_docs",
          size=9.0, color=INK, family=MONO)

    return _save(fig, out_dir / "slide07_architecture.png")


# --------------------------------------------------------------- slide 12 --
def _table(ax, x0, w, y_top, title, headers, rows, col_x, highlight=()):
    _text(ax, x0, y_top + 5.2, title, size=11.0, color=DEEP, weight="bold", ha="left")
    aligns = ("left", "right", "right")
    for cx, head, align in zip(col_x, headers, aligns):
        _text(ax, x0 + cx, y_top, head, size=8.4, color=GREY, weight="bold", ha=align)
    ax.plot([x0, x0 + w], [y_top - 2.6, y_top - 2.6], color=DEEP, lw=1.6, zorder=3)
    y = y_top - 6.8
    for i, row in enumerate(rows):
        strong = i in highlight
        if strong:
            ax.add_patch(
                FancyBboxPatch(
                    (x0 - 1.2, y - 2.4), w + 2.4, 5.4,
                    boxstyle="round,pad=0,rounding_size=0.6",
                    facecolor=FILL_A, edgecolor="none", zorder=1,
                )
            )
        for cx, cell, align in zip(col_x, row, aligns):
            _text(ax, x0 + cx, y, cell,
                  size=9.0 if strong else 8.8,
                  color=MAGENTA if (strong and align == "right") else INK,
                  family=SANS if align == "left" else MONO,
                  weight="bold" if strong else "normal", ha=align)
        y -= 6.2
    return y


def build_performance(out_dir: Path) -> Path:
    """The two measured comparison tables (clean run on an idle machine)."""

    fig, ax = _canvas(13.6, 6.1)

    _table(
        ax, 2.0, 44.0, 90.0,
        "1 · Same RDD code, only the platform changes",
        ("partitions", "Windows, 12 cores", "Cluster, 4 cores"),
        [("2", "11.4 s", "2.3 s"),
         ("4", "21.6 s", "2.2 s"),
         ("12", "66.8 s", "4.0 s   (16.7×)")],
        (0.0, 26.0, 44.0),
        highlight=(2,),
    )
    _text(ax, 2.0, 63.0, "Windows: time GROWS with parallelism (11.4 → 21.6 → 66.8)",
          size=9.0, color=INK, ha="left")
    _text(ax, 2.0, 58.6, "Cluster: falls, then flat (2.3 → 2.2 → 4.0) on 3× fewer cores",
          size=9.0, color=INK, ha="left")
    _box(ax, 2.0, 38.0, 44.0, 15.0, fill=FILL_B, edge=MAGENTA, lw=1.6)
    _text(ax, 24.0, 48.6, "every partition = one task", size=10.0,
          color=MAGENTA, weight="bold")
    _text(ax, 24.0, 44.4, "Windows: a new python.exe per task", size=8.8,
          color=INK, family=MONO)
    _text(ax, 24.0, 40.6, "Linux: fork() costs almost nothing", size=8.8,
          color=INK, family=MONO)

    _table(
        ax, 52.0, 46.0, 90.0,
        "2 · Ways to speed PySpark up (baseline 66.8 s)",
        ("remedy", "time", "speed-up"),
        [("spark.python.worker.reuse=false", "64.1 s", "1.0×"),
         ("fewer partitions: 12 to 4", "21.6 s", "3.1×"),
         ("fewer partitions: 12 to 2", "11.4 s", "5.9×"),
         ("cluster, 12 partitions", "4.0 s", "16.7×"),
         ("cluster, 4 partitions", "2.2 s", "30×"),
         ("DataFrame/SQL on Windows, 12", "1.3 s", "51×"),
         ("DataFrame/SQL on Windows, 4", "1.2 s", "56×"),
         ("DataFrame/SQL on cluster, 4", "1.9 s", "35×")],
        (0.0, 35.0, 46.0),
        highlight=(0, 6),
    )
    _text(ax, 52.0, 31.0, "The advice from the internet does nothing", size=9.8,
          color=MAGENTA, weight="bold", ha="left")
    _text(ax, 52.0, 26.6, "worker.reuse tunes the daemon/fork path, which Windows has not got.",
          size=8.6, color=INK, ha="left")
    _text(ax, 52.0, 20.0, "DataFrame/SQL is fastest ON WINDOWS", size=9.8,
          color=MAGENTA, weight="bold", ha="left")
    _text(ax, 52.0, 15.6, "1.2 s vs 1.9 s on the cluster. With no Python workers to start,",
          size=8.6, color=INK, ha="left")
    _text(ax, 52.0, 11.8, "the 12-core laptop beats the 4-core cluster: the bottleneck was",
          size=8.6, color=INK, ha="left")
    _text(ax, 52.0, 8.0, "never Windows, it was Python-worker spawn.",
          size=8.6, color=INK, ha="left")
    # The third takeaway goes in the left column, which has room below table 1.
    _box(ax, 2.0, 8.5, 44.0, 24.0, fill=WHITE, edge=VIOLET, lw=1.6)
    _text(ax, 24.0, 28.1, "Why the project still ships the RDD path", size=9.8,
          color=VIOLET, weight="bold")
    _text(ax, 24.0, 23.1, "The SQL variant builds a different vocabulary", size=8.6, color=INK)
    _text(ax, 24.0, 19.5, "(18,158 vs 5,520 terms): its SQL tokenizer is", size=8.6, color=INK)
    _text(ax, 24.0, 15.9, "not the project's. It demonstrates the mechanism,", size=8.6, color=INK)
    _text(ax, 24.0, 12.3, "not parity.", size=8.6, color=INK)
    _text(ax, 24.0, 4.0, "correctness first — speed from the cluster", size=9.0,
          color=MAGENTA, weight="bold")

    return _save(fig, out_dir / "slide12_performance.png")


# ---------------------------------------------------------------- slide 5 --
#: Measured over data/processed_documents/sec_macro_company_ir_ppo_2010_2023_documents.jsonl
#: (352,104,589 bytes = 335.8 MiB). Grouped by source_type; the company_* types
#: are collapsed into one row. Used when the corpus is not present locally.
CORPUS_SNAPSHOT = (
    ("SEC filing sections", "10-K \u00b7 10-Q \u00b7 8-K", 6356, 253.8),
    ("Official macro releases", "FRED \u00b7 BLS \u00b7 Treasury", 18240, 37.6),
    ("SEC exhibits", "earnings releases, agreements", 655, 26.5),
    ("Company IR documents", "newsrooms \u00b7 reports \u00b7 decks", 1117, 17.8),
)

#: One real row from that corpus. A SEC section: 55 fields in the file, 9 here.
#: Chosen over a macro release because SEC sections are 76% of the corpus and
#: they carry the provenance chain -- source URL, accession, parent filing, and
#: the exact character range the section occupies inside it.
SAMPLE_DOCUMENT = (
    ("doc_id", "sec_aapl_10k_000032019321000105__item_1a_risk_factors"),
    ("title", "Apple Inc. 10-K filed 2021-10-29 \u2014 Item 1A Risk Factors"),
    ("url", "https://www.sec.gov/Archives/edgar/data/320193/\u2026"),
    ("sec_accession_number", "0000320193-21-000105"),
    ("parent_doc_id", "sec_aapl_10k_000032019321000105"),
    ("sec_section_code", "1A   \u00b7   chars 24,654\u201391,275 of 223,208"),
    ("available_at", "2021-10-29T16:00:00Z"),
    ("matched_tickers", "AAPL"),
    ("split", "test"),
)

SAMPLE_BODY = (
    '"Item 1A. Risk Factors \u2014 The Company\u2019s business, reputation,',
    ' results of operations and financial condition\u2026"',
)


def _measure_corpus():
    """Recount the corpus so the slide cannot drift; None when it is absent."""

    import json
    from collections import defaultdict

    path = ROOT / "data" / "processed_documents" / "sec_macro_company_ir_ppo_2010_2023_documents.jsonl"
    if not path.is_file():
        return None
    groups = {
        "sec_filing_section": 0, "official_macro_release": 1, "sec_filing_exhibit": 2,
    }
    counts = defaultdict(int)
    sizes = defaultdict(float)
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            source_type = json.loads(line).get("source_type", "")
            slot = groups.get(source_type, 3)   # everything else is company IR
            counts[slot] += 1
            sizes[slot] += len(line.encode("utf-8"))
    return tuple(
        (label, sub, counts[i], round(sizes[i] / 1024 / 1024, 1))
        for i, (label, sub, _c, _m) in enumerate(CORPUS_SNAPSHOT)
    )


def _verify_sample_document() -> None:
    """Fail loudly if the hand-written example no longer matches the corpus.

    The example is retyped into this file so the slide can be rendered without
    the (gitignored) corpus present. That invites exactly one bug -- a mistyped
    identifier -- so when the corpus IS present, check it. Silently shipping a
    slide with a doc_id that does not exist is worse than not rendering.
    """

    import json

    path = ROOT / "data" / "processed_documents" / "sec_macro_company_ir_ppo_2010_2023_documents.jsonl"
    if not path.is_file():
        return
    fields = dict(SAMPLE_DOCUMENT)
    wanted = fields["doc_id"]
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if wanted not in line:
                continue
            record = json.loads(line)
            if record.get("doc_id") != wanted:
                continue
            for key in ("sec_accession_number", "parent_doc_id", "available_at", "split"):
                if str(record.get(key)) != fields[key]:
                    raise SystemExit(
                        f"slide 5 example is stale: {key} is {record.get(key)!r} "
                        f"in the corpus, {fields[key]!r} on the slide"
                    )
            return
    raise SystemExit(f"slide 5 example is stale: no document with doc_id {wanted!r}")


def build_data(out_dir: Path) -> Path:
    """What the 352 MB actually is, one real document, and what the job does to it."""

    _verify_sample_document()
    fig, ax = _canvas(13.6, 6.1)
    rows = _measure_corpus() or CORPUS_SNAPSHOT
    total_docs = sum(r[2] for r in rows)
    total_mb = sum(r[3] for r in rows)

    # ---- left: what is inside the corpus ---------------------------------
    _text(ax, 2.0, 95.5, "What the 352 MB contains", size=12.5, color=DEEP,
          weight="bold", ha="left")
    _text(ax, 2.0, 90.8, f"{total_docs:,} documents  \u00b7  one JSONL file  \u00b7  2010\u20132023",
          size=9.0, color=GREY, ha="left")

    bar_x, bar_w = 2.0, 44.0
    scale = bar_w / max(r[3] for r in rows)
    y = 84.0
    for i, (label, sub, count, megabytes) in enumerate(rows):
        _text(ax, bar_x, y + 4.4, label, size=9.4, color=INK, weight="bold", ha="left")
        _text(ax, bar_x + bar_w, y + 4.4, f"{count:,} docs", size=8.6,
              color=MAGENTA, family=MONO, ha="right")
        ax.add_patch(
            FancyBboxPatch(
                (bar_x, y - 1.2), max(megabytes * scale, 0.5), 4.0,
                boxstyle="round,pad=0,rounding_size=0.5",
                facecolor=DEEP if i == 0 else VIOLET, edgecolor="none", zorder=2,
            )
        )
        _text(ax, bar_x + max(megabytes * scale, 0.5) + 1.2, y + 0.8,
              f"{megabytes:.0f} MB", size=8.6, color=GREY, family=MONO, ha="left")
        _text(ax, bar_x, y - 3.6, sub, size=8.0, color=GREY, ha="left", style="italic")
        y -= 11.0

    _box(ax, 2.0, 35.0, 44.0, 10.6, fill=FILL_A, edge=VIOLET, lw=1.6)
    _text(ax, 24.0, 42.4, "Count and size tell different stories", size=9.4,
          color=DEEP, weight="bold")
    _text(ax, 24.0, 39.2, "Macro is 69% of the documents but 11% of the bytes:", size=8.4, color=INK)
    _text(ax, 24.0, 36.4, "a macro release is 2 KB, a 10-K section is 41 KB.", size=8.4, color=INK)

    # ---- right: one real document ----------------------------------------
    _text(ax, 52.0, 95.5, "One document, as stored", size=12.5, color=MAGENTA,
          weight="bold", ha="left")
    _text(ax, 98.0, 95.5, "9 of its 55 fields", size=8.6, color=GREY, ha="right")
    _text(ax, 52.0, 91.4, "a 10-K section \u2014 the type that is 76% of the corpus",
          size=8.6, color=GREY, ha="left", style="italic")

    _box(ax, 52.0, 47.0, 46.0, 42.5, fill=WHITE, edge=MAGENTA, lw=2.0)
    y = 86.0
    for key, value in SAMPLE_DOCUMENT:
        emphasis = key == "available_at"
        _text(ax, 54.0, y, key, size=8.0, color=GREY, family=MONO, ha="left")
        _text(ax, 68.5, y, value, size=7.6,
              color=MAGENTA if emphasis else INK, family=MONO,
              weight="bold" if emphasis else "normal", ha="left")
        y -= 4.0
    _text(ax, 54.0, 51.6, SAMPLE_BODY[0], size=7.4, color=GREY, family=MONO, ha="left")
    _text(ax, 54.0, 49.2, SAMPLE_BODY[1], size=7.4, color=GREY, family=MONO, ha="left")

    _text(ax, 52.0, 43.0,
          "Every document traces back: source URL, accession number, the parent",
          size=8.6, color=INK, ha="left")
    _text(ax, 52.0, 39.6,
          "filing, and the exact characters this section was cut from.",
          size=8.6, color=INK, ha="left")
    _text(ax, 52.0, 34.6,
          "available_at is the only date search filters on.",
          size=8.8, color=MAGENTA, ha="left", weight="bold")

    # ---- bottom: what one run does to it ---------------------------------
    ax.plot([2.0, 98.0], [29.0, 29.0], color="#E3DCEF", lw=1.4, zorder=1)
    _text(ax, 2.0, 25.2, "What one Spark run moves through it", size=11.5,
          color=DEEP, weight="bold", ha="left")

    chain = [
        (2.0, "352 MB", f"{total_docs:,} documents read", DEEP),
        (27.0, "45,055,627", "(term, 1) pairs emitted", VIOLET),
        (52.0, "shuffle", "hash(term) % partitions", VIOLET),
        (77.0, "79,705", "distinct terms out", MAGENTA),
    ]
    for i, (x0, big, sub, accent) in enumerate(chain):
        _box(ax, x0, 6.0, 21.0, 15.0, fill=WHITE, edge=accent, lw=2.0)
        _text(ax, x0 + 10.5, 15.6, big, size=14.0, color=accent,
              weight="bold", family=MONO)
        _text(ax, x0 + 10.5, 9.6, sub, size=8.2, color=GREY)
        if i < len(chain) - 1:
            _arrow(ax, (x0 + 21.6, 13.5), (x0 + 24.4, 13.5), color=GREY, lw=1.8)

    _text(ax, 50, 1.6,
          "1,709 pairs per document, collapsed 565\u00d7 \u2014 and repeated on every corpus update.",
          size=9.4, color=MAGENTA, weight="bold")

    return _save(fig, out_dir / "slide05_data.png")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT))
    parser.add_argument("--only", default="", help="Build a single figure by name.")
    args = parser.parse_args(argv)

    out_dir = Path(args.out_dir)
    builders = {
        "pipeline": build_pipeline,
        "architecture": build_architecture,
        "performance": build_performance,
        "data": build_data,
    }
    selected = {args.only: builders[args.only]} if args.only else builders
    print(f"Writing to {out_dir}")
    for name, fn in selected.items():
        fn(out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
