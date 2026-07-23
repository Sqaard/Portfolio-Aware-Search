"""Fill evidence-unit spot-check labels from a compact reviewer string.

Example:

    python evaluation/fill_evidence_unit_spotcheck_labels.py \
      --input data/annotations/evidence_unit_holdout_human_spotcheck_v1.csv \
      --labels "1=3, 2=1, eu_spot_0003=0" \
      --output data/annotations/evidence_unit_holdout_human_spotcheck_v1.csv

Left-hand side can be either the 1-based visible row number or the explicit
`spotcheck_id`. Right-hand side must be 0, 1, 2, or 3.
"""

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path
from typing import Any, Optional, Union


VALID_RELEVANCE = {"0", "1", "2", "3"}
ISSUE_FIELDS = ["label_key", "issue_type", "message"]


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


def parse_label_assignments(text: str) -> tuple[dict[str, str], list[dict[str, str]]]:
    assignments: dict[str, str] = {}
    issues: list[dict[str, str]] = []
    normalized = str(text or "").replace("\n", ",").replace(";", ",")
    parts = [part.strip() for part in normalized.split(",") if part.strip()]
    for part in parts:
        match = re.fullmatch(r"([A-Za-z0-9_\-]+)\s*=\s*([0-3])", part)
        if not match:
            issues.append(
                {
                    "label_key": part,
                    "issue_type": "parse_error",
                    "message": "Expected KEY=0..3, for example 1=3 or eu_spot_0001=2.",
                }
            )
            continue
        key, relevance = match.group(1), match.group(2)
        if key in assignments:
            issues.append({"label_key": key, "issue_type": "duplicate_assignment", "message": "Duplicate label assignment."})
            continue
        assignments[key] = relevance
    return assignments, issues


def apply_label_assignments(
    rows: list[dict[str, str]],
    assignments: dict[str, str],
    *,
    reviewer_notes: str = "",
    overwrite: bool = False,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    output = [dict(row) for row in rows]
    issues: list[dict[str, str]] = []
    index_lookup = {str(index): row for index, row in enumerate(output, start=1)}
    id_lookup = {_clean(row.get("spotcheck_id")): row for row in output if _clean(row.get("spotcheck_id"))}

    for key, relevance in assignments.items():
        if relevance not in VALID_RELEVANCE:
            issues.append({"label_key": key, "issue_type": "invalid_relevance", "message": "Expected relevance 0, 1, 2, or 3."})
            continue
        row = index_lookup.get(key) or id_lookup.get(key)
        if row is None:
            issues.append({"label_key": key, "issue_type": "unknown_spotcheck", "message": "No row matches this number or spotcheck_id."})
            continue
        if _clean(row.get("human_relevance")) and not overwrite:
            issues.append(
                {
                    "label_key": key,
                    "issue_type": "existing_label",
                    "message": "Row already has human_relevance; pass --overwrite to replace it.",
                }
            )
            continue
        row["human_relevance"] = relevance
        if reviewer_notes:
            current_notes = _clean(row.get("human_notes"))
            row["human_notes"] = f"{current_notes} | {reviewer_notes}".strip(" |") if current_notes else reviewer_notes
    return output, issues


def _fieldnames(rows: list[dict[str, str]]) -> list[str]:
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    for required in ["human_relevance", "human_notes"]:
        if required not in fields:
            fields.append(required)
    return fields


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Fill evidence-unit spot-check labels from a compact reviewer string.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--labels", default="", help='Inline labels such as "1=3, 2=1".')
    parser.add_argument("--labels-file", default="", help="Optional text file containing labels.")
    parser.add_argument("--reviewer-notes", default="")
    parser.add_argument("--issues-output", default="")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args(argv)

    label_text = args.labels
    if args.labels_file:
        label_text = f"{label_text}, {Path(args.labels_file).read_text(encoding='utf-8')}" if label_text else Path(args.labels_file).read_text(encoding="utf-8")
    assignments, parse_issues = parse_label_assignments(label_text)
    rows, apply_issues = apply_label_assignments(
        read_csv(args.input),
        assignments,
        reviewer_notes=args.reviewer_notes,
        overwrite=args.overwrite,
    )
    issues = parse_issues + apply_issues
    write_csv(args.output, rows, _fieldnames(rows))
    if args.issues_output:
        write_csv(args.issues_output, issues, ISSUE_FIELDS)
    print(f"assignments={len(assignments)} issues={len(issues)} output={args.output}")
    if args.strict and issues:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
