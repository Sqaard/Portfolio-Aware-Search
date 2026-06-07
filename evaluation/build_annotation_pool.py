"""Create an annotation pool CSV from retrieval output.

The intended input is either a single retrieval JSONL or the combined
`ablation_retrieved_all.jsonl` produced by `run_ablation_suite.py`. Rows are
deduplicated by `(query_id, doc_id)` so the same document retrieved by multiple
methods is reviewed once.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Optional, Union

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluation.evaluate_ir_metrics import load_qrels
from finportfolio_ir.io_utils import read_jsonl


POOL_FIELDS = [
    "query_id",
    "portfolio_id",
    "decision_time",
    "doc_id",
    "title",
    "published_at",
    "available_at",
    "matched_tickers",
    "best_rank",
    "methods",
    "ranks_by_method",
    "scores_by_method",
    "max_final_score",
    "review_priority",
    "body_excerpt",
    "url",
    "existing_relevance",
    "relevance",
    "label_source",
    "annotator",
    "notes",
]


def _join_tickers(value: object) -> str:
    if isinstance(value, list):
        return "|".join(str(item) for item in value)
    return str(value or "")


def _compact_json(value: dict[str, object]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def build_pool_records(
    retrieval_records: list[dict[str, object]],
    qrels: Optional[dict[str, dict[str, int]]] = None,
) -> list[dict[str, object]]:
    qrels = qrels or {}
    grouped: dict[tuple[str, str], dict[str, object]] = {}

    for record in retrieval_records:
        query_id = str(record.get("query_id", ""))
        doc_id = str(record.get("doc_id", ""))
        if not query_id or not doc_id:
            continue
        key = (query_id, doc_id)
        method = str(record.get("method", "run") or "run")
        rank = int(record.get("rank", 0) or 0)
        score = float(record.get("final_score", 0.0) or 0.0)

        if key not in grouped:
            grouped[key] = {
                "query_id": query_id,
                "portfolio_id": record.get("portfolio_id", ""),
                "decision_time": record.get("decision_time", ""),
                "doc_id": doc_id,
                "title": record.get("title", ""),
                "published_at": record.get("published_at", ""),
                "available_at": record.get("available_at", ""),
                "matched_tickers": _join_tickers(record.get("matched_tickers", [])),
                "best_rank": rank,
                "methods": set(),
                "ranks_by_method": {},
                "scores_by_method": {},
                "max_final_score": score,
                "review_priority": "",
                "body_excerpt": record.get("body_excerpt", ""),
                "url": record.get("url", ""),
                "existing_relevance": qrels.get(query_id, {}).get(doc_id, ""),
                "relevance": "",
                "label_source": "",
                "annotator": "",
                "notes": "",
            }

        item = grouped[key]
        item["methods"].add(method)
        item["ranks_by_method"][method] = rank
        item["scores_by_method"][method] = round(score, 6)
        item["best_rank"] = min(int(item["best_rank"]), rank) if rank else item["best_rank"]
        item["max_final_score"] = max(float(item["max_final_score"]), score)

    rows: list[dict[str, object]] = []
    for item in grouped.values():
        row = dict(item)
        row["methods"] = "|".join(sorted(row["methods"]))
        row["ranks_by_method"] = _compact_json(row["ranks_by_method"])
        row["scores_by_method"] = _compact_json(row["scores_by_method"])
        row["max_final_score"] = round(float(row["max_final_score"]), 6)
        has_label = row["existing_relevance"] != ""
        best_rank = int(row["best_rank"])
        if has_label:
            row["review_priority"] = "already_labeled"
        elif best_rank <= 5:
            row["review_priority"] = "high_missing_label"
        elif best_rank <= 10:
            row["review_priority"] = "missing_label"
        else:
            row["review_priority"] = "low"
        rows.append(row)

    priority_order = {
        "high_missing_label": 0,
        "missing_label": 1,
        "already_labeled": 2,
        "low": 3,
    }
    rows.sort(
        key=lambda row: (
            str(row["query_id"]),
            priority_order.get(str(row["review_priority"]), 9),
            int(row["best_rank"]),
            str(row["doc_id"]),
        )
    )
    return rows


def write_pool(path: Union[str, Path], rows: list[dict[str, object]]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=POOL_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Build a deduplicated human relevance annotation pool.")
    parser.add_argument("--input", required=True, help="Retrieval JSONL.")
    parser.add_argument("--output", required=True, help="Annotation CSV.")
    parser.add_argument("--qrels", default="", help="Optional qrels CSV to prefill existing_relevance.")
    args = parser.parse_args(argv)

    qrels = load_qrels(args.qrels) if args.qrels else None
    rows = build_pool_records(read_jsonl(args.input), qrels=qrels)
    write_pool(args.output, rows)
    print(f"Wrote {len(rows)} annotation rows to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
