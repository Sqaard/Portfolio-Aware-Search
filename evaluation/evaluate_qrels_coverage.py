"""Measure judged-document coverage for qrels against a ranked run."""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path
from typing import Optional, Union

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluation.evaluate_ir_metrics import load_qrels, load_run


COVERAGE_FIELDS = [
    "query_id",
    "method",
    "judged_at_5",
    "unjudged_at_5",
    "judged_rate_at_5",
    "judged_at_10",
    "unjudged_at_10",
    "judged_rate_at_10",
    "unjudged_doc_ids_at_10",
]


def _coverage_counts(ranked_docs: list[dict[str, object]], judged_doc_ids: set[str], k: int) -> tuple[int, int, float, list[str]]:
    top_docs = [str(row["doc_id"]) for row in ranked_docs[:k]]
    judged = sum(1 for doc_id in top_docs if doc_id in judged_doc_ids)
    unjudged_ids = [doc_id for doc_id in top_docs if doc_id not in judged_doc_ids]
    denominator = len(top_docs)
    rate = judged / denominator if denominator > 0 else 0.0
    return judged, len(unjudged_ids), rate, unjudged_ids


def evaluate_qrels_coverage(
    qrels: dict[str, dict[str, int]],
    runs: dict[tuple[str, str], list[dict[str, object]]],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for (method, query_id), ranked_docs in sorted(runs.items()):
        judged_doc_ids = set(qrels.get(query_id, {}))
        judged_5, unjudged_5, rate_5, _ = _coverage_counts(ranked_docs, judged_doc_ids, 5)
        judged_10, unjudged_10, rate_10, unjudged_ids = _coverage_counts(ranked_docs, judged_doc_ids, 10)
        rows.append(
            {
                "query_id": query_id,
                "method": method,
                "judged_at_5": judged_5,
                "unjudged_at_5": unjudged_5,
                "judged_rate_at_5": rate_5,
                "judged_at_10": judged_10,
                "unjudged_at_10": unjudged_10,
                "judged_rate_at_10": rate_10,
                "unjudged_doc_ids_at_10": "|".join(unjudged_ids),
            }
        )
    return rows


def summarize_coverage(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["method"])].append(row)
    summaries: list[dict[str, object]] = []
    for method, method_rows in sorted(grouped.items()):
        count = len(method_rows)
        summaries.append(
            {
                "method": method,
                "query_count": count,
                "mean_judged_rate_at_5": sum(float(row["judged_rate_at_5"]) for row in method_rows) / count if count else 0.0,
                "mean_judged_rate_at_10": sum(float(row["judged_rate_at_10"]) for row in method_rows) / count if count else 0.0,
                "queries_below_80pct_at_10": sum(1 for row in method_rows if float(row["judged_rate_at_10"]) < 0.8),
                "queries_below_100pct_at_10": sum(1 for row in method_rows if float(row["judged_rate_at_10"]) < 1.0),
            }
        )
    return summaries


def write_csv(path: Union[str, Path], rows: list[dict[str, object]], fieldnames: list[str]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Measure qrels judged-document coverage for a ranked run.")
    parser.add_argument("--qrels", required=True)
    parser.add_argument("--run", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--summary-output", default="")
    args = parser.parse_args(argv)

    rows = evaluate_qrels_coverage(load_qrels(args.qrels), load_run(args.run))
    write_csv(args.output, rows, COVERAGE_FIELDS)
    summaries = summarize_coverage(rows)
    if args.summary_output:
        write_csv(
            args.summary_output,
            summaries,
            [
                "method",
                "query_count",
                "mean_judged_rate_at_5",
                "mean_judged_rate_at_10",
                "queries_below_80pct_at_10",
                "queries_below_100pct_at_10",
            ],
        )
    for row in summaries:
        print(row)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
