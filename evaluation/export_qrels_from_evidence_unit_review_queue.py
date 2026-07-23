"""Export qrels from an evidence-unit held-out review queue.

This exporter reads `suggested_human_relevance` by default because the queue is
designed for assistant-suggested review before a real human fills final labels.
Keep the label source explicit when reporting metrics.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Optional, Union


VALID_RELEVANCE = {"0", "1", "2", "3"}
QREL_FIELDS = ["query_id", "doc_id", "relevance", "label_source", "annotator", "notes"]
ISSUE_FIELDS = ["review_id", "line", "query_id", "doc_id", "issue_type", "message"]


def _clean(value: object) -> str:
    return str(value or "").strip()


def _notes(row: dict[str, str]) -> str:
    parts = []
    for key, prefix in [
        ("review_id", "review_id"),
        ("reason", "review_reason"),
        ("existing_relevance", "previous_relevance"),
        ("label_source", "previous_label_source"),
        ("calibration_tags", "calibration_tags"),
        ("human_notes", "notes"),
    ]:
        value = _clean(row.get(key))
        if value:
            parts.append(f"{prefix}:{value}")
    return " | ".join(parts)


def export_qrels_rows(
    rows: list[dict[str, str]],
    *,
    relevance_field: str = "suggested_human_relevance",
    label_source: str = "assistant_evidence_unit_review_v1",
    annotator: str = "codex_assistant",
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    qrels: list[dict[str, str]] = []
    issues: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for index, row in enumerate(rows, start=2):
        query_id = _clean(row.get("query_id"))
        doc_id = _clean(row.get("doc_id"))
        review_id = _clean(row.get("review_id"))
        relevance = _clean(row.get(relevance_field))
        issue_base = {"review_id": review_id, "line": str(index), "query_id": query_id, "doc_id": doc_id}

        if not query_id or not doc_id:
            issues.append({**issue_base, "issue_type": "missing_key", "message": "query_id and doc_id are required."})
            continue
        if not relevance:
            issues.append(
                {
                    **issue_base,
                    "issue_type": "missing_relevance",
                    "message": f"Fill {relevance_field} with 0, 1, 2, or 3.",
                }
            )
            continue
        if relevance not in VALID_RELEVANCE:
            issues.append(
                {
                    **issue_base,
                    "issue_type": "invalid_relevance",
                    "message": f"Expected relevance 0, 1, 2, or 3; got {relevance!r}.",
                }
            )
            continue
        key = (query_id, doc_id)
        if key in seen:
            issues.append({**issue_base, "issue_type": "duplicate_review_row", "message": "Duplicate query_id/doc_id."})
            continue
        seen.add(key)
        qrels.append(
            {
                "query_id": query_id,
                "doc_id": doc_id,
                "relevance": relevance,
                "label_source": label_source,
                "annotator": annotator,
                "notes": _notes(row),
            }
        )
    return qrels, issues


def read_csv(path: Union[str, Path]) -> list[dict[str, str]]:
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Union[str, Path], rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Export qrels from an evidence-unit review queue.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--issues-output", default="")
    parser.add_argument("--relevance-field", default="suggested_human_relevance")
    parser.add_argument("--label-source", default="assistant_evidence_unit_review_v1")
    parser.add_argument("--annotator", default="codex_assistant")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args(argv)

    qrels, issues = export_qrels_rows(
        read_csv(args.input),
        relevance_field=args.relevance_field,
        label_source=args.label_source,
        annotator=args.annotator,
    )
    write_csv(args.output, qrels, QREL_FIELDS)
    if args.issues_output:
        write_csv(args.issues_output, issues, ISSUE_FIELDS)
    print(f"exported_qrels={len(qrels)} issues={len(issues)}")
    if args.strict and issues:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
