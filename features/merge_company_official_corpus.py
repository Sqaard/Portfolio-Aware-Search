"""Merge company-official discovery outputs into one deduplicated corpus."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    if not path.exists() or path.stat().st_size == 0:
        return
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Bad JSON in {path} line {line_no}: {exc}") from exc
            if isinstance(row, dict):
                yield row


def row_key(row: dict[str, Any]) -> tuple[str, str]:
    doc_id = str(row.get("doc_id") or "").strip()
    if doc_id:
        return ("doc_id", doc_id)
    canonical = str(row.get("canonical_url") or row.get("url") or "").strip().lower()
    if canonical:
        return ("url", canonical)
    doc_hash = str(row.get("document_hash") or "").strip()
    if doc_hash:
        return ("hash", doc_hash)
    return ("title", f"{row.get('title', '')}|{row.get('published_at', '')}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--primary", action="append", default=[], help="Primary JSONL corpus path. Can be repeated.")
    parser.add_argument("--backfill-dir", default="", help="Directory with *_documents.jsonl adapter batches.")
    parser.add_argument("--output", required=True)
    parser.add_argument("--summary-output", required=True)
    parser.add_argument("--expected-tickers", default="", help="Comma-separated expected ticker coverage set.")
    args = parser.parse_args()

    inputs: list[tuple[str, Path]] = []
    for raw_path in args.primary:
        inputs.append(("primary", Path(raw_path)))
    if args.backfill_dir:
        for path in sorted(Path(args.backfill_dir).glob("*_documents.jsonl")):
            inputs.append(("adapter_backfill", path))

    seen: dict[tuple[str, str], dict[str, Any]] = {}
    duplicate_count = 0
    source_labels: dict[tuple[str, str], list[str]] = defaultdict(list)

    for label, path in inputs:
        for row in read_jsonl(path):
            key = row_key(row)
            source_labels[key].append(label)
            if key in seen:
                duplicate_count += 1
                current_wc = int(seen[key].get("body_word_count") or 0)
                new_wc = int(row.get("body_word_count") or 0)
                if new_wc > current_wc:
                    seen[key] = row
                continue
            seen[key] = row

    rows = list(seen.values())
    rows.sort(key=lambda row: (str(row.get("available_at") or row.get("published_at") or ""), str(row.get("doc_id") or "")))

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    ticker_counts: Counter[str] = Counter()
    source_type_counts: Counter[str] = Counter()
    event_type_counts: Counter[str] = Counter()
    method_counts: Counter[str] = Counter()
    split_counts: Counter[str] = Counter()
    date_ranges: dict[str, list[str]] = {}

    for row in rows:
        tickers = row.get("matched_tickers") or row.get("tickers_detected") or []
        if isinstance(tickers, str):
            tickers = [tickers]
        ticker = str(tickers[0]) if tickers else ""
        if ticker:
            ticker_counts[ticker] += 1
            published = str(row.get("published_at") or row.get("available_at") or "")
            if published:
                current = date_ranges.setdefault(ticker, [published, published])
                current[0] = min(current[0], published)
                current[1] = max(current[1], published)
        source_type_counts[str(row.get("source_type") or "missing")] += 1
        event_type_counts[str(row.get("event_type") or "missing")] += 1
        method_counts[str(row.get("discovery_method") or "missing")] += 1
        split_counts[str(row.get("document_split") or "missing")] += 1

    expected = [ticker.strip().upper() for ticker in args.expected_tickers.split(",") if ticker.strip()]
    covered = sorted(ticker_counts)
    uncovered = sorted(set(expected) - set(covered)) if expected else []

    summary = {
        "generated_at": utc_now_iso(),
        "inputs": [{"role": label, "path": str(path)} for label, path in inputs],
        "rows": len(rows),
        "duplicates_dropped": duplicate_count,
        "covered_tickers": covered,
        "covered_ticker_count": len(covered),
        "expected_ticker_count": len(expected) if expected else None,
        "uncovered_tickers": uncovered,
        "ticker_counts": dict(sorted(ticker_counts.items())),
        "ticker_date_ranges": dict(sorted(date_ranges.items())),
        "source_type_counts": dict(sorted(source_type_counts.items())),
        "event_type_counts": dict(sorted(event_type_counts.items())),
        "discovery_method_counts": dict(sorted(method_counts.items())),
        "document_split_counts": dict(sorted(split_counts.items())),
    }

    summary_path = Path(args.summary_output)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
