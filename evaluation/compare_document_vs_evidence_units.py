"""Compare document-level retrieval against evidence-unit retrieval.

The key evaluation issue is qrels grain. Existing qrels often label source
documents, while evidence-unit retrieval may return a smaller fact block inside
that source document. This script therefore keeps raw unit results, but can
also collapse units back to source-document IDs before computing IR metrics.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluation.build_annotation_pool import build_pool_records, write_pool  # noqa: E402
from evaluation.evaluate_ir_metrics import evaluate, load_qrels, load_run, summarize_by_method, write_metrics  # noqa: E402
from evaluation.evaluate_qrels_coverage import (  # noqa: E402
    COVERAGE_FIELDS,
    evaluate_qrels_coverage,
    summarize_coverage,
    write_csv as write_coverage_csv,
)
from evaluation.run_ablation_suite import configured_methods, load_query_requests, resolve_project_path, single_query_request  # noqa: E402
from features.build_evidence_units import build_evidence_units, summarize_evidence_units  # noqa: E402
from finportfolio_ir.io_utils import read_jsonl, write_jsonl  # noqa: E402
from retrieval.retrieve_for_portfolio import retrieval_records  # noqa: E402


METRIC_FIELDS = ["precision_at_5", "precision_at_10", "ndcg_at_5", "ndcg_at_10", "map", "mrr"]
EVIDENCE_EVAL_GRAINS = {"source_document", "parent", "unit"}


def _read_input_documents(inputs: Iterable[str | Path]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for raw in inputs:
        for item in str(raw).split(","):
            path = item.strip()
            if path:
                records.extend(read_jsonl(path))
    return records


def _with_method(records: Iterable[dict[str, object]], method: str) -> list[dict[str, object]]:
    output: list[dict[str, object]] = []
    for record in records:
        row = dict(record)
        row["method"] = method
        output.append(row)
    return output


def _evidence_eval_doc_id(record: dict[str, object], grain: str) -> str:
    unit_doc_id = str(record.get("evidence_unit_id") or record.get("doc_id") or "")
    parent_doc_id = str(record.get("parent_doc_id") or "")
    unit_type = str(record.get("evidence_unit_type") or "")
    if grain == "unit":
        return unit_doc_id
    if grain == "parent":
        return parent_doc_id or unit_doc_id
    if grain == "source_document":
        if unit_type in {"company_ir_fact_block", "document_block"}:
            return parent_doc_id or unit_doc_id
        return unit_doc_id
    raise ValueError(f"Unknown evidence evaluation grain: {grain}")


def collapse_evidence_unit_records(
    records: Iterable[dict[str, object]],
    *,
    grain: str,
    method_label: str,
) -> list[dict[str, object]]:
    """Deduplicate ranked evidence units into the requested qrels grain.

    For example, several company IR fact blocks can point to the same
    `parent_doc_id`. The first ranked block wins, then ranks are reassigned.
    """

    if grain not in EVIDENCE_EVAL_GRAINS:
        raise ValueError(f"Unsupported grain {grain!r}; expected {sorted(EVIDENCE_EVAL_GRAINS)}")

    grouped: dict[str, list[dict[str, object]]] = {}
    for record in records:
        query_id = str(record.get("query_id", ""))
        grouped.setdefault(query_id, []).append(dict(record))

    collapsed: list[dict[str, object]] = []
    for query_id, rows in sorted(grouped.items()):
        rows.sort(key=lambda row: (int(row.get("rank", 0) or 0), -float(row.get("final_score", 0.0) or 0.0)))
        seen_doc_ids: set[str] = set()
        kept: list[dict[str, object]] = []
        for row in rows:
            eval_doc_id = _evidence_eval_doc_id(row, grain)
            if not eval_doc_id or eval_doc_id in seen_doc_ids:
                continue
            seen_doc_ids.add(eval_doc_id)
            row["evaluated_doc_id"] = eval_doc_id
            row["evidence_unit_doc_id"] = row.get("doc_id", "")
            row["doc_id"] = eval_doc_id
            row["method"] = method_label
            kept.append(row)
        for rank, row in enumerate(kept, start=1):
            row["rank"] = rank
            collapsed.append(row)
    return collapsed


def write_run_csv(path: Path, records: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["query_id", "doc_id", "rank", "score", "method"])
        writer.writeheader()
        for record in records:
            writer.writerow(
                {
                    "query_id": record["query_id"],
                    "doc_id": record["doc_id"],
                    "rank": record["rank"],
                    "score": record["final_score"],
                    "method": record["method"],
                }
            )


def write_csv(path: Path, rows: list[dict[str, object]], fieldnames: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        fieldnames = list(rows[0].keys()) if rows else ["status"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def build_comparison_rows(summary_rows: list[dict[str, object]], methods: list[str], grains: list[str]) -> list[dict[str, object]]:
    by_method = {str(row["method"]): row for row in summary_rows}
    rows: list[dict[str, object]] = []
    for method in methods:
        document_label = f"document__{method}"
        document = by_method.get(document_label)
        if not document:
            continue
        for grain in grains:
            evidence_label = f"evidence_unit_{grain}__{method}"
            evidence = by_method.get(evidence_label)
            if not evidence:
                continue
            row: dict[str, object] = {
                "base_method": method,
                "evidence_eval_grain": grain,
                "document_method": document_label,
                "evidence_method": evidence_label,
                "query_count": evidence.get("query_count", 0),
            }
            for metric in METRIC_FIELDS:
                doc_value = float(document.get(metric, 0.0) or 0.0)
                evidence_value = float(evidence.get(metric, 0.0) or 0.0)
                row[f"document_{metric}"] = doc_value
                row[f"evidence_unit_{metric}"] = evidence_value
                row[f"delta_{metric}"] = evidence_value - doc_value
            rows.append(row)
    return rows


def compare_document_vs_evidence_units(
    *,
    documents: list[str],
    output_dir: Path,
    metadata_path: Path,
    config_path: Path,
    top_k: int,
    methods: list[str],
    evidence_eval_grains: list[str],
    portfolio_path: str = "",
    decision_datetime: str = "",
    query_id: str | None = None,
    queries_path: str = "",
    qrels_path: str = "",
    company_max_chars: int = 1400,
    company_min_chars: int = 120,
    include_unknown_documents: bool = False,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    query_requests = (
        load_query_requests(queries_path)
        if queries_path
        else single_query_request(portfolio_path, decision_datetime, query_id)
    )

    input_records = _read_input_documents(documents)
    input_documents_path = output_dir / "input_documents.jsonl"
    write_jsonl(input_documents_path, input_records)
    evidence_units = build_evidence_units(
        input_records,
        company_max_chars=company_max_chars,
        company_min_chars=company_min_chars,
        include_unknown_documents=include_unknown_documents,
    )
    evidence_units_path = output_dir / "evidence_units.jsonl"
    evidence_units_summary_path = output_dir / "evidence_units_summary.json"
    write_jsonl(evidence_units_path, evidence_units)
    evidence_units_summary = summarize_evidence_units(evidence_units, documents)
    evidence_units_summary_path.write_text(json.dumps(evidence_units_summary, ensure_ascii=False, indent=2), encoding="utf-8")

    document_records_all: list[dict[str, object]] = []
    evidence_raw_records_all: list[dict[str, object]] = []
    evidence_eval_records_all: list[dict[str, object]] = []
    run_records: list[dict[str, object]] = []

    for method in methods:
        document_method_records: list[dict[str, object]] = []
        evidence_method_raw: list[dict[str, object]] = []
        for request in query_requests:
            document_method_records.extend(
                retrieval_records(
                    documents_path=input_documents_path,
                    portfolio_path=resolve_project_path(request["portfolio"]),
                    metadata_path=metadata_path,
                    decision_datetime_text=request["decision_datetime"],
                    config_path=config_path,
                    top_k=top_k,
                    query_id=request["query_id"] or None,
                    method=method,
                )
            )
            evidence_method_raw.extend(
                retrieval_records(
                    documents_path=evidence_units_path,
                    portfolio_path=resolve_project_path(request["portfolio"]),
                    metadata_path=metadata_path,
                    decision_datetime_text=request["decision_datetime"],
                    config_path=config_path,
                    top_k=top_k,
                    query_id=request["query_id"] or None,
                    method=method,
                )
            )

        document_labeled = _with_method(document_method_records, f"document__{method}")
        evidence_raw_labeled = _with_method(evidence_method_raw, f"evidence_unit_raw__{method}")
        document_records_all.extend(document_labeled)
        evidence_raw_records_all.extend(evidence_raw_labeled)
        run_records.extend(document_labeled)

        for grain in evidence_eval_grains:
            evidence_eval_labeled = collapse_evidence_unit_records(
                evidence_method_raw,
                grain=grain,
                method_label=f"evidence_unit_{grain}__{method}",
            )
            evidence_eval_records_all.extend(evidence_eval_labeled)
            run_records.extend(evidence_eval_labeled)

    write_jsonl(output_dir / "document_retrieved_all.jsonl", document_records_all)
    write_jsonl(output_dir / "evidence_unit_raw_retrieved_all.jsonl", evidence_raw_records_all)
    write_jsonl(output_dir / "evidence_unit_eval_retrieved_all.jsonl", evidence_eval_records_all)
    run_csv_path = output_dir / "comparison_run.csv"
    write_run_csv(run_csv_path, run_records)

    metrics_path = output_dir / "comparison_metrics.csv"
    summary_path = output_dir / "comparison_metrics_by_method.csv"
    delta_path = output_dir / "comparison_delta_by_method.csv"
    coverage_path = output_dir / "qrels_coverage.csv"
    coverage_summary_path = output_dir / "qrels_coverage_summary.csv"
    annotation_pool_path = output_dir / "comparison_annotation_pool.csv"
    metrics: list[dict[str, object]] = []
    summary: list[dict[str, object]] = []
    comparison_rows: list[dict[str, object]] = []
    coverage_rows: list[dict[str, object]] = []
    coverage_summary_rows: list[dict[str, object]] = []
    qrels: dict[str, dict[str, int]] | None = None
    if qrels_path:
        qrels = load_qrels(qrels_path)
        loaded_run = load_run(run_csv_path)
        metrics = evaluate(qrels, loaded_run)
        summary = summarize_by_method(metrics)
        comparison_rows = build_comparison_rows(summary, methods, evidence_eval_grains)
        write_metrics(metrics_path, metrics)
        write_metrics(summary_path, summary)
        write_csv(delta_path, comparison_rows)
        coverage_rows = evaluate_qrels_coverage(qrels, loaded_run)
        coverage_summary_rows = summarize_coverage(coverage_rows)
        write_coverage_csv(coverage_path, coverage_rows, COVERAGE_FIELDS)
        write_coverage_csv(
            coverage_summary_path,
            coverage_summary_rows,
            [
                "method",
                "query_count",
                "mean_judged_rate_at_5",
                "mean_judged_rate_at_10",
                "queries_below_80pct_at_10",
                "queries_below_100pct_at_10",
            ],
        )

    annotation_pool_rows = build_pool_records(run_records, qrels=qrels)
    write_pool(annotation_pool_path, annotation_pool_rows)

    min_judged_rate_at_10 = (
        min(float(row.get("mean_judged_rate_at_10", 0.0) or 0.0) for row in coverage_summary_rows)
        if coverage_summary_rows
        else None
    )
    coverage_warning = ""
    if min_judged_rate_at_10 is not None and min_judged_rate_at_10 < 0.8:
        coverage_warning = (
            "Qrels coverage is too low for reliable metric interpretation. "
            "Review comparison_annotation_pool.csv and export matching qrels."
        )

    manifest: dict[str, Any] = {
        "status": "completed",
        "documents": documents,
        "output_dir": str(output_dir),
        "query_count": len(query_requests),
        "methods": methods,
        "top_k": top_k,
        "qrels": qrels_path,
        "evidence_eval_grains": evidence_eval_grains,
        "input_document_count": len(input_records),
        "evidence_unit_count": len(evidence_units),
        "document_retrieved_count": len(document_records_all),
        "evidence_unit_raw_retrieved_count": len(evidence_raw_records_all),
        "evidence_unit_eval_retrieved_count": len(evidence_eval_records_all),
        "comparison_run": str(run_csv_path),
        "comparison_metrics": str(metrics_path) if qrels_path else "",
        "comparison_metrics_by_method": str(summary_path) if qrels_path else "",
        "comparison_delta_by_method": str(delta_path) if qrels_path else "",
        "qrels_coverage": str(coverage_path) if qrels_path else "",
        "qrels_coverage_summary": str(coverage_summary_path) if qrels_path else "",
        "annotation_pool": str(annotation_pool_path),
        "annotation_pool_count": len(annotation_pool_rows),
        "min_mean_judged_rate_at_10": min_judged_rate_at_10,
        "coverage_warning": coverage_warning,
        "input_documents": str(input_documents_path),
        "evidence_units": str(evidence_units_path),
        "evidence_units_summary": str(evidence_units_summary_path),
        "unit_summary": evidence_units_summary,
    }
    (output_dir / "comparison_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def _parse_methods(raw_methods: list[str], config_path: str) -> list[str]:
    values: list[str] = []
    for raw in raw_methods:
        values.extend(part.strip() for part in raw.split(",") if part.strip())
    return values or configured_methods(config_path)


def _parse_grains(raw_grains: list[str]) -> list[str]:
    values: list[str] = []
    for raw in raw_grains:
        values.extend(part.strip() for part in raw.split(",") if part.strip())
    values = values or ["source_document"]
    unknown = sorted(set(values).difference(EVIDENCE_EVAL_GRAINS))
    if unknown:
        raise ValueError(f"Unknown evidence eval grain(s): {unknown}; expected {sorted(EVIDENCE_EVAL_GRAINS)}")
    return values


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare document retrieval with evidence-unit retrieval on qrels.")
    parser.add_argument("--documents", required=True, nargs="+", help="Normalized document JSONL files; comma-separated values are accepted.")
    parser.add_argument("--portfolio", default="")
    parser.add_argument("--queries", default="", help="CSV with query_id,portfolio,decision_datetime columns.")
    parser.add_argument("--metadata", default="data/processed_documents/ticker_metadata.csv")
    parser.add_argument("--config", default="configs/default.yaml")
    parser.add_argument("--decision-datetime", default="")
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--qrels", default="")
    parser.add_argument("--query-id", default=None)
    parser.add_argument("--method", action="append", default=[], help="Ranking method. Repeat or pass comma-separated values. Defaults to all configured methods.")
    parser.add_argument("--evidence-eval-grain", action="append", default=[], help="unit, parent, or source_document. Repeat or pass comma-separated values.")
    parser.add_argument("--company-max-chars", type=int, default=1400)
    parser.add_argument("--company-min-chars", type=int, default=120)
    parser.add_argument("--include-unknown-documents", action="store_true")
    args = parser.parse_args(argv)

    manifest = compare_document_vs_evidence_units(
        documents=args.documents,
        output_dir=Path(args.output_dir),
        metadata_path=Path(args.metadata),
        config_path=Path(args.config),
        top_k=args.top_k,
        methods=_parse_methods(args.method, args.config),
        evidence_eval_grains=_parse_grains(args.evidence_eval_grain),
        portfolio_path=args.portfolio,
        decision_datetime=args.decision_datetime,
        query_id=args.query_id,
        queries_path=args.queries,
        qrels_path=args.qrels,
        company_max_chars=args.company_max_chars,
        company_min_chars=args.company_min_chars,
        include_unknown_documents=args.include_unknown_documents,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
