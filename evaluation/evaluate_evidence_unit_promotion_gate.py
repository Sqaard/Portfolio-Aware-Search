"""Evaluate whether calibrated evidence-unit retrieval passes promotion rules.

This script compares document, raw evidence-unit, and calibrated evidence-unit
runs on the same qrels file. It writes metrics, coverage, deltas, and a compact
acceptance report. It intentionally blocks promotion when there are too few
human spot-check labels, even if assistant/mixed metrics look perfect.
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Optional, Union

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluation.evaluate_ir_metrics import evaluate, load_qrels, load_run, summarize_by_method, write_metrics
from evaluation.evaluate_qrels_coverage import COVERAGE_FIELDS, evaluate_qrels_coverage, summarize_coverage, write_csv as write_coverage_csv


DELTA_FIELDS = [
    "document_method",
    "raw_evidence_method",
    "calibrated_method",
    "document_precision_at_10",
    "raw_evidence_precision_at_10",
    "calibrated_precision_at_10",
    "delta_calibrated_vs_document_precision_at_10",
    "document_ndcg_at_10",
    "raw_evidence_ndcg_at_10",
    "calibrated_ndcg_at_10",
    "delta_calibrated_vs_document_ndcg_at_10",
    "document_mrr",
    "raw_evidence_mrr",
    "calibrated_mrr",
    "delta_calibrated_vs_document_mrr",
]

ACCEPTANCE_FIELDS = [
    "decision",
    "reason",
    "human_label_count",
    "qrels_count",
    "min_human_labels",
    "document_method",
    "calibrated_method",
    "document_ndcg_at_10",
    "calibrated_ndcg_at_10",
    "delta_ndcg_at_10",
    "document_precision_at_10",
    "calibrated_precision_at_10",
    "delta_precision_at_10",
    "min_ndcg_delta",
    "max_precision_drop",
    "min_judged_rate_at_10",
    "calibrated_judged_rate_at_10",
]


def read_csv(path: Union[str, Path]) -> list[dict[str, str]]:
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Union[str, Path], rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _combined_runs(*paths: Union[str, Path]) -> dict[tuple[str, str], list[dict[str, object]]]:
    combined: dict[tuple[str, str], list[dict[str, object]]] = {}
    for path in paths:
        for key, rows in load_run(path).items():
            combined[key] = rows
    return combined


def _summary_by_name(summary_rows: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    return {str(row["method"]): row for row in summary_rows}


def _choose_methods(summary_rows: list[dict[str, object]]) -> tuple[str, str, str]:
    methods = [str(row["method"]) for row in summary_rows]
    document = next((method for method in methods if method.startswith("document__")), "")
    calibrated = next((method for method in methods if method.startswith("evidence_unit_calibrated")), "")
    raw = next((method for method in methods if method.startswith("evidence_unit_") and not method.startswith("evidence_unit_calibrated")), "")
    return document, raw, calibrated


def _human_label_count(qrels_rows: list[dict[str, str]]) -> int:
    return sum(
        1
        for row in qrels_rows
        if str(row.get("label_source", "")).startswith("human_") or str(row.get("annotator", "")).lower() in {"human", "user_chat", "reviewer_1"}
    )


def label_source_counts(qrels_rows: list[dict[str, str]]) -> list[dict[str, str]]:
    counts = Counter(str(row.get("label_source", "") or "unknown") for row in qrels_rows)
    return [{"label_source": key, "rows": str(value)} for key, value in sorted(counts.items())]


def build_delta(summary_rows: list[dict[str, object]]) -> list[dict[str, object]]:
    by_method = _summary_by_name(summary_rows)
    document_method, raw_method, calibrated_method = _choose_methods(summary_rows)
    document = by_method.get(document_method, {})
    raw = by_method.get(raw_method, {})
    calibrated = by_method.get(calibrated_method, {})

    def metric(row: dict[str, object], name: str) -> float:
        try:
            return float(row.get(name, 0.0))
        except (TypeError, ValueError):
            return 0.0

    document_precision = metric(document, "precision_at_10")
    raw_precision = metric(raw, "precision_at_10")
    calibrated_precision = metric(calibrated, "precision_at_10")
    document_ndcg = metric(document, "ndcg_at_10")
    raw_ndcg = metric(raw, "ndcg_at_10")
    calibrated_ndcg = metric(calibrated, "ndcg_at_10")
    document_mrr = metric(document, "mrr")
    raw_mrr = metric(raw, "mrr")
    calibrated_mrr = metric(calibrated, "mrr")
    return [
        {
            "document_method": document_method,
            "raw_evidence_method": raw_method,
            "calibrated_method": calibrated_method,
            "document_precision_at_10": document_precision,
            "raw_evidence_precision_at_10": raw_precision,
            "calibrated_precision_at_10": calibrated_precision,
            "delta_calibrated_vs_document_precision_at_10": calibrated_precision - document_precision,
            "document_ndcg_at_10": document_ndcg,
            "raw_evidence_ndcg_at_10": raw_ndcg,
            "calibrated_ndcg_at_10": calibrated_ndcg,
            "delta_calibrated_vs_document_ndcg_at_10": calibrated_ndcg - document_ndcg,
            "document_mrr": document_mrr,
            "raw_evidence_mrr": raw_mrr,
            "calibrated_mrr": calibrated_mrr,
            "delta_calibrated_vs_document_mrr": calibrated_mrr - document_mrr,
        }
    ]


def build_acceptance(
    *,
    summary_rows: list[dict[str, object]],
    coverage_summary_rows: list[dict[str, object]],
    qrels_rows: list[dict[str, str]],
    min_human_labels: int = 10,
    min_ndcg_delta: float = 0.02,
    max_precision_drop: float = 0.02,
    min_judged_rate_at_10: float = 1.0,
) -> list[dict[str, object]]:
    by_method = _summary_by_name(summary_rows)
    coverage_by_method = _summary_by_name(coverage_summary_rows)
    document_method, _, calibrated_method = _choose_methods(summary_rows)
    document = by_method.get(document_method, {})
    calibrated = by_method.get(calibrated_method, {})

    def metric(row: dict[str, object], name: str) -> float:
        try:
            return float(row.get(name, 0.0))
        except (TypeError, ValueError):
            return 0.0

    human_count = _human_label_count(qrels_rows)
    document_ndcg = metric(document, "ndcg_at_10")
    calibrated_ndcg = metric(calibrated, "ndcg_at_10")
    document_precision = metric(document, "precision_at_10")
    calibrated_precision = metric(calibrated, "precision_at_10")
    calibrated_judged = metric(coverage_by_method.get(calibrated_method, {}), "mean_judged_rate_at_10")
    delta_ndcg = calibrated_ndcg - document_ndcg
    delta_precision = calibrated_precision - document_precision

    reasons: list[str] = []
    if not document_method or not calibrated_method:
        reasons.append("missing_document_or_calibrated_method")
    if human_count < min_human_labels:
        reasons.append("pending_human_spotcheck_labels")
    if delta_ndcg < min_ndcg_delta:
        reasons.append("ndcg_delta_below_threshold")
    if delta_precision < -max_precision_drop:
        reasons.append("precision_drop_too_large")
    if calibrated_judged < min_judged_rate_at_10:
        reasons.append("insufficient_top10_qrels_coverage")

    decision = "accept_guarded_promotion" if not reasons else "block_promotion"
    return [
        {
            "decision": decision,
            "reason": "|".join(reasons) if reasons else "all_thresholds_passed",
            "human_label_count": human_count,
            "qrels_count": len(qrels_rows),
            "min_human_labels": min_human_labels,
            "document_method": document_method,
            "calibrated_method": calibrated_method,
            "document_ndcg_at_10": document_ndcg,
            "calibrated_ndcg_at_10": calibrated_ndcg,
            "delta_ndcg_at_10": delta_ndcg,
            "document_precision_at_10": document_precision,
            "calibrated_precision_at_10": calibrated_precision,
            "delta_precision_at_10": delta_precision,
            "min_ndcg_delta": min_ndcg_delta,
            "max_precision_drop": max_precision_drop,
            "min_judged_rate_at_10": min_judged_rate_at_10,
            "calibrated_judged_rate_at_10": calibrated_judged,
        }
    ]


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate calibrated evidence-unit promotion gate.")
    parser.add_argument("--qrels", required=True)
    parser.add_argument("--comparison-run", required=True, help="Run containing document and raw evidence-unit methods.")
    parser.add_argument("--calibrated-run", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--prefix", default="evidence_unit_promotion")
    parser.add_argument("--min-human-labels", type=int, default=10)
    parser.add_argument("--min-ndcg-delta", type=float, default=0.02)
    parser.add_argument("--max-precision-drop", type=float, default=0.02)
    parser.add_argument("--min-judged-rate-at-10", type=float, default=1.0)
    args = parser.parse_args(argv)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    qrels = load_qrels(args.qrels)
    qrels_rows = read_csv(args.qrels)
    runs = _combined_runs(args.comparison_run, args.calibrated_run)

    metric_rows = evaluate(qrels, runs)
    summary_rows = summarize_by_method(metric_rows)
    coverage_rows = evaluate_qrels_coverage(qrels, runs)
    coverage_summary_rows = summarize_coverage(coverage_rows)
    delta_rows = build_delta(summary_rows)
    acceptance_rows = build_acceptance(
        summary_rows=summary_rows,
        coverage_summary_rows=coverage_summary_rows,
        qrels_rows=qrels_rows,
        min_human_labels=args.min_human_labels,
        min_ndcg_delta=args.min_ndcg_delta,
        max_precision_drop=args.max_precision_drop,
        min_judged_rate_at_10=args.min_judged_rate_at_10,
    )

    write_metrics(output_dir / f"{args.prefix}_metrics.csv", metric_rows)
    write_metrics(output_dir / f"{args.prefix}_metrics_by_method.csv", summary_rows)
    write_coverage_csv(output_dir / f"{args.prefix}_qrels_coverage.csv", coverage_rows, COVERAGE_FIELDS)
    write_coverage_csv(
        output_dir / f"{args.prefix}_qrels_coverage_summary.csv",
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
    write_csv(output_dir / f"{args.prefix}_delta.csv", delta_rows, DELTA_FIELDS)
    write_csv(output_dir / f"{args.prefix}_acceptance.csv", acceptance_rows, ACCEPTANCE_FIELDS)
    write_csv(output_dir / f"{args.prefix}_label_source_counts.csv", label_source_counts(qrels_rows), ["label_source", "rows"])

    print(
        {
            "decision": acceptance_rows[0]["decision"],
            "reason": acceptance_rows[0]["reason"],
            "human_label_count": acceptance_rows[0]["human_label_count"],
            "delta_ndcg_at_10": acceptance_rows[0]["delta_ndcg_at_10"],
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
