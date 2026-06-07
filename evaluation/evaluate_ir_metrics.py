"""Evaluate ranked retrieval runs with standard IR metrics."""

from __future__ import annotations

import argparse
import csv
import math
from collections import defaultdict
from pathlib import Path
from typing import Optional, Union


def load_qrels(path: Union[str, Path]) -> dict[str, dict[str, int]]:
    qrels: dict[str, dict[str, int]] = defaultdict(dict)
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            qrels[row["query_id"]][row["doc_id"]] = int(row["relevance"])
    return qrels


def load_run(path: Union[str, Path]) -> dict[tuple[str, str], list[dict[str, object]]]:
    runs: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            method = row.get("method", "run") or "run"
            runs[(method, row["query_id"])].append(
                {
                    "doc_id": row["doc_id"],
                    "rank": int(row["rank"]),
                    "score": float(row["score"]),
                    "method": method,
                }
            )
    for rows in runs.values():
        rows.sort(key=lambda item: int(item["rank"]))
    return runs


def precision_at_k(relevances: list[int], k: int) -> float:
    if k <= 0:
        return 0.0
    return sum(1 for value in relevances[:k] if value > 0) / k


def dcg(relevances: list[int], k: int) -> float:
    return sum((2**rel - 1) / math.log2(index + 2) for index, rel in enumerate(relevances[:k]))


def ndcg_at_k(relevances: list[int], ideal_relevances: list[int], k: int) -> float:
    ideal = dcg(sorted(ideal_relevances, reverse=True), k)
    return dcg(relevances, k) / ideal if ideal > 0 else 0.0


def average_precision(relevances: list[int]) -> float:
    hits = 0
    total = 0.0
    for index, relevance in enumerate(relevances, start=1):
        if relevance > 0:
            hits += 1
            total += hits / index
    return total / hits if hits else 0.0


def reciprocal_rank(relevances: list[int]) -> float:
    for index, relevance in enumerate(relevances, start=1):
        if relevance > 0:
            return 1.0 / index
    return 0.0


def evaluate(
    qrels: dict[str, dict[str, int]],
    runs: dict[tuple[str, str], list[dict[str, object]]],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for (method, query_id), ranked_docs in sorted(runs.items()):
        query_qrels = qrels.get(query_id, {})
        relevances = [query_qrels.get(str(item["doc_id"]), 0) for item in ranked_docs]
        ideal = list(query_qrels.values())
        rows.append(
            {
                "query_id": query_id,
                "method": method,
                "precision_at_5": precision_at_k(relevances, 5),
                "precision_at_10": precision_at_k(relevances, 10),
                "ndcg_at_5": ndcg_at_k(relevances, ideal, 5),
                "ndcg_at_10": ndcg_at_k(relevances, ideal, 10),
                "map": average_precision(relevances),
                "mrr": reciprocal_rank(relevances),
            }
        )
    return rows


def write_metrics(path: Union[str, Path], rows: list[dict[str, object]]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()) if rows else ["query_id"])
        writer.writeheader()
        writer.writerows(rows)


def summarize_by_method(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["method"])].append(row)

    summary: list[dict[str, object]] = []
    metric_names = [
        "precision_at_5",
        "precision_at_10",
        "ndcg_at_5",
        "ndcg_at_10",
        "map",
        "mrr",
    ]
    for method, method_rows in sorted(grouped.items()):
        aggregate: dict[str, object] = {
            "method": method,
            "query_count": len(method_rows),
        }
        for metric in metric_names:
            values = [float(row[metric]) for row in method_rows]
            aggregate[metric] = sum(values) / len(values) if values else 0.0
        summary.append(aggregate)
    return summary


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate IR ranking metrics.")
    parser.add_argument("--qrels", required=True)
    parser.add_argument("--run", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--summary-output", default="")
    args = parser.parse_args(argv)

    rows = evaluate(load_qrels(args.qrels), load_run(args.run))
    write_metrics(args.output, rows)
    if args.summary_output:
        write_metrics(args.summary_output, summarize_by_method(rows))
    for row in rows:
        print(row)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
