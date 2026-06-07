"""Build a prioritized human-review queue for web-search qrels.

The goal is to spend manual labeling time where it can change metrics or expose
ranking mistakes: top-ranked rows, low-confidence bootstrap labels, weak-query
results, and rows whose rank changed after a reranker update.
"""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path
from typing import Optional, Union


QUEUE_FIELDS = [
    "review_id",
    "priority",
    "reason",
    "query_id",
    "query",
    "intent",
    "expected_ticker",
    "source_scope",
    "rank_baseline",
    "rank_candidate",
    "bootstrap_relevance",
    "doc_id",
    "folder_key",
    "title",
    "source_type",
    "matched_tickers",
    "event_tags",
    "excerpt",
    "document_path",
    "human_relevance",
    "reviewer_notes",
]


def read_csv(path: Union[str, Path]) -> list[dict[str, str]]:
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Union[str, Path], rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def keyed(rows: list[dict[str, str]]) -> dict[tuple[str, str], dict[str, str]]:
    return {
        (str(row.get("query_id", "")), str(row.get("doc_id", ""))): row
        for row in rows
        if row.get("query_id") and row.get("doc_id")
    }


def qrel_lookup(rows: list[dict[str, str]]) -> dict[tuple[str, str], int]:
    return {
        (str(row.get("query_id", "")), str(row.get("doc_id", ""))): int(row.get("relevance", 0) or 0)
        for row in rows
        if row.get("query_id") and row.get("doc_id")
    }


def metric_lookup(rows: list[dict[str, str]]) -> dict[str, float]:
    lookup: dict[str, float] = {}
    for row in rows:
        query_id = str(row.get("query_id", ""))
        if query_id:
            lookup[query_id] = float(row.get("ndcg_at_10", 0.0) or 0.0)
    return lookup


def safe_rank(row: dict[str, str] | None) -> int:
    if not row:
        return 9999
    try:
        return int(row.get("rank", 9999) or 9999)
    except ValueError:
        return 9999


def queue_reason_and_priority(
    *,
    baseline_row: dict[str, str] | None,
    candidate_row: dict[str, str],
    relevance: int,
    candidate_ndcg: float,
) -> tuple[int, str]:
    rank_baseline = safe_rank(baseline_row)
    rank_candidate = safe_rank(candidate_row)
    reasons: list[str] = []
    priority = 0

    if rank_candidate <= 10:
        priority += 45 - rank_candidate
        reasons.append("top_candidate")
    if rank_baseline <= 10:
        priority += 18
        reasons.append("top_baseline")
    if relevance in {1, 2}:
        priority += 30
        reasons.append("borderline_label")
    if rank_candidate <= 10 and relevance <= 1:
        priority += 26
        reasons.append("possible_false_positive")
    if rank_candidate > 10 and relevance >= 2:
        priority += 20
        reasons.append("possible_false_negative")
    if abs(rank_baseline - rank_candidate) >= 10 and min(rank_baseline, rank_candidate) <= 15:
        priority += 14
        reasons.append("rank_changed")
    if candidate_ndcg < 0.7:
        priority += 18
        reasons.append("weak_query")
    if not reasons:
        reasons.append("coverage")
    return priority, "|".join(reasons)


def build_review_queue(
    *,
    baseline_pool: list[dict[str, str]],
    candidate_pool: list[dict[str, str]],
    qrels: list[dict[str, str]],
    candidate_metrics: list[dict[str, str]],
    limit: int,
    min_per_query: int,
    candidate_top_k_per_query: int = 3,
    baseline_top_k_per_query: int = 2,
) -> list[dict[str, str]]:
    baseline_by_key = keyed(baseline_pool)
    rel_by_key = qrel_lookup(qrels)
    ndcg_by_query = metric_lookup(candidate_metrics)
    candidates: list[dict[str, str]] = []

    for candidate in candidate_pool:
        key = (str(candidate.get("query_id", "")), str(candidate.get("doc_id", "")))
        if not key[0] or not key[1]:
            continue
        relevance = rel_by_key.get(key, 0)
        baseline = baseline_by_key.get(key)
        priority, reason = queue_reason_and_priority(
            baseline_row=baseline,
            candidate_row=candidate,
            relevance=relevance,
            candidate_ndcg=ndcg_by_query.get(key[0], 1.0),
        )
        candidates.append(
            {
                "priority": str(priority),
                "reason": reason,
                "query_id": key[0],
                "query": candidate.get("query", ""),
                "intent": candidate.get("intent", ""),
                "expected_ticker": candidate.get("expected_ticker", ""),
                "source_scope": candidate.get("source_scope", ""),
                "rank_baseline": "" if baseline is None else str(safe_rank(baseline)),
                "rank_candidate": str(safe_rank(candidate)),
                "bootstrap_relevance": str(relevance),
                "doc_id": key[1],
                "folder_key": candidate.get("folder_key", ""),
                "title": candidate.get("title", ""),
                "source_type": candidate.get("source_type", ""),
                "matched_tickers": candidate.get("matched_tickers", ""),
                "event_tags": candidate.get("event_tags", ""),
                "excerpt": candidate.get("excerpt", ""),
                "document_path": candidate.get("document_path", ""),
                "human_relevance": "",
                "reviewer_notes": "",
            }
        )

    candidates.sort(
        key=lambda row: (
            -int(row["priority"]),
            row["query_id"],
            int(row["rank_candidate"]),
            row["title"],
        )
    )

    selected: list[dict[str, str]] = []
    selected_keys: set[tuple[str, str]] = set()
    by_query: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in candidates:
        by_query[row["query_id"]].append(row)

    def add_row(row: dict[str, str]) -> None:
        if len(selected) >= limit:
            return
        key = (row["query_id"], row["doc_id"])
        if key in selected_keys:
            return
        selected.append(row)
        selected_keys.add(key)

    for query_id in sorted(by_query):
        query_rows = by_query[query_id]
        for row in sorted(query_rows, key=lambda item: (int(item["rank_candidate"]), -int(item["priority"])))[: max(0, candidate_top_k_per_query)]:
            add_row(row)
        baseline_rows = [row for row in query_rows if row["rank_baseline"] and int(row["rank_baseline"]) < 9999]
        for row in sorted(baseline_rows, key=lambda item: (int(item["rank_baseline"]), -int(item["priority"])))[: max(0, baseline_top_k_per_query)]:
            add_row(row)
        for row in query_rows[: max(0, min_per_query)]:
            add_row(row)

    for row in candidates:
        if len(selected) >= limit:
            break
        add_row(row)

    selected = selected[:limit]
    for index, row in enumerate(selected, start=1):
        row["review_id"] = f"review_{index:04d}"
    return selected


def summarize(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[row["query_id"]].append(row)
    return [
        {
            "query_id": query_id,
            "rows": str(len(query_rows)),
            "mean_priority": f"{sum(int(row['priority']) for row in query_rows) / len(query_rows):.2f}",
            "borderline_or_fp": str(
                sum(
                    1
                    for row in query_rows
                    if "borderline_label" in row["reason"] or "possible_false_positive" in row["reason"]
                )
            ),
        }
        for query_id, query_rows in sorted(grouped.items())
    ]


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Build a prioritized search-quality human review queue.")
    parser.add_argument("--baseline-pool", required=True)
    parser.add_argument("--candidate-pool", required=True)
    parser.add_argument("--qrels", required=True)
    parser.add_argument("--candidate-metrics", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--summary-output", default="")
    parser.add_argument("--limit", type=int, default=150)
    parser.add_argument("--min-per-query", type=int, default=4)
    parser.add_argument(
        "--candidate-top-k-per-query",
        type=int,
        default=3,
        help="Always include this many top candidate-run rows per query before priority sampling.",
    )
    parser.add_argument(
        "--baseline-top-k-per-query",
        type=int,
        default=2,
        help="Always include this many top baseline-run rows per query when they are present in the candidate pool.",
    )
    args = parser.parse_args(argv)

    rows = build_review_queue(
        baseline_pool=read_csv(args.baseline_pool),
        candidate_pool=read_csv(args.candidate_pool),
        qrels=read_csv(args.qrels),
        candidate_metrics=read_csv(args.candidate_metrics),
        limit=max(1, args.limit),
        min_per_query=max(0, args.min_per_query),
        candidate_top_k_per_query=max(0, args.candidate_top_k_per_query),
        baseline_top_k_per_query=max(0, args.baseline_top_k_per_query),
    )
    write_csv(args.output, rows, QUEUE_FIELDS)
    if args.summary_output:
        write_csv(args.summary_output, summarize(rows), ["query_id", "rows", "mean_priority", "borderline_or_fp"])
    print(f"wrote_review_rows={len(rows)} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
