"""Build a compact human spot-check queue for search ranking labels.

This is an active-learning style helper: after a small human review, it picks
the next rows that are most likely to change ranking conclusions. It avoids
random annotation and focuses on weak queries, top-ranked calibrated results,
rank disagreements, borderline labels, and rows similar to already corrected
human judgments.
"""

from __future__ import annotations

import argparse
import csv
import math
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional, Union


OUTPUT_FIELDS = [
    "spotcheck_id",
    "priority",
    "reason",
    "query_id",
    "query",
    "intent",
    "expected_ticker",
    "source_scope",
    "rank_primary",
    "rank_comparison",
    "rank_delta",
    "doc_id",
    "title",
    "source_type",
    "matched_tickers",
    "event_tags",
    "excerpt",
    "document_path",
    "current_relevance",
    "current_label_source",
    "suggested_human_relevance",
    "human_notes",
]

SUMMARY_FIELDS = ["query_id", "selected_rows", "mean_priority", "weak_query_ndcg_at_10"]


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


def _key(row: dict[str, str]) -> tuple[str, str]:
    return str(row.get("query_id", "")), str(row.get("doc_id", ""))


def _safe_rank(value: object) -> int:
    try:
        return int(str(value or "").strip())
    except ValueError:
        return 9999


def _rank_lookup(rows: list[dict[str, str]]) -> dict[tuple[str, str], int]:
    return {_key(row): _safe_rank(row.get("rank")) for row in rows if row.get("query_id") and row.get("doc_id")}


def _metadata_lookup(*tables: list[dict[str, str]]) -> dict[tuple[str, str], dict[str, str]]:
    lookup: dict[tuple[str, str], dict[str, str]] = {}
    for rows in tables:
        for row in rows:
            key = _key(row)
            if key[0] and key[1]:
                lookup.setdefault(key, row)
    return lookup


def _qrels_lookup(rows: list[dict[str, str]]) -> dict[str, dict[str, int]]:
    qrels: dict[str, dict[str, int]] = defaultdict(dict)
    for row in rows:
        relevance = str(row.get("relevance", "")).strip()
        if relevance in {"0", "1", "2", "3"}:
            qrels[row["query_id"]][row["doc_id"]] = int(relevance)
    return qrels


def _label_source_lookup(rows: list[dict[str, str]]) -> dict[tuple[str, str], str]:
    return {_key(row): str(row.get("label_source", "")) for row in rows if row.get("query_id") and row.get("doc_id")}


def _relevance_lookup(rows: list[dict[str, str]]) -> dict[tuple[str, str], int]:
    lookup: dict[tuple[str, str], int] = {}
    for row in rows:
        relevance = str(row.get("relevance", "")).strip()
        if relevance in {"0", "1", "2", "3"}:
            lookup[_key(row)] = int(relevance)
    return lookup


def dcg(relevances: list[int], k: int) -> float:
    return sum((2**rel - 1) / math.log2(index + 2) for index, rel in enumerate(relevances[:k]))


def ndcg_at_10(qrels: dict[str, dict[str, int]], ranked_doc_ids: list[str], query_id: str) -> float:
    query_qrels = qrels.get(query_id, {})
    relevances = [query_qrels.get(doc_id, 0) for doc_id in ranked_doc_ids]
    ideal = sorted(query_qrels.values(), reverse=True)
    ideal_dcg = dcg(ideal, 10)
    return dcg(relevances, 10) / ideal_dcg if ideal_dcg > 0 else 0.0


def primary_query_ndcg(primary_pool: list[dict[str, str]], qrels: dict[str, dict[str, int]]) -> dict[str, float]:
    by_query: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in primary_pool:
        by_query[row["query_id"]].append(row)
    scores: dict[str, float] = {}
    for query_id, rows in by_query.items():
        ranked = sorted(rows, key=lambda row: _safe_rank(row.get("rank")))
        scores[query_id] = ndcg_at_10(qrels, [row["doc_id"] for row in ranked], query_id)
    return scores


def _split_values(value: str) -> set[str]:
    return {item.strip().upper() for item in str(value or "").split("|") if item.strip()}


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _source_mismatch(row: dict[str, str]) -> bool:
    scope = row.get("source_scope", "")
    source_type = row.get("source_type", "").lower()
    folder_key = row.get("folder_key", "")
    if scope == "sec_filings":
        return not (source_type.startswith("sec_") or folder_key == "sec_filings")
    if scope == "macro":
        return not source_type.startswith("official_macro")
    if scope == "company_ir":
        return not (source_type.startswith("company_") or folder_key == "company_ir")
    return False


def _wrong_company(row: dict[str, str]) -> bool:
    expected = str(row.get("expected_ticker", "")).upper()
    matched = _split_values(row.get("matched_tickers", ""))
    return bool(expected and expected != "MARKET" and matched and expected not in matched and "MARKET" not in matched)


def _human_reviewed_key(row: dict[str, str]) -> bool:
    label_source = str(row.get("label_source", "")).lower()
    annotator = str(row.get("annotator", "")).lower()
    return label_source.startswith("human_") or annotator in {"user_chat", "human", "reviewer_1"}


def score_row(
    row: dict[str, str],
    *,
    rank_primary: int,
    rank_comparison: int,
    relevance: Optional[int],
    label_source: str,
    human_queries: set[str],
    weak_query_scores: dict[str, float],
) -> tuple[int, list[str]]:
    priority = 0
    reasons: list[str] = []
    query_id = row.get("query_id", "")

    if rank_primary <= 10:
        priority += 80 - rank_primary
        reasons.append("primary_top10")
    if rank_comparison <= 10:
        priority += 30 - min(rank_comparison, 10)
        reasons.append("comparison_top10")

    if rank_primary < 9999 and rank_comparison < 9999:
        delta = abs(rank_comparison - rank_primary)
        if delta >= 5:
            priority += min(30, delta)
            reasons.append("rank_disagreement")
    elif rank_primary <= 10 or rank_comparison <= 10:
        priority += 15
        reasons.append("new_top_result")

    ndcg = weak_query_scores.get(query_id, 1.0)
    if ndcg < 0.85:
        priority += 45
        reasons.append("weak_query")
    elif ndcg < 0.95:
        priority += 25
        reasons.append("borderline_query")

    if query_id in human_queries:
        priority += 25
        reasons.append("near_human_spotcheck_query")
    if relevance in {1, 2}:
        priority += 18
        reasons.append("borderline_label")
    if label_source and not label_source.startswith("human_"):
        priority += 10
        reasons.append("assistant_label")
    if _wrong_company(row):
        priority += 30
        reasons.append("wrong_company_risk")
    if _source_mismatch(row):
        priority += 18
        reasons.append("source_scope_mismatch")

    return priority, sorted(set(reasons))


def build_spotcheck_queue(
    *,
    review_rows: list[dict[str, str]],
    primary_pool: list[dict[str, str]],
    comparison_pool: list[dict[str, str]],
    qrels_rows: list[dict[str, str]],
    limit: int,
    max_per_query: int,
) -> list[dict[str, Any]]:
    primary_rank_by_key = _rank_lookup(primary_pool)
    comparison_rank_by_key = _rank_lookup(comparison_pool)
    metadata_by_key = _metadata_lookup(review_rows, primary_pool, comparison_pool)
    relevance_by_key = _relevance_lookup(qrels_rows)
    label_source_by_key = _label_source_lookup(qrels_rows)
    qrels = _qrels_lookup(qrels_rows)
    weak_query_scores = primary_query_ndcg(primary_pool, qrels)
    human_queries = {row["query_id"] for row in review_rows if _human_reviewed_key(row)}
    human_keys = {_key(row) for row in review_rows if _human_reviewed_key(row)}

    candidate_keys = set(primary_rank_by_key) | set(comparison_rank_by_key)
    scored: list[dict[str, Any]] = []
    for key in candidate_keys:
        if key in human_keys:
            continue
        row = dict(metadata_by_key.get(key, {}))
        if not row:
            continue
        row.setdefault("query_id", key[0])
        row.setdefault("doc_id", key[1])
        rank_primary = primary_rank_by_key.get(key, 9999)
        rank_comparison = comparison_rank_by_key.get(key, 9999)
        relevance = relevance_by_key.get(key)
        label_source = label_source_by_key.get(key, "")
        priority, reasons = score_row(
            row,
            rank_primary=rank_primary,
            rank_comparison=rank_comparison,
            relevance=relevance,
            label_source=label_source,
            human_queries=human_queries,
            weak_query_scores=weak_query_scores,
        )
        if priority <= 0:
            continue
        row["priority"] = priority
        row["reason"] = "|".join(reasons)
        row["rank_primary"] = "" if rank_primary >= 9999 else str(rank_primary)
        row["rank_comparison"] = "" if rank_comparison >= 9999 else str(rank_comparison)
        row["rank_delta"] = "" if rank_primary >= 9999 or rank_comparison >= 9999 else str(rank_comparison - rank_primary)
        row["current_relevance"] = "" if relevance is None else str(relevance)
        row["current_label_source"] = label_source
        row["suggested_human_relevance"] = ""
        row["human_notes"] = ""
        scored.append(row)

    scored.sort(
        key=lambda row: (
            -int(row.get("priority", 0)),
            row.get("query_id", ""),
            _safe_rank(row.get("rank_primary")),
            row.get("doc_id", ""),
        )
    )

    selected: list[dict[str, Any]] = []
    per_query: dict[str, int] = defaultdict(int)
    for row in scored:
        query_id = row.get("query_id", "")
        if per_query[query_id] >= max_per_query:
            continue
        selected.append(row)
        per_query[query_id] += 1
        if len(selected) >= limit:
            break

    for index, row in enumerate(selected, start=1):
        row["spotcheck_id"] = f"spot_{index:04d}"
        row["excerpt"] = _normalize(row.get("excerpt", ""))
    return selected


def summarize(rows: list[dict[str, Any]], primary_pool: list[dict[str, str]], qrels_rows: list[dict[str, str]]) -> list[dict[str, str]]:
    weak_scores = primary_query_ndcg(primary_pool, _qrels_lookup(qrels_rows))
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["query_id"]].append(row)
    output: list[dict[str, str]] = []
    for query_id, query_rows in sorted(grouped.items()):
        priorities = [int(row.get("priority", 0)) for row in query_rows]
        output.append(
            {
                "query_id": query_id,
                "selected_rows": str(len(query_rows)),
                "mean_priority": f"{(sum(priorities) / len(priorities)):.3f}" if priorities else "0.000",
                "weak_query_ndcg_at_10": f"{weak_scores.get(query_id, 0.0):.6f}",
            }
        )
    return output


def write_prompt_markdown(path: Union[str, Path], rows: list[dict[str, Any]]) -> None:
    lines = [
        "# Search Quality Human Spot-Check Queue",
        "",
        "Use relevance labels: 0 = irrelevant, 1 = weak fallback, 2 = useful, 3 = highly relevant.",
        "Reply format can be compact, for example: `1=0, 2=3, 3=1`.",
        "",
    ]
    for index, row in enumerate(rows, start=1):
        excerpt = _normalize(row.get("excerpt", ""))[:320]
        lines.extend(
            [
                f"## {index}. {row.get('query', '')}",
                "",
                f"- Spotcheck ID: `{row.get('spotcheck_id', '')}`",
                f"- Query ID: `{row.get('query_id', '')}`",
                f"- Rank: primary `{row.get('rank_primary', '') or '-'}`, comparison `{row.get('rank_comparison', '') or '-'}`",
                f"- Source: `{row.get('source_type', '')}`; tickers: `{row.get('matched_tickers', '')}`",
                f"- Title: {row.get('title', '')}",
                f"- Excerpt: {excerpt}",
                "",
            ]
        )
    Path(path).write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Build a compact active-learning human spot-check queue.")
    parser.add_argument("--review-queue", required=True)
    parser.add_argument("--primary-pool", required=True, help="Candidate pool to validate, e.g. calibrated_v7.")
    parser.add_argument("--comparison-pool", required=True, help="Previous pool for rank-disagreement features.")
    parser.add_argument("--qrels", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--summary-output", default="")
    parser.add_argument("--prompt-output", default="")
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--max-per-query", type=int, default=4)
    args = parser.parse_args(argv)

    review_rows = read_csv(args.review_queue)
    primary_pool = read_csv(args.primary_pool)
    comparison_pool = read_csv(args.comparison_pool)
    qrels_rows = read_csv(args.qrels)
    rows = build_spotcheck_queue(
        review_rows=review_rows,
        primary_pool=primary_pool,
        comparison_pool=comparison_pool,
        qrels_rows=qrels_rows,
        limit=max(1, args.limit),
        max_per_query=max(1, args.max_per_query),
    )
    write_csv(args.output, rows, OUTPUT_FIELDS)
    if args.summary_output:
        write_csv(args.summary_output, summarize(rows, primary_pool, qrels_rows), SUMMARY_FIELDS)
    if args.prompt_output:
        write_prompt_markdown(args.prompt_output, rows)
    print(f"wrote_spotcheck_rows={len(rows)} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
