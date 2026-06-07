"""Export qrels from a reviewed annotation pool."""

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


def export_qrels_rows(
    pool_rows: list[dict[str, str]],
    fallback_existing: bool = False,
    default_label_source: str = "",
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    qrels: list[dict[str, str]] = []
    issues: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for index, row in enumerate(pool_rows, start=2):
        query_id = row.get("query_id", "").strip()
        doc_id = row.get("doc_id", "").strip()
        relevance = row.get("relevance", "").strip()
        if not relevance and fallback_existing:
            relevance = row.get("existing_relevance", "").strip()

        if not query_id or not doc_id:
            issues.append(
                {
                    "line": str(index),
                    "query_id": query_id,
                    "doc_id": doc_id,
                    "issue_type": "missing_key",
                    "message": "query_id and doc_id are required.",
                }
            )
            continue
        if not relevance:
            issues.append(
                {
                    "line": str(index),
                    "query_id": query_id,
                    "doc_id": doc_id,
                    "issue_type": "missing_relevance",
                    "message": "No relevance label found.",
                }
            )
            continue
        if relevance not in VALID_RELEVANCE:
            issues.append(
                {
                    "line": str(index),
                    "query_id": query_id,
                    "doc_id": doc_id,
                    "issue_type": "invalid_relevance",
                    "message": f"Expected relevance 0, 1, 2, or 3; got {relevance!r}.",
                }
            )
            continue

        key = (query_id, doc_id)
        if key in seen:
            issues.append(
                {
                    "line": str(index),
                    "query_id": query_id,
                    "doc_id": doc_id,
                    "issue_type": "duplicate_pool_row",
                    "message": "Duplicate query_id/doc_id in annotation pool.",
                }
            )
            continue
        seen.add(key)

        qrels.append(
            {
                "query_id": query_id,
                "doc_id": doc_id,
                "relevance": relevance,
                "label_source": row.get("label_source", "").strip() or default_label_source,
                "annotator": row.get("annotator", "").strip(),
                "notes": row.get("notes", "").strip(),
            }
        )

    return qrels, issues


def read_pool(path: Union[str, Path]) -> list[dict[str, str]]:
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
    parser = argparse.ArgumentParser(description="Export qrels from an annotation pool CSV.")
    parser.add_argument("--input", required=True, help="Annotation pool CSV.")
    parser.add_argument("--output", required=True, help="Output qrels CSV.")
    parser.add_argument("--issues-output", default="", help="Optional export issues CSV.")
    parser.add_argument("--fallback-existing", action="store_true", help="Use existing_relevance when relevance is blank.")
    parser.add_argument("--label-source", default="", help="Default label_source for exported rows.")
    parser.add_argument("--strict", action="store_true", help="Exit 1 if export issues are found.")
    args = parser.parse_args(argv)

    qrels, issues = export_qrels_rows(
        read_pool(args.input),
        fallback_existing=args.fallback_existing,
        default_label_source=args.label_source,
    )
    write_csv(args.output, qrels, ["query_id", "doc_id", "relevance", "label_source", "annotator", "notes"])
    if args.issues_output:
        write_csv(args.issues_output, issues, ["line", "query_id", "doc_id", "issue_type", "message"])

    print(f"exported_qrels={len(qrels)} issues={len(issues)}")
    if args.strict and issues:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
