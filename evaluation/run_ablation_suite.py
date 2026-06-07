"""Run configured retrieval ablations and evaluate them with qrels."""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import Optional, Union

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluation.evaluate_ir_metrics import evaluate, load_qrels, load_run, summarize_by_method, write_metrics
from evaluation.evaluate_retrieval_diagnostics import (
    evaluate_diagnostics,
    summarize_diagnostics,
    write_csv as write_diagnostics_csv,
)
from finportfolio_ir.io_utils import load_yaml, write_jsonl
from retrieval.retrieve_for_portfolio import retrieval_records


def resolve_project_path(value: str, base_dir: Path = ROOT) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return base_dir / path


def load_query_requests(path: Union[str, Path]) -> list[dict[str, str]]:
    requests: list[dict[str, str]] = []
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"query_id", "portfolio", "decision_datetime"}
        missing = required.difference(reader.fieldnames or [])
        if missing:
            raise ValueError(f"Query CSV missing columns: {sorted(missing)}")
        for row in reader:
            requests.append(
                {
                    "query_id": row["query_id"],
                    "portfolio": row["portfolio"],
                    "decision_datetime": row["decision_datetime"],
                }
            )
    if not requests:
        raise ValueError(f"Query CSV has no requests: {path}")
    return requests


def single_query_request(portfolio: Optional[str], decision_datetime: Optional[str], query_id: Optional[str]) -> list[dict[str, str]]:
    if not portfolio or not decision_datetime:
        raise ValueError("Either --queries or both --portfolio and --decision-datetime are required.")
    return [
        {
            "query_id": query_id or "",
            "portfolio": portfolio,
            "decision_datetime": decision_datetime,
        }
    ]


def configured_methods(config_path: Union[str, Path]) -> list[str]:
    config = load_yaml(config_path)
    methods = config.get("ranking_methods", {}) or {}
    if not isinstance(methods, dict) or not methods:
        return ["full_hybrid"]
    return list(methods.keys())


def append_run_rows(path: Path, records: list[dict[str, object]], write_header: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["query_id", "doc_id", "rank", "score", "method"])
        if write_header:
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


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run retrieval ablations and optional IR evaluation.")
    parser.add_argument("--documents", required=True)
    parser.add_argument("--portfolio", default="")
    parser.add_argument("--queries", default="", help="CSV with query_id,portfolio,decision_datetime columns.")
    parser.add_argument("--metadata", default="data/processed_documents/ticker_metadata.csv")
    parser.add_argument("--config", default="configs/default.yaml")
    parser.add_argument("--decision-datetime", default="")
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--qrels", default="")
    parser.add_argument("--query-id", default=None)
    args = parser.parse_args(argv)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    run_csv = output_dir / "ablation_run.csv"
    if run_csv.exists():
        run_csv.unlink()

    all_records: list[dict[str, object]] = []
    write_header = True
    query_requests = (
        load_query_requests(args.queries)
        if args.queries
        else single_query_request(args.portfolio, args.decision_datetime, args.query_id)
    )

    for method in configured_methods(args.config):
        method_records: list[dict[str, object]] = []
        for request in query_requests:
            records = retrieval_records(
                documents_path=args.documents,
                portfolio_path=resolve_project_path(request["portfolio"]),
                metadata_path=args.metadata,
                decision_datetime_text=request["decision_datetime"],
                config_path=args.config,
                top_k=args.top_k,
                query_id=request["query_id"] or None,
                method=method,
            )
            method_records.extend(records)
        records = method_records
        write_jsonl(output_dir / f"retrieved_{method}.jsonl", records)
        append_run_rows(run_csv, records, write_header=write_header)
        write_header = False
        all_records.extend(records)

    write_jsonl(output_dir / "ablation_retrieved_all.jsonl", all_records)
    diagnostics = evaluate_diagnostics(all_records, k=args.top_k)
    write_diagnostics_csv(output_dir / "ablation_diagnostics.csv", diagnostics)
    write_diagnostics_csv(output_dir / "ablation_diagnostics_by_method.csv", summarize_diagnostics(diagnostics))

    if args.qrels:
        metrics = evaluate(load_qrels(args.qrels), load_run(run_csv))
        write_metrics(output_dir / "ablation_metrics.csv", metrics)
        write_metrics(output_dir / "ablation_metrics_by_method.csv", summarize_by_method(metrics))
        for row in metrics:
            print(row)

    print(
        f"Wrote ablation outputs for {len(configured_methods(args.config))} methods "
        f"and {len(query_requests)} queries to {output_dir}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
