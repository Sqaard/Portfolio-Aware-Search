"""Validate qrels labels and optional run coverage."""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import Optional, Union

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluation.evaluate_ir_metrics import load_qrels, load_run


VALID_RELEVANCE = {"0", "1", "2", "3"}


def validate_qrels_file(
    qrels_path: Union[str, Path],
    run_path: Optional[Union[str, Path]] = None,
    top_k: Optional[int] = None,
) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    with Path(qrels_path).open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"query_id", "doc_id", "relevance"}
        missing = required.difference(reader.fieldnames or [])
        if missing:
            return [
                {
                    "issue_type": "missing_column",
                    "query_id": "",
                    "doc_id": "",
                    "message": f"Missing qrels columns: {sorted(missing)}",
                }
            ]
        for line_number, row in enumerate(reader, start=2):
            query_id = row["query_id"].strip()
            doc_id = row["doc_id"].strip()
            relevance = row["relevance"].strip()
            key = (query_id, doc_id)
            if key in seen:
                issues.append(
                    {
                        "issue_type": "duplicate_qrel",
                        "query_id": query_id,
                        "doc_id": doc_id,
                        "message": f"Duplicate qrels row at line {line_number}",
                    }
                )
            seen.add(key)
            if relevance not in VALID_RELEVANCE:
                issues.append(
                    {
                        "issue_type": "invalid_relevance",
                        "query_id": query_id,
                        "doc_id": doc_id,
                        "message": f"Relevance must be one of 0, 1, 2, 3; got {relevance!r}",
                    }
                )

    if run_path:
        qrels = load_qrels(qrels_path)
        runs = load_run(run_path)
        checked_run_docs: set[tuple[str, str]] = set()
        for (_, query_id), ranked_docs in sorted(runs.items()):
            if query_id not in qrels:
                issues.append(
                    {
                        "issue_type": "missing_query_labels",
                        "query_id": query_id,
                        "doc_id": "",
                        "message": "Run contains query_id with no qrels rows.",
                    }
                )
                continue
            ranked_window = ranked_docs[:top_k] if top_k and top_k > 0 else ranked_docs
            for item in ranked_window:
                doc_id = str(item["doc_id"])
                key = (query_id, doc_id)
                if key in checked_run_docs:
                    continue
                checked_run_docs.add(key)
                if doc_id not in qrels[query_id]:
                    issues.append(
                        {
                            "issue_type": "unlabeled_run_doc",
                            "query_id": query_id,
                            "doc_id": doc_id,
                            "message": "Retrieved document has no qrels label.",
                        }
                    )
    return issues


def write_issues(path: Union[str, Path], issues: list[dict[str, str]]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["issue_type", "query_id", "doc_id", "message"])
        writer.writeheader()
        writer.writerows(issues)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Validate qrels labels and optional run coverage.")
    parser.add_argument("--qrels", required=True)
    parser.add_argument("--run", default="")
    parser.add_argument("--top-k", type=int, default=0, help="Only require labels for the top K run documents.")
    parser.add_argument("--output", default="")
    parser.add_argument("--strict", action="store_true", help="Exit with status 1 if issues are found.")
    args = parser.parse_args(argv)

    issues = validate_qrels_file(args.qrels, run_path=args.run or None, top_k=args.top_k or None)
    if args.output:
        write_issues(args.output, issues)
    print(f"qrels issues: {len(issues)}")
    if args.strict and issues:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
