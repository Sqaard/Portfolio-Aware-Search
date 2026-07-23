"""Build a manual review queue for document vs evidence-unit retrieval.

The queue is intentionally small and audit-oriented: it focuses human review on
rows where calibrated evidence-unit ranking changes the top results, where
assistant labels are still provisional, and where a borderline judgment can
move NDCG.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional, Union


REVIEW_FIELDS = [
    "review_id",
    "priority",
    "reason",
    "query_id",
    "decision_time",
    "doc_id",
    "evidence_unit_id",
    "parent_doc_id",
    "document_rank",
    "raw_evidence_rank",
    "calibrated_rank",
    "raw_to_calibrated_delta",
    "document_to_calibrated_delta",
    "existing_relevance",
    "label_source",
    "suggested_human_relevance",
    "human_notes",
    "title",
    "source",
    "source_type",
    "evidence_unit_type",
    "evidence_unit_claim_type",
    "published_at",
    "available_at",
    "matched_tickers",
    "calibration_delta",
    "calibration_tags",
    "body_excerpt",
    "url",
]

SUMMARY_FIELDS = [
    "query_id",
    "rows",
    "calibrated_top_rows",
    "promoted_rows",
    "assistant_labeled_rows",
    "borderline_rows",
]


def read_jsonl(path: Union[str, Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


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


def _safe_rank(value: object) -> int:
    try:
        rank = int(str(value or "").strip())
    except ValueError:
        return 9999
    return rank if rank > 0 else 9999


def source_document_id(row: dict[str, Any]) -> str:
    """Return the qrels/source-document grain for an evidence-unit row."""

    evaluated = str(row.get("evaluated_doc_id") or "").strip()
    if evaluated:
        return evaluated
    doc_id = str(row.get("doc_id") or "").strip()
    parent_doc_id = str(row.get("parent_doc_id") or "").strip()
    unit_type = str(row.get("evidence_unit_type") or "").strip()
    if unit_type in {"company_ir_fact_block", "document_block"} and parent_doc_id:
        return parent_doc_id
    return doc_id


def _key(row: dict[str, Any]) -> tuple[str, str]:
    return str(row.get("query_id", "")), source_document_id(row)


def _rank_lookup(rows: list[dict[str, Any]]) -> dict[tuple[str, str], int]:
    lookup: dict[tuple[str, str], int] = {}
    for row in rows:
        key = _key(row)
        if key[0] and key[1]:
            lookup[key] = min(lookup.get(key, 9999), _safe_rank(row.get("rank")))
    return lookup


def _metadata_lookup(*tables: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    lookup: dict[tuple[str, str], dict[str, Any]] = {}
    for rows in tables:
        for row in rows:
            key = _key(row)
            if key[0] and key[1]:
                lookup.setdefault(key, row)
    return lookup


def _qrels_lookup(rows: list[dict[str, str]]) -> dict[tuple[str, str], dict[str, str]]:
    lookup: dict[tuple[str, str], dict[str, str]] = {}
    for row in rows:
        key = (str(row.get("query_id", "")), str(row.get("doc_id", "")))
        if key[0] and key[1]:
            lookup[key] = row
    return lookup


def _stringify(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        return "|".join(str(item) for item in value)
    return str(value)


def _rank_delta(before: int, after: int) -> str:
    if before >= 9999 or after >= 9999:
        return ""
    return str(before - after)


def score_review_row(
    *,
    document_rank: int,
    raw_rank: int,
    calibrated_rank: int,
    relevance: str,
    label_source: str,
) -> tuple[int, list[str]]:
    priority = 0
    reasons: list[str] = []

    if calibrated_rank <= 10:
        priority += 120 - calibrated_rank
        reasons.append("calibrated_top10")
    if calibrated_rank <= 3:
        priority += 25
        reasons.append("calibrated_top3")
    if raw_rank >= 9999 and calibrated_rank <= 10:
        priority += 35
        reasons.append("new_after_calibration")
    elif calibrated_rank < raw_rank:
        priority += min(35, raw_rank - calibrated_rank + 10)
        reasons.append("promoted_by_calibration")
    if document_rank < 9999 and calibrated_rank < 9999 and abs(document_rank - calibrated_rank) >= 3:
        priority += 18
        reasons.append("document_unit_rank_disagreement")
    if relevance in {"1", "2"}:
        priority += 18
        reasons.append("borderline_label")
    if not relevance:
        priority += 20
        reasons.append("missing_label")
    if label_source and not label_source.startswith("human_"):
        priority += 12
        reasons.append("assistant_label")
    return priority, sorted(set(reasons))


def build_review_queue(
    *,
    document_rows: list[dict[str, Any]],
    raw_evidence_rows: list[dict[str, Any]],
    calibrated_rows: list[dict[str, Any]],
    qrels_rows: list[dict[str, str]],
    top_k: int,
    limit: int,
) -> list[dict[str, Any]]:
    document_rank_by_key = _rank_lookup(document_rows)
    raw_rank_by_key = _rank_lookup(raw_evidence_rows)
    calibrated_rank_by_key = _rank_lookup(calibrated_rows)
    metadata_by_key = _metadata_lookup(calibrated_rows, raw_evidence_rows, document_rows)
    qrels_by_key = _qrels_lookup(qrels_rows)

    candidate_keys: set[tuple[str, str]] = set(qrels_by_key)
    for ranks in (document_rank_by_key, raw_rank_by_key, calibrated_rank_by_key):
        candidate_keys.update(key for key, rank in ranks.items() if rank <= top_k)

    rows: list[dict[str, Any]] = []
    for key in candidate_keys:
        metadata = dict(metadata_by_key.get(key, {}))
        if not metadata:
            continue
        document_rank = document_rank_by_key.get(key, 9999)
        raw_rank = raw_rank_by_key.get(key, 9999)
        calibrated_rank = calibrated_rank_by_key.get(key, 9999)
        qrel = qrels_by_key.get(key, {})
        relevance = str(qrel.get("relevance", "") or "")
        label_source = str(qrel.get("label_source", "") or "")
        priority, reasons = score_review_row(
            document_rank=document_rank,
            raw_rank=raw_rank,
            calibrated_rank=calibrated_rank,
            relevance=relevance,
            label_source=label_source,
        )
        rows.append(
            {
                "priority": priority,
                "reason": "|".join(reasons),
                "query_id": key[0],
                "decision_time": metadata.get("decision_time", metadata.get("decision_datetime", "")),
                "doc_id": key[1],
                "evidence_unit_id": metadata.get("evidence_unit_id", metadata.get("doc_id", "")),
                "parent_doc_id": metadata.get("parent_doc_id", ""),
                "document_rank": "" if document_rank >= 9999 else document_rank,
                "raw_evidence_rank": "" if raw_rank >= 9999 else raw_rank,
                "calibrated_rank": "" if calibrated_rank >= 9999 else calibrated_rank,
                "raw_to_calibrated_delta": _rank_delta(raw_rank, calibrated_rank),
                "document_to_calibrated_delta": _rank_delta(document_rank, calibrated_rank),
                "existing_relevance": relevance,
                "label_source": label_source,
                "suggested_human_relevance": "",
                "human_notes": "",
                "title": metadata.get("title", ""),
                "source": metadata.get("source", ""),
                "source_type": metadata.get("source_type", ""),
                "evidence_unit_type": metadata.get("evidence_unit_type", ""),
                "evidence_unit_claim_type": metadata.get("evidence_unit_claim_type", ""),
                "published_at": metadata.get("published_at", ""),
                "available_at": metadata.get("available_at", ""),
                "matched_tickers": _stringify(metadata.get("matched_tickers", "")),
                "calibration_delta": metadata.get("calibration_score_delta", ""),
                "calibration_tags": _stringify(metadata.get("evidence_calibration_tags", "")),
                "body_excerpt": metadata.get("body_excerpt", ""),
                "url": metadata.get("url", ""),
            }
        )

    rows.sort(
        key=lambda row: (
            -int(row["priority"]),
            row["query_id"],
            int(row["calibrated_rank"] or 9999),
            int(row["raw_evidence_rank"] or 9999),
            row["doc_id"],
        )
    )
    if limit > 0:
        rows = rows[:limit]
    for index, row in enumerate(rows, start=1):
        row["review_id"] = f"eu_review_{index:04d}"
    return rows


def summarize(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["query_id"])].append(row)
    summary: list[dict[str, str]] = []
    for query_id, query_rows in sorted(grouped.items()):
        summary.append(
            {
                "query_id": query_id,
                "rows": str(len(query_rows)),
                "calibrated_top_rows": str(sum(1 for row in query_rows if str(row.get("calibrated_rank", "")).isdigit())),
                "promoted_rows": str(sum(1 for row in query_rows if "promoted_by_calibration" in row.get("reason", ""))),
                "assistant_labeled_rows": str(sum(1 for row in query_rows if "assistant_label" in row.get("reason", ""))),
                "borderline_rows": str(sum(1 for row in query_rows if "borderline_label" in row.get("reason", ""))),
            }
        )
    return summary


def write_markdown_prompt(path: Union[str, Path], rows: list[dict[str, Any]]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Evidence-Unit Held-Out Review Queue",
        "",
        "Rubric: 0 = irrelevant, 1 = weak fallback, 2 = useful evidence, 3 = highly relevant and timely.",
        "Please review `suggested_human_relevance` for each item. Prefer the exact section/fact that answers the query intent.",
        "",
    ]
    for row in rows:
        excerpt = " ".join(str(row.get("body_excerpt", "")).split())
        if len(excerpt) > 420:
            excerpt = excerpt[:417].rstrip() + "..."
        lines.extend(
            [
                f"## {row['review_id']} | {row['query_id']}",
                "",
                f"- Current label: `{row.get('existing_relevance', '')}` ({row.get('label_source', '')})",
                f"- Ranks: document `{row.get('document_rank', '')}`, raw unit `{row.get('raw_evidence_rank', '')}`, calibrated `{row.get('calibrated_rank', '')}`",
                f"- Reason: `{row.get('reason', '')}`",
                f"- Doc: `{row.get('doc_id', '')}`",
                f"- Unit: `{row.get('evidence_unit_id', '')}`",
                f"- Title: {row.get('title', '')}",
                f"- Source/type: {row.get('source', '')} / {row.get('source_type', '')} / {row.get('evidence_unit_claim_type', '')}",
                f"- Excerpt: {excerpt}",
                "",
                "Human relevance: ",
                "Notes: ",
                "",
            ]
        )
    output.write_text("\n".join(lines), encoding="utf-8")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Build a manual review queue for evidence-unit ranking validation.")
    parser.add_argument("--document-run", required=True, help="Document-level retrieval JSONL.")
    parser.add_argument("--raw-evidence-run", required=True, help="Raw evidence-unit retrieval JSONL.")
    parser.add_argument("--calibrated-run", required=True, help="Calibrated evidence-unit eval JSONL.")
    parser.add_argument("--qrels", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--summary-output", default="")
    parser.add_argument("--prompt-output", default="")
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--limit", type=int, default=80)
    args = parser.parse_args(argv)

    rows = build_review_queue(
        document_rows=read_jsonl(args.document_run),
        raw_evidence_rows=read_jsonl(args.raw_evidence_run),
        calibrated_rows=read_jsonl(args.calibrated_run),
        qrels_rows=read_csv(args.qrels),
        top_k=max(1, args.top_k),
        limit=max(0, args.limit),
    )
    write_csv(args.output, rows, REVIEW_FIELDS)
    if args.summary_output:
        write_csv(args.summary_output, summarize(rows), SUMMARY_FIELDS)
    if args.prompt_output:
        write_markdown_prompt(args.prompt_output, rows)
    print(json.dumps({"status": "completed", "rows": len(rows), "output": args.output}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
