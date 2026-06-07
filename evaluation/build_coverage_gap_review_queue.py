"""Expand a review queue with unjudged top-ranked documents.

Partial qrels can make a better ranking look worse when new top results are
unjudged. This helper appends coverage gaps from baseline and candidate pools
before metrics are used for ranking decisions.
"""

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

from evaluation.build_search_review_queue import QUEUE_FIELDS, qrel_lookup, read_csv, safe_rank, write_csv


SUMMARY_FIELDS = ["query_id", "rows", "coverage_gap_rows", "candidate_top_rows", "baseline_top_rows"]


def _key(row: dict[str, str]) -> tuple[str, str]:
    return str(row.get("query_id", "")), str(row.get("doc_id", ""))


def _rank_lookup(rows: list[dict[str, str]]) -> dict[tuple[str, str], int]:
    return {_key(row): safe_rank(row) for row in rows if row.get("query_id") and row.get("doc_id")}


def _metadata_lookup(*pools: list[dict[str, str]]) -> dict[tuple[str, str], dict[str, str]]:
    lookup: dict[tuple[str, str], dict[str, str]] = {}
    for pool in pools:
        for row in pool:
            key = _key(row)
            if key[0] and key[1]:
                lookup.setdefault(key, row)
    return lookup


def _normalize_existing_row(row: dict[str, str]) -> dict[str, str]:
    return {field: str(row.get(field, "") or "") for field in QUEUE_FIELDS}


def _gap_row(
    *,
    key: tuple[str, str],
    metadata: dict[str, str],
    baseline_rank: int,
    candidate_rank: int,
    bootstrap_relevance: int | None,
    reason: str,
    priority: int,
) -> dict[str, str]:
    return {
        "review_id": "",
        "priority": str(priority),
        "reason": reason,
        "query_id": key[0],
        "query": metadata.get("query", ""),
        "intent": metadata.get("intent", ""),
        "expected_ticker": metadata.get("expected_ticker", ""),
        "source_scope": metadata.get("source_scope", ""),
        "rank_baseline": "" if baseline_rank >= 9999 else str(baseline_rank),
        "rank_candidate": "" if candidate_rank >= 9999 else str(candidate_rank),
        "bootstrap_relevance": "" if bootstrap_relevance is None else str(bootstrap_relevance),
        "doc_id": key[1],
        "folder_key": metadata.get("folder_key", ""),
        "title": metadata.get("title", ""),
        "source_type": metadata.get("source_type", ""),
        "matched_tickers": metadata.get("matched_tickers", ""),
        "event_tags": metadata.get("event_tags", ""),
        "excerpt": metadata.get("excerpt", ""),
        "document_path": metadata.get("document_path", ""),
        "human_relevance": "",
        "reviewer_notes": "",
    }


def _round_robin(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in sorted(
        rows,
        key=lambda item: (
            min(int(item.get("rank_candidate") or 9999), int(item.get("rank_baseline") or 9999)),
            -int(item.get("priority") or 0),
            item.get("doc_id", ""),
        ),
    ):
        grouped[row["query_id"]].append(row)

    ordered: list[dict[str, str]] = []
    query_ids = sorted(grouped)
    while any(grouped.values()):
        for query_id in query_ids:
            if grouped[query_id]:
                ordered.append(grouped[query_id].pop(0))
    return ordered


def build_coverage_gap_queue(
    *,
    existing_queue: list[dict[str, str]],
    baseline_pool: list[dict[str, str]],
    candidate_pool: list[dict[str, str]],
    judged_qrels: list[dict[str, str]],
    bootstrap_qrels: list[dict[str, str]],
    top_k: int,
    limit: int,
) -> list[dict[str, str]]:
    selected: list[dict[str, str]] = []
    selected_keys: set[tuple[str, str]] = set()
    judged_keys = set(qrel_lookup(judged_qrels))
    bootstrap_by_key = qrel_lookup(bootstrap_qrels)
    baseline_rank_by_key = _rank_lookup(baseline_pool)
    candidate_rank_by_key = _rank_lookup(candidate_pool)
    metadata_by_key = _metadata_lookup(candidate_pool, baseline_pool, existing_queue)

    for row in existing_queue:
        key = _key(row)
        if not key[0] or not key[1] or key in selected_keys:
            continue
        selected.append(_normalize_existing_row(row))
        selected_keys.add(key)

    gap_by_key: dict[tuple[str, str], dict[str, str]] = {}

    def add_gap(row: dict[str, str], source: str) -> None:
        key = _key(row)
        if not key[0] or not key[1] or key in selected_keys or key in judged_keys:
            return
        rank = safe_rank(row)
        if rank > top_k:
            return
        baseline_rank = baseline_rank_by_key.get(key, 9999)
        candidate_rank = candidate_rank_by_key.get(key, 9999)
        priority = 130 - rank if source == "candidate" else 115 - rank
        reason = f"coverage_gap_{source}_top{top_k}"
        if key in gap_by_key:
            existing = gap_by_key[key]
            reasons = set(existing["reason"].split("|"))
            reasons.add(reason)
            existing["reason"] = "|".join(sorted(reasons))
            existing["priority"] = str(max(int(existing["priority"]), priority))
            existing["rank_baseline"] = "" if baseline_rank >= 9999 else str(baseline_rank)
            existing["rank_candidate"] = "" if candidate_rank >= 9999 else str(candidate_rank)
            return
        gap_by_key[key] = _gap_row(
            key=key,
            metadata=metadata_by_key.get(key, row),
            baseline_rank=baseline_rank,
            candidate_rank=candidate_rank,
            bootstrap_relevance=bootstrap_by_key.get(key),
            reason=reason,
            priority=priority,
        )

    for row in candidate_pool:
        add_gap(row, "candidate")
    for row in baseline_pool:
        add_gap(row, "baseline")

    max_rows = limit if limit > 0 else len(selected) + len(gap_by_key)
    for row in _round_robin(list(gap_by_key.values())):
        if len(selected) >= max_rows:
            break
        key = _key(row)
        if key in selected_keys:
            continue
        selected.append(row)
        selected_keys.add(key)

    for index, row in enumerate(selected, start=1):
        row["review_id"] = f"review_{index:04d}"
    return selected


def summarize(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[row["query_id"]].append(row)
    summaries: list[dict[str, str]] = []
    for query_id, query_rows in sorted(grouped.items()):
        summaries.append(
            {
                "query_id": query_id,
                "rows": str(len(query_rows)),
                "coverage_gap_rows": str(sum(1 for row in query_rows if "coverage_gap" in row.get("reason", ""))),
                "candidate_top_rows": str(sum(1 for row in query_rows if row.get("rank_candidate") and int(row["rank_candidate"]) <= 10)),
                "baseline_top_rows": str(sum(1 for row in query_rows if row.get("rank_baseline") and int(row["rank_baseline"]) <= 10)),
            }
        )
    return summaries


def write_csv_rows(path: Union[str, Path], rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Append unjudged top-ranked coverage gaps to a search review queue.")
    parser.add_argument("--existing-queue", required=True)
    parser.add_argument("--baseline-pool", required=True)
    parser.add_argument("--candidate-pool", required=True)
    parser.add_argument("--qrels", required=True, help="Current judged qrels used for coverage checks.")
    parser.add_argument("--bootstrap-qrels", default="", help="Optional bootstrap qrels used only to prefill bootstrap_relevance.")
    parser.add_argument("--output", required=True)
    parser.add_argument("--summary-output", default="")
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--limit", type=int, default=300, help="Maximum output rows; use 0 for no limit.")
    args = parser.parse_args(argv)

    rows = build_coverage_gap_queue(
        existing_queue=read_csv(args.existing_queue),
        baseline_pool=read_csv(args.baseline_pool),
        candidate_pool=read_csv(args.candidate_pool),
        judged_qrels=read_csv(args.qrels),
        bootstrap_qrels=read_csv(args.bootstrap_qrels) if args.bootstrap_qrels else [],
        top_k=max(1, args.top_k),
        limit=max(0, args.limit),
    )
    write_csv(args.output, rows, QUEUE_FIELDS)
    if args.summary_output:
        write_csv_rows(args.summary_output, summarize(rows), SUMMARY_FIELDS)
    print(f"wrote_review_rows={len(rows)} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
