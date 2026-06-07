"""Combine JSONL files while dropping duplicate keys."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


def combine_jsonl_by_key(*, inputs: list[Path], output: Path, key: str, summary_output: Path) -> dict[str, Any]:
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    duplicate_rows = 0
    input_counts: dict[str, int] = {}

    for path in inputs:
        input_count = 0
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                input_count += 1
                row = json.loads(line)
                value = str(row.get(key, ""))
                if value and value in seen:
                    duplicate_rows += 1
                    continue
                if value:
                    seen.add(value)
                rows.append(row)
        input_counts[str(path)] = input_count

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    summary = {
        "inputs": [str(path) for path in inputs],
        "input_counts": input_counts,
        "output": str(output),
        "key": key,
        "output_rows": len(rows),
        "duplicate_rows_dropped": duplicate_rows,
        "split_counts": dict(Counter(str(row.get("split", "")) for row in rows)),
        "source_type_counts": dict(Counter(str(row.get("source_type", "")) for row in rows)),
        "ticker_counts": dict(
            sorted(
                Counter(
                    str(
                        row.get("sec_ticker")
                        or ((row.get("sec") or {}).get("ticker") if isinstance(row.get("sec"), dict) else "")
                        or ((row.get("matched_tickers") or [""])[0])
                    )
                    for row in rows
                ).items()
            )
        ),
    }
    summary_output.parent.mkdir(parents=True, exist_ok=True)
    summary_output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Combine JSONL files and drop duplicate keys.")
    parser.add_argument("--inputs", required=True, help="Comma-separated JSONL paths.")
    parser.add_argument("--output", required=True)
    parser.add_argument("--key", default="doc_id")
    parser.add_argument("--summary-output", required=True)
    args = parser.parse_args(argv)

    inputs = [Path(item.strip()) for item in args.inputs.split(",") if item.strip()]
    summary = combine_jsonl_by_key(
        inputs=inputs,
        output=Path(args.output),
        key=args.key,
        summary_output=Path(args.summary_output),
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
