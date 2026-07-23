"""Apply human evidence-unit spot-check labels to the full review queue.

The output is a mixed qrels file: human spot-check labels override assistant
labels for reviewed rows; the remaining rows keep assistant-reviewed labels with
explicit provenance. This lets us measure the reranker without pretending the
whole queue was human-labeled.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Any, Optional, Union


VALID_RELEVANCE = {"0", "1", "2", "3"}
QREL_FIELDS = ["query_id", "doc_id", "relevance", "label_source", "annotator", "notes"]
ISSUE_FIELDS = ["spotcheck_id", "line", "query_id", "doc_id", "issue_type", "message"]
MERGED_EXTRA_FIELDS = ["human_relevance", "final_relevance", "final_label_source", "final_annotator", "final_notes"]


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


def _clean(value: object) -> str:
    return str(value or "").strip()


def _key(row: dict[str, str]) -> tuple[str, str]:
    return _clean(row.get("query_id")), _clean(row.get("doc_id"))


def _notes(row: dict[str, str], *, source_note: str) -> str:
    parts = [source_note]
    for key, prefix in [
        ("review_id", "review_id"),
        ("spotcheck_id", "spotcheck_id"),
        ("reason", "review_reason"),
        ("existing_relevance", "previous_relevance"),
        ("suggested_human_relevance", "assistant_relevance"),
        ("calibration_tags", "calibration_tags"),
        ("human_notes", "notes"),
    ]:
        value = _clean(row.get(key))
        if value:
            parts.append(f"{prefix}:{value}")
    return " | ".join(parts)


def _spotcheck_lookup(rows: list[dict[str, str]]) -> tuple[dict[tuple[str, str], dict[str, str]], list[dict[str, str]]]:
    lookup: dict[tuple[str, str], dict[str, str]] = {}
    issues: list[dict[str, str]] = []
    for index, row in enumerate(rows, start=2):
        query_id, doc_id = _key(row)
        spotcheck_id = _clean(row.get("spotcheck_id"))
        relevance = _clean(row.get("human_relevance"))
        issue_base = {"spotcheck_id": spotcheck_id, "line": str(index), "query_id": query_id, "doc_id": doc_id}
        if not query_id or not doc_id:
            issues.append({**issue_base, "issue_type": "missing_key", "message": "query_id and doc_id are required."})
            continue
        if not relevance:
            issues.append(
                {
                    **issue_base,
                    "issue_type": "missing_human_relevance",
                    "message": "Fill human_relevance with 0, 1, 2, or 3 to override assistant labels.",
                }
            )
            continue
        if relevance not in VALID_RELEVANCE:
            issues.append(
                {
                    **issue_base,
                    "issue_type": "invalid_human_relevance",
                    "message": f"Expected human_relevance 0, 1, 2, or 3; got {relevance!r}.",
                }
            )
            continue
        key = (query_id, doc_id)
        if key in lookup:
            issues.append({**issue_base, "issue_type": "duplicate_spotcheck_row", "message": "Duplicate query_id/doc_id."})
            continue
        lookup[key] = row
    return lookup, issues


def apply_spotcheck_labels(
    review_rows: list[dict[str, str]],
    spotcheck_rows: list[dict[str, str]],
    *,
    human_label_source: str = "human_evidence_unit_spotcheck_v1",
    assistant_label_source: str = "assistant_evidence_unit_review_v1",
    human_annotator: str = "user_chat",
    assistant_annotator: str = "codex_assistant",
    allow_missing_human: bool = False,
) -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, str]]]:
    spot_by_key, issues = _spotcheck_lookup(spotcheck_rows)
    if allow_missing_human:
        issues = [issue for issue in issues if issue["issue_type"] != "missing_human_relevance"]

    merged: list[dict[str, str]] = []
    qrels: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for index, row in enumerate(review_rows, start=2):
        query_id, doc_id = _key(row)
        issue_base = {"spotcheck_id": "", "line": str(index), "query_id": query_id, "doc_id": doc_id}
        if not query_id or not doc_id:
            issues.append({**issue_base, "issue_type": "missing_review_key", "message": "query_id and doc_id are required."})
            continue
        key = (query_id, doc_id)
        if key in seen:
            issues.append({**issue_base, "issue_type": "duplicate_review_row", "message": "Duplicate query_id/doc_id."})
            continue
        seen.add(key)

        spot = spot_by_key.get(key)
        output_row = dict(row)
        if spot is not None:
            relevance = _clean(spot.get("human_relevance"))
            label_source = human_label_source
            annotator = human_annotator
            final_notes = _notes({**row, **spot}, source_note="source:human_spotcheck")
            output_row["human_relevance"] = relevance
        else:
            relevance = _clean(row.get("suggested_human_relevance"))
            label_source = assistant_label_source
            annotator = assistant_annotator
            final_notes = _notes(row, source_note="source:assistant_fallback")

        if relevance not in VALID_RELEVANCE:
            issues.append(
                {
                    **issue_base,
                    "issue_type": "missing_or_invalid_final_relevance",
                    "message": "Final relevance must be 0, 1, 2, or 3.",
                }
            )
            continue

        output_row.update(
            {
                "final_relevance": relevance,
                "final_label_source": label_source,
                "final_annotator": annotator,
                "final_notes": final_notes,
            }
        )
        merged.append(output_row)
        qrels.append(
            {
                "query_id": query_id,
                "doc_id": doc_id,
                "relevance": relevance,
                "label_source": label_source,
                "annotator": annotator,
                "notes": final_notes,
            }
        )

    return merged, qrels, issues


def _fieldnames_for_merged(review_rows: list[dict[str, str]]) -> list[str]:
    fields: list[str] = []
    for row in review_rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    for key in MERGED_EXTRA_FIELDS:
        if key not in fields:
            fields.append(key)
    return fields


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Apply human evidence-unit spot-check labels to review queue.")
    parser.add_argument("--review-queue", required=True)
    parser.add_argument("--spotcheck", required=True)
    parser.add_argument("--output-queue", required=True)
    parser.add_argument("--qrels-output", required=True)
    parser.add_argument("--issues-output", default="")
    parser.add_argument("--human-label-source", default="human_evidence_unit_spotcheck_v1")
    parser.add_argument("--assistant-label-source", default="assistant_evidence_unit_review_v1")
    parser.add_argument("--human-annotator", default="user_chat")
    parser.add_argument("--assistant-annotator", default="codex_assistant")
    parser.add_argument("--allow-missing-human", action="store_true")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args(argv)

    review_rows = read_csv(args.review_queue)
    spotcheck_rows = read_csv(args.spotcheck)
    merged, qrels, issues = apply_spotcheck_labels(
        review_rows,
        spotcheck_rows,
        human_label_source=args.human_label_source,
        assistant_label_source=args.assistant_label_source,
        human_annotator=args.human_annotator,
        assistant_annotator=args.assistant_annotator,
        allow_missing_human=args.allow_missing_human,
    )
    write_csv(args.output_queue, merged, _fieldnames_for_merged(review_rows))
    write_csv(args.qrels_output, qrels, QREL_FIELDS)
    if args.issues_output:
        write_csv(args.issues_output, issues, ISSUE_FIELDS)
    print(f"merged_rows={len(merged)} qrels={len(qrels)} issues={len(issues)}")
    if args.strict and issues:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
