"""Merge SEC Dow 30 raw sources into a reproducible 300-document corpus."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from crawler.normalize_documents import normalize_records  # noqa: E402
from finportfolio_ir.io_utils import read_jsonl, write_jsonl  # noqa: E402


def _ticker(record: dict[str, Any]) -> str:
    if record.get("ticker"):
        return str(record["ticker"]).upper()
    if isinstance(record.get("sec"), dict) and record["sec"].get("ticker"):
        return str(record["sec"]["ticker"]).upper()
    tickers = record.get("matched_tickers") or record.get("tickers_detected") or []
    return str(tickers[0]).upper() if tickers else ""


def _split(record: dict[str, Any]) -> str:
    if record.get("split") in {"train", "test"}:
        return str(record["split"])
    available = str(record.get("available_at") or record.get("published_at") or "")
    return "train" if available[:10] < "2021-10-01" else "test"


def _dedup_key(record: dict[str, Any]) -> str:
    accession = (
        record.get("sec_accession_number")
        or (record.get("sec", {}) if isinstance(record.get("sec"), dict) else {}).get("accession")
        or record.get("version_id")
    )
    if accession:
        return f"accession:{accession}"
    url = record.get("canonical_url") or record.get("url")
    if url:
        return f"url:{url}"
    return f"doc:{record.get('doc_id')}"


def _sort_key(record: dict[str, Any]) -> tuple[str, str, str, str]:
    return (_split(record), _ticker(record), str(record.get("available_at", "")), str(record.get("doc_id", "")))


def _select_balanced(records: list[dict[str, Any]], target_docs: int) -> list[dict[str, Any]]:
    by_ticker: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        ticker = _ticker(record)
        if ticker:
            by_ticker.setdefault(ticker, []).append(record)
    if len(by_ticker) < 30:
        raise RuntimeError(f"Expected 30 tickers, found {len(by_ticker)}")

    selected: list[dict[str, Any]] = []
    selected_keys: set[str] = set()
    for ticker in sorted(by_ticker):
        rows = sorted(by_ticker[ticker], key=lambda row: (_split(row), str(row.get("available_at", "")), str(row.get("doc_id", ""))))
        train = [row for row in rows if _split(row) == "train"]
        test = [row for row in rows if _split(row) == "test"]
        for row in (train[:8] + test[:2]):
            key = _dedup_key(row)
            if key in selected_keys:
                continue
            selected_keys.add(key)
            selected.append(row)

    if len(selected) < target_docs:
        for row in sorted(records, key=_sort_key):
            key = _dedup_key(row)
            if key in selected_keys:
                continue
            selected_keys.add(key)
            selected.append(row)
            if len(selected) >= target_docs:
                break
    if len(selected) < target_docs:
        raise RuntimeError(f"Could only select {len(selected)} records out of requested {target_docs}")
    return sorted(selected[:target_docs], key=_sort_key)


def build_sec_300_corpus(
    *,
    inputs: list[Path],
    output_raw: Path,
    output_processed: Path,
    metadata: Path,
    source_registry: Path,
    summary_output: Path,
    target_docs: int,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for path in inputs:
        rows.extend(read_jsonl(path))
    by_key: dict[str, dict[str, Any]] = {}
    for row in rows:
        by_key.setdefault(_dedup_key(row), row)
    selected = _select_balanced(list(by_key.values()), target_docs)
    write_jsonl(output_raw, selected)

    normalized = normalize_records(selected, metadata, source_registry)
    raw_by_doc = {str(row.get("doc_id")): row for row in selected}
    for row in normalized:
        raw = raw_by_doc.get(str(row.get("doc_id")), {})
        ticker = _ticker(raw or row)
        row["split"] = _split(raw or row)
        row["sec_ticker"] = ticker
        row["sec_form"] = raw.get("sec_form") or (raw.get("sec", {}) if isinstance(raw.get("sec"), dict) else {}).get("form", "")
        row["sec_accession_number"] = raw.get("sec_accession_number") or raw.get("version_id") or (
            raw.get("sec", {}) if isinstance(raw.get("sec"), dict) else {}
        ).get("accession", "")
        # SEC filings have an authoritative registrant ticker. Keep retrieval
        # labels conservative and avoid false positives from short tickers such
        # as V appearing in filing boilerplate.
        if ticker:
            row["matched_tickers"] = [ticker]
            row["matched_holdings"] = [ticker]
            row["tickers_detected"] = [ticker]
    write_jsonl(output_processed, normalized)

    summary = {
        "input_files": [str(path) for path in inputs],
        "input_rows": len(rows),
        "deduped_rows": len(by_key),
        "raw_rows": len(selected),
        "processed_rows": len(normalized),
        "raw_split_counts": dict(Counter(_split(row) for row in selected)),
        "processed_split_counts": dict(Counter(str(row.get("split", "")) for row in normalized)),
        "raw_ticker_counts": dict(sorted(Counter(_ticker(row) for row in selected).items())),
        "processed_ticker_count": len({ticker for row in normalized for ticker in row.get("matched_tickers", [])}),
        "min_available_at": min(str(row.get("available_at", "")) for row in selected),
        "max_available_at": max(str(row.get("available_at", "")) for row in selected),
        "raw_output": str(output_raw),
        "processed_output": str(output_processed),
    }
    summary_output.parent.mkdir(parents=True, exist_ok=True)
    summary_output.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a merged 300-document SEC Dow 30 corpus.")
    parser.add_argument("--inputs", required=True, help="Comma-separated raw JSONL inputs.")
    parser.add_argument("--output-raw", default="data/raw_documents/sec_dow30_2010_2023_300.jsonl")
    parser.add_argument("--output-processed", default="data/processed_documents/sec_dow30_2010_2023_300_documents.jsonl")
    parser.add_argument("--metadata", default="data/processed_documents/dow30_ticker_metadata.csv")
    parser.add_argument("--source-registry", default="data/source_registry/source_registry.csv")
    parser.add_argument("--summary-output", default="data/processed_documents/sec_dow30_2010_2023_300_summary.json")
    parser.add_argument("--target-docs", type=int, default=300)
    args = parser.parse_args(argv)

    summary = build_sec_300_corpus(
        inputs=[Path(item.strip()) for item in args.inputs.split(",") if item.strip()],
        output_raw=Path(args.output_raw),
        output_processed=Path(args.output_processed),
        metadata=Path(args.metadata),
        source_registry=Path(args.source_registry),
        summary_output=Path(args.summary_output),
        target_docs=args.target_docs,
    )
    print(json.dumps(summary, indent=2))
    return 0 if summary["raw_rows"] == args.target_docs and summary["processed_rows"] == args.target_docs else 1


if __name__ == "__main__":
    raise SystemExit(main())
