"""CLI: compute distributed corpus analytics over a corpus.

Example:

    python -m bigdata.run_corpus_analytics --corpus macro
    python -m bigdata.run_corpus_analytics --corpus all_ppo --engine spark --master local[*]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bigdata.cli_common import (  # noqa: E402
    Timer,
    add_common_args,
    build_engine,
    load_dataset,
    resolve_output_dir,
    run_context,
    write_csv,
    write_json,
)
from bigdata.jobs import corpus_analytics  # noqa: E402


def _write_tables(output_dir: Path, report: dict) -> None:
    """Emit the analytics report as flat CSVs for spreadsheets / the report."""

    def counts_csv(name: str, mapping: dict, key_header: str) -> None:
        write_csv(output_dir / name, [key_header, "count"], list(mapping.items()))

    counts_csv("by_source_family.csv", report.get("by_source_family", {}), "source_family")
    counts_csv("by_source_type.csv", report.get("by_source_type", {}), "source_type")
    counts_csv("by_reliability_tier.csv", report.get("by_reliability_tier", {}), "reliability_tier")
    counts_csv("by_year.csv", report.get("by_year", {}), "year")
    counts_csv("by_language.csv", report.get("by_language", {}), "language")

    def top_csv(name: str, rows: list, key_header: str) -> None:
        write_csv(output_dir / name, [key_header, "count"], [(r["name"], r["count"]) for r in rows])

    top_csv("top_tickers.csv", report.get("top_tickers", []), "ticker")
    top_csv("top_event_tags.csv", report.get("top_event_tags", []), "event_tag")
    top_csv("top_risk_terms.csv", report.get("top_risk_terms", []), "risk_term")


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description="Distributed corpus analytics.")
    add_common_args(parser)
    parser.add_argument("--top-n", type=int, default=25, help="Top-N tickers / events / risk terms to report.")
    args = parser.parse_args(argv)

    output_dir = resolve_output_dir(args, "analytics")
    context = run_context(args, "analytics")

    engine = build_engine(args)
    context["engine_used"] = engine.name
    print(f"[bigdata] corpus analytics | engine={engine.name} corpus={args.corpus}", flush=True)
    try:
        dataset, corpus_path = load_dataset(engine, args)
        with Timer() as timer:
            report = corpus_analytics.compute_analytics(dataset, top_n=args.top_n)
        context["corpus_path"] = str(corpus_path)
        context["seconds"] = round(timer.seconds, 3)
        report["run"] = context
        write_json(output_dir / "analytics.json", report)
        _write_tables(output_dir, report)
    finally:
        engine.stop()

    print(json.dumps({
        "engine": engine.name,
        "corpus": str(corpus_path),
        "total_documents": report.get("total_documents"),
        "by_source_family": report.get("by_source_family"),
        "by_year": report.get("by_year"),
        "avg_document_length_tokens": report.get("avg_document_length_tokens"),
        "point_in_time": report.get("point_in_time"),
        "seconds": context["seconds"],
        "output_dir": str(output_dir),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
