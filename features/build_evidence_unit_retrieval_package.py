"""Build and retrieve over evidence units in one reproducible package."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from features.build_evidence_units import build_evidence_units, summarize_evidence_units  # noqa: E402
from features.export_evidence_bundles import build_evidence_bundles  # noqa: E402
from finportfolio_ir.io_utils import read_jsonl, write_jsonl  # noqa: E402
from retrieval.retrieve_for_portfolio import retrieval_records, write_run_csv  # noqa: E402


def _read_input_documents(inputs: list[str]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for raw in inputs:
        for item in str(raw).split(","):
            path = item.strip()
            if path:
                records.extend(read_jsonl(path))
    return records


def build_evidence_unit_retrieval_package(
    *,
    inputs: list[str],
    output_dir: Path,
    portfolio_path: Path,
    metadata_path: Path,
    config_path: Path,
    decision_datetime: str,
    top_k: int,
    method: str,
    company_max_chars: int = 1400,
    company_min_chars: int = 120,
    include_unknown_documents: bool = False,
    build_sqlite_index: bool = False,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    evidence_units_path = output_dir / "evidence_units.jsonl"
    evidence_units_summary_path = output_dir / "evidence_units_summary.json"
    retrieved_contexts_path = output_dir / "retrieved_contexts.jsonl"
    evidence_bundles_path = output_dir / "evidence_bundles.jsonl"
    run_csv_path = output_dir / "run.csv"
    sqlite_index_path = output_dir / "evidence_units.sqlite"
    manifest_path = output_dir / "manifest.json"

    input_records = _read_input_documents(inputs)
    units = build_evidence_units(
        input_records,
        company_max_chars=company_max_chars,
        company_min_chars=company_min_chars,
        include_unknown_documents=include_unknown_documents,
    )
    write_jsonl(evidence_units_path, units)
    unit_summary = summarize_evidence_units(units, inputs)
    evidence_units_summary_path.write_text(json.dumps(unit_summary, ensure_ascii=False, indent=2), encoding="utf-8")

    retrieved = retrieval_records(
        documents_path=evidence_units_path,
        portfolio_path=portfolio_path,
        metadata_path=metadata_path,
        decision_datetime_text=decision_datetime,
        config_path=config_path,
        top_k=top_k,
        method=method,
    )
    write_jsonl(retrieved_contexts_path, retrieved)
    write_run_csv(run_csv_path, retrieved, method=method)

    bundles = build_evidence_bundles(retrieved)
    write_jsonl(evidence_bundles_path, bundles)

    sqlite_index_summary: dict[str, Any] | None = None
    if build_sqlite_index:
        from indexing.build_search_index import build_search_index  # noqa: WPS433 - optional heavy import

        sqlite_index_summary = build_search_index(
            documents_path=evidence_units_path,
            output_path=sqlite_index_path,
        )

    manifest: dict[str, Any] = {
        "status": "completed",
        "inputs": inputs,
        "output_dir": str(output_dir),
        "evidence_units": str(evidence_units_path),
        "evidence_units_summary": str(evidence_units_summary_path),
        "retrieved_contexts": str(retrieved_contexts_path),
        "evidence_bundles": str(evidence_bundles_path),
        "run_csv": str(run_csv_path),
        "sqlite_index": str(sqlite_index_path) if build_sqlite_index else "",
        "portfolio": str(portfolio_path),
        "metadata": str(metadata_path),
        "config": str(config_path),
        "decision_datetime": decision_datetime,
        "method": method,
        "top_k": top_k,
        "input_document_count": len(input_records),
        "evidence_unit_count": len(units),
        "retrieved_context_count": len(retrieved),
        "evidence_bundle_count": len(bundles),
        "unit_summary": unit_summary,
        "sqlite_index_summary": sqlite_index_summary,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build evidence units and retrieve over them.")
    parser.add_argument("--input", required=True, nargs="+", help="Normalized document JSONL files; comma-separated values are accepted.")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--portfolio", default="configs/sample_portfolio.yaml")
    parser.add_argument("--metadata", default="data/processed_documents/ticker_metadata.csv")
    parser.add_argument("--config", default="configs/default.yaml")
    parser.add_argument("--decision-datetime", required=True)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--method", default="full_hybrid")
    parser.add_argument("--company-max-chars", type=int, default=1400)
    parser.add_argument("--company-min-chars", type=int, default=120)
    parser.add_argument("--include-unknown-documents", action="store_true")
    parser.add_argument("--build-sqlite-index", action="store_true")
    args = parser.parse_args(argv)

    manifest = build_evidence_unit_retrieval_package(
        inputs=args.input,
        output_dir=Path(args.output_dir),
        portfolio_path=Path(args.portfolio),
        metadata_path=Path(args.metadata),
        config_path=Path(args.config),
        decision_datetime=args.decision_datetime,
        top_k=args.top_k,
        method=args.method,
        company_max_chars=args.company_max_chars,
        company_min_chars=args.company_min_chars,
        include_unknown_documents=args.include_unknown_documents,
        build_sqlite_index=args.build_sqlite_index,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
