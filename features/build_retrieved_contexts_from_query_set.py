"""Build FinGPT-ready retrieved contexts from a decision-date query set."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from features.build_fingpt_handoff_package import build_handoff_package
from finportfolio_ir.io_utils import write_jsonl
from retrieval.retrieve_for_portfolio import retrieval_records


def _read_query_set(path: str | Path) -> list[dict[str, str]]:
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    required = {"query_id", "portfolio", "decision_datetime"}
    if not rows:
        raise ValueError("Query set is empty.")
    missing = required.difference(rows[0])
    if missing:
        raise ValueError(f"Query set missing columns: {sorted(missing)}")
    return rows


def build_retrieval_records_from_query_set(
    *,
    documents: str | Path,
    queries: str | Path,
    metadata: str | Path,
    config: str | Path,
    method: str,
    top_k: int,
) -> list[dict[str, object]]:
    output: list[dict[str, object]] = []
    for row in _read_query_set(queries):
        records = retrieval_records(
            documents_path=documents,
            portfolio_path=row["portfolio"],
            metadata_path=metadata,
            decision_datetime_text=row["decision_datetime"],
            config_path=config,
            top_k=top_k,
            query_id=row["query_id"],
            method=method,
        )
        split = row.get("split") or ("train" if row["decision_datetime"] < "2021-10-01" else "test")
        for record in records:
            record["split"] = split
            record["regime"] = row.get("regime", "")
            record["query_notes"] = row.get("notes", "")
        output.extend(records)
    return output


def _summarize(records: list[dict[str, object]]) -> dict[str, object]:
    split_counts: dict[str, int] = {}
    query_counts: dict[str, int] = {}
    ticker_counts: dict[str, int] = {}
    unique_docs = set()
    leakage_rows = 0
    for record in records:
        split = str(record.get("split", ""))
        split_counts[split] = split_counts.get(split, 0) + 1
        query_id = str(record.get("query_id", ""))
        query_counts[query_id] = query_counts.get(query_id, 0) + 1
        unique_docs.add(str(record.get("doc_id", "")))
        if str(record.get("available_at", "")) > str(record.get("retrieval_cutoff", "")):
            leakage_rows += 1
        for ticker in record.get("matched_tickers", []) or []:
            ticker_text = str(ticker).upper()
            ticker_counts[ticker_text] = ticker_counts.get(ticker_text, 0) + 1
    return {
        "rows": len(records),
        "unique_docs": len(unique_docs),
        "query_count": len(query_counts),
        "split_counts": split_counts,
        "ticker_counts": ticker_counts,
        "leakage_rows": leakage_rows,
        "min_rows_per_query": min(query_counts.values()) if query_counts else 0,
        "max_rows_per_query": max(query_counts.values()) if query_counts else 0,
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Build FinIR retrieval rows and FinGPT handoff contexts for a query set.")
    parser.add_argument("--documents", required=True)
    parser.add_argument("--queries", required=True)
    parser.add_argument("--metadata", default="data/processed_documents/ticker_metadata.csv")
    parser.add_argument("--config", default="configs/default.yaml")
    parser.add_argument("--method", default="full_hybrid_diversified")
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--retrieval-output", required=True)
    parser.add_argument("--handoff-dir", required=True)
    args = parser.parse_args(argv)

    records = build_retrieval_records_from_query_set(
        documents=args.documents,
        queries=args.queries,
        metadata=args.metadata,
        config=args.config,
        method=args.method,
        top_k=args.top_k,
    )
    write_jsonl(args.retrieval_output, records)
    manifest = build_handoff_package(records, args.handoff_dir, args.retrieval_output)
    summary = _summarize(records)
    summary["handoff_status"] = manifest["status"]
    summary["retrieval_output"] = args.retrieval_output
    summary["handoff_dir"] = args.handoff_dir
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0 if summary["leakage_rows"] == 0 and manifest["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
