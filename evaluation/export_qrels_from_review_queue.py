"""Export qrels from a prioritized search-quality review queue."""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import Optional, Union

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


VALID_RELEVANCE = {"0", "1", "2", "3"}
QREL_FIELDS = ["query_id", "doc_id", "relevance", "label_source", "annotator", "notes"]
ISSUE_FIELDS = ["review_id", "line", "query_id", "doc_id", "issue_type", "message"]


def _clean(value: object) -> str:
    return str(value or "").strip()


def _review_notes(row: dict[str, str]) -> str:
    parts = []
    review_id = _clean(row.get("review_id"))
    reason = _clean(row.get("reason"))
    notes = _clean(row.get("reviewer_notes"))
    if review_id:
        parts.append(f"review_id:{review_id}")
    if reason:
        parts.append(f"review_reason:{reason}")
    if notes:
        parts.append(notes)
    return " | ".join(parts)


def export_review_queue_qrels_rows(
    review_rows: list[dict[str, str]],
    *,
    fallback_bootstrap: bool = False,
    default_label_source: str = "",
    annotator: str = "",
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    qrels: list[dict[str, str]] = []
    issues: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for index, row in enumerate(review_rows, start=2):
        review_id = _clean(row.get("review_id"))
        query_id = _clean(row.get("query_id"))
        doc_id = _clean(row.get("doc_id"))
        relevance = _clean(row.get("human_relevance"))
        if not relevance and fallback_bootstrap:
            relevance = _clean(row.get("bootstrap_relevance"))

        issue_base = {"review_id": review_id, "line": str(index), "query_id": query_id, "doc_id": doc_id}

        if not query_id or not doc_id:
            issues.append(
                {
                    **issue_base,
                    "issue_type": "missing_key",
                    "message": "query_id and doc_id are required.",
                }
            )
            continue
        if not relevance:
            issues.append(
                {
                    **issue_base,
                    "issue_type": "missing_human_relevance",
                    "message": "Fill human_relevance with 0, 1, 2, or 3 before exporting final qrels.",
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
            issues.append(
                {
                    **issue_base,
                    "issue_type": "duplicate_review_row",
                    "message": "Duplicate query_id/doc_id in review queue.",
                }
            )
            continue
        seen.add(key)

        qrels.append(
            {
                "query_id": query_id,
                "doc_id": doc_id,
                "relevance": relevance,
                "label_source": _clean(row.get("label_source")) or default_label_source,
                "annotator": _clean(row.get("annotator")) or annotator,
                "notes": _review_notes(row),
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
    parser = argparse.ArgumentParser(description="Export qrels from a search-quality review queue CSV.")
    parser.add_argument("--input", required=True, help="Review queue CSV.")
    parser.add_argument("--output", required=True, help="Output qrels CSV.")
    parser.add_argument("--issues-output", default="", help="Optional export issues CSV.")
    parser.add_argument("--fallback-bootstrap", action="store_true", help="Use bootstrap_relevance when human_relevance is blank.")
    parser.add_argument("--label-source", default="", help="Default label_source for exported rows.")
    parser.add_argument("--annotator", default="", help="Default annotator for exported rows.")
    parser.add_argument("--strict", action="store_true", help="Exit 1 if export issues are found.")
    args = parser.parse_args(argv)

    qrels, issues = export_review_queue_qrels_rows(
        read_csv(args.input),
        fallback_bootstrap=args.fallback_bootstrap,
        default_label_source=args.label_source,
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
