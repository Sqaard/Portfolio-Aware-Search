"""Source/type-aware calibration for evidence-unit retrieval runs.

This is an evaluation utility, not the default production ranker. It applies a
small transparent score adjustment to evidence-unit rows, reranks per query,
then optionally collapses units back to the qrels grain for evaluation.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluation.compare_document_vs_evidence_units import (  # noqa: E402
    build_comparison_rows,
    collapse_evidence_unit_records,
    write_csv,
    write_run_csv,
)
from evaluation.evaluate_ir_metrics import evaluate, load_qrels, load_run, summarize_by_method, write_metrics  # noqa: E402
from evaluation.evaluate_qrels_coverage import (  # noqa: E402
    COVERAGE_FIELDS,
    evaluate_qrels_coverage,
    summarize_coverage,
    write_csv as write_coverage_csv,
)
from finportfolio_ir.io_utils import read_jsonl, write_jsonl  # noqa: E402
from retrieval.evidence_unit_calibration import (  # noqa: E402
    DEFAULT_EVIDENCE_UNIT_WEIGHTS,
    active_evidence_unit_calibration_tags,
    evidence_unit_calibration_delta,
    evidence_unit_calibration_features,
    published_year,
)


DEFAULT_WEIGHTS = DEFAULT_EVIDENCE_UNIT_WEIGHTS


def calibrate_evidence_unit_records(
    records: list[dict[str, object]],
    *,
    weights: dict[str, float] | None = None,
    method_label: str = "evidence_unit_calibrated",
    top_k: int = 10,
) -> list[dict[str, object]]:
    weights = weights or DEFAULT_WEIGHTS
    grouped: dict[str, list[dict[str, object]]] = {}
    for record in records:
        grouped.setdefault(str(record.get("query_id", "")), []).append(dict(record))

    calibrated: list[dict[str, object]] = []
    for query_id, rows in sorted(grouped.items()):
        scored_rows: list[dict[str, object]] = []
        for row in rows:
            delta = evidence_unit_calibration_delta(row, weights)
            base_score = float(row.get("final_score", 0.0) or 0.0)
            row["base_final_score"] = base_score
            row["calibration_score_delta"] = round(delta, 6)
            row["calibrated_final_score"] = round(base_score + delta, 6)
            row["final_score"] = row["calibrated_final_score"]
            row["method"] = method_label
            row["evidence_calibration_tags"] = active_evidence_unit_calibration_tags(row)
            row["evidence_calibration_weights_version"] = "source_type_v1"
            scored_rows.append(row)

        scored_rows.sort(key=lambda row: (-float(row["calibrated_final_score"]), str(row.get("doc_id", ""))))
        for rank, row in enumerate(scored_rows[:top_k], start=1):
            row["rank"] = rank
            calibrated.append(row)
    return calibrated


def parse_weights(overrides: list[str]) -> dict[str, float]:
    weights = dict(DEFAULT_WEIGHTS)
    for raw in overrides:
        for item in raw.split(","):
            item = item.strip()
            if not item:
                continue
            if "=" not in item:
                raise ValueError(f"Expected weight override as name=value, got {item!r}")
            name, value = item.split("=", 1)
            if name not in weights:
                raise ValueError(f"Unknown calibration weight {name!r}; expected one of {sorted(weights)}")
            weights[name] = float(value)
    return weights


def _write_weights(path: Path, weights: dict[str, float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(weights, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_summary_rows(path: Path) -> list[dict[str, object]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Calibrate and evaluate an evidence-unit retrieval run.")
    parser.add_argument("--input", required=True, help="Raw evidence-unit retrieval JSONL.")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--method-label", default="evidence_unit_calibrated_source_document__full_hybrid")
    parser.add_argument("--eval-grain", default="source_document")
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--qrels", default="")
    parser.add_argument("--document-summary", default="", help="Optional document metrics-by-method CSV for delta comparison.")
    parser.add_argument("--document-method", default="document__full_hybrid")
    parser.add_argument("--weight", action="append", default=[], help="Override calibration weight, e.g. fresh_2021_plus=0.10. Repeat or comma-separate.")
    args = parser.parse_args(argv)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    weights = parse_weights(args.weight)

    raw_records = read_jsonl(args.input)
    calibrated_raw = calibrate_evidence_unit_records(
        raw_records,
        weights=weights,
        method_label=f"raw__{args.method_label}",
        top_k=args.top_k,
    )
    calibrated_eval = collapse_evidence_unit_records(
        calibrated_raw,
        grain=args.eval_grain,
        method_label=args.method_label,
    )
    calibrated_eval = calibrated_eval[: args.top_k * len({str(row.get("query_id", "")) for row in calibrated_eval})]

    raw_path = output_dir / "evidence_unit_calibrated_raw.jsonl"
    eval_path = output_dir / "evidence_unit_calibrated_eval.jsonl"
    run_path = output_dir / "evidence_unit_calibrated_run.csv"
    weights_path = output_dir / "evidence_unit_calibration_weights.json"
    write_jsonl(raw_path, calibrated_raw)
    write_jsonl(eval_path, calibrated_eval)
    write_run_csv(run_path, calibrated_eval)
    _write_weights(weights_path, weights)

    manifest: dict[str, Any] = {
        "status": "completed",
        "input": args.input,
        "output_dir": str(output_dir),
        "method_label": args.method_label,
        "eval_grain": args.eval_grain,
        "top_k": args.top_k,
        "raw_records": len(raw_records),
        "calibrated_raw_records": len(calibrated_raw),
        "calibrated_eval_records": len(calibrated_eval),
        "weights": str(weights_path),
        "calibrated_raw": str(raw_path),
        "calibrated_eval": str(eval_path),
        "calibrated_run": str(run_path),
    }

    if args.qrels:
        qrels = load_qrels(args.qrels)
        loaded_run = load_run(run_path)
        metrics = evaluate(qrels, loaded_run)
        summary = summarize_by_method(metrics)
        metrics_path = output_dir / "evidence_unit_calibrated_metrics.csv"
        summary_path = output_dir / "evidence_unit_calibrated_metrics_by_method.csv"
        coverage_path = output_dir / "evidence_unit_calibrated_qrels_coverage.csv"
        coverage_summary_path = output_dir / "evidence_unit_calibrated_qrels_coverage_summary.csv"
        write_metrics(metrics_path, metrics)
        write_metrics(summary_path, summary)
        coverage = evaluate_qrels_coverage(qrels, loaded_run)
        write_coverage_csv(coverage_path, coverage, COVERAGE_FIELDS)
        write_coverage_csv(
            coverage_summary_path,
            summarize_coverage(coverage),
            [
                "method",
                "query_count",
                "mean_judged_rate_at_5",
                "mean_judged_rate_at_10",
                "queries_below_80pct_at_10",
                "queries_below_100pct_at_10",
            ],
        )
        manifest.update(
            {
                "qrels": args.qrels,
                "metrics": str(metrics_path),
                "metrics_by_method": str(summary_path),
                "qrels_coverage": str(coverage_path),
                "qrels_coverage_summary": str(coverage_summary_path),
                "summary": summary,
            }
        )

        if args.document_summary:
            comparison_rows = build_comparison_rows(
                _read_summary_rows(Path(args.document_summary)) + summary,
                methods=["full_hybrid"],
                grains=["calibrated_source_document"],
            )
            # build_comparison_rows expects evidence_unit_<grain>__<method>;
            # write a direct delta row if the method naming is custom.
            if not comparison_rows:
                document_rows = {str(row["method"]): row for row in _read_summary_rows(Path(args.document_summary))}
                document = document_rows.get(args.document_method)
                evidence = summary[0] if summary else None
                if document and evidence:
                    comparison_rows = [
                        {
                            "base_method": "full_hybrid",
                            "evidence_eval_grain": args.eval_grain,
                            "document_method": args.document_method,
                            "evidence_method": args.method_label,
                            "query_count": evidence.get("query_count", 0),
                            **{
                                f"document_{metric}": float(document.get(metric, 0.0) or 0.0)
                                for metric in ("precision_at_5", "precision_at_10", "ndcg_at_5", "ndcg_at_10", "map", "mrr")
                            },
                            **{
                                f"evidence_unit_{metric}": float(evidence.get(metric, 0.0) or 0.0)
                                for metric in ("precision_at_5", "precision_at_10", "ndcg_at_5", "ndcg_at_10", "map", "mrr")
                            },
                        }
                    ]
                    for row in comparison_rows:
                        for metric in ("precision_at_5", "precision_at_10", "ndcg_at_5", "ndcg_at_10", "map", "mrr"):
                            row[f"delta_{metric}"] = row[f"evidence_unit_{metric}"] - row[f"document_{metric}"]
            delta_path = output_dir / "evidence_unit_calibrated_delta_vs_document.csv"
            write_csv(delta_path, comparison_rows)
            manifest["delta_vs_document"] = str(delta_path)

    manifest_path = output_dir / "evidence_unit_calibration_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
