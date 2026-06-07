"""Build a 300-context SEC medium handoff set through FinIR retrieval."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from crawler.collect_sec_dow30 import TEST_START, collect_sec_dow30_records, write_ticker_metadata  # noqa: E402
from crawler.normalize_documents import normalize_records  # noqa: E402
from features.build_fingpt_handoff_package import build_handoff_package  # noqa: E402
from finportfolio_ir.dow30 import DOW30_TICKERS  # noqa: E402
from finportfolio_ir.io_utils import read_jsonl, write_jsonl  # noqa: E402
from finportfolio_ir.time_utils import parse_datetime  # noqa: E402
from retrieval.retrieve_for_portfolio import retrieval_records  # noqa: E402


DEFAULT_RAW = Path("data/raw_documents/sec_dow30_2010_2023_raw.jsonl")
DEFAULT_METADATA = Path("data/processed_documents/dow30_sec_ticker_metadata.csv")
DEFAULT_PROCESSED = Path("data/processed_documents/sec_dow30_2010_2023_documents.jsonl")
DEFAULT_WORK_DIR = Path("data/exports/sec_dow30_medium")
DEFAULT_HANDOFF_DIR = Path("data/exports/fingpt_handoff_sec_medium")
TRAIN_DECISION = "2021-10-01T09:30:00-04:00"
TEST_DECISION = "2023-03-01T09:30:00-05:00"


def _split_for_available_at(value: str) -> str:
    return "train" if parse_datetime(value).date() < TEST_START else "test"


def _filter_docs_by_split(records: list[dict[str, Any]], split: str) -> list[dict[str, Any]]:
    return [record for record in records if _split_for_available_at(str(record["available_at"])) == split]


def _write_portfolio(path: Path, ticker: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                f"portfolio_id: dow30_{ticker.lower()}_focus",
                "holdings:",
                f"  {ticker}: 1.0",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _write_query_set(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["query_id", "portfolio", "decision_datetime", "split", "regime", "notes"])
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _ensure_raw_and_metadata(
    *,
    raw_path: Path,
    metadata_path: Path,
    refresh: bool,
    user_agent: str,
    delay_seconds: float,
    max_download_bytes: int,
    max_body_chars: int,
) -> None:
    if raw_path.exists() and metadata_path.exists() and not refresh:
        return
    records, metadata_rows = collect_sec_dow30_records(
        user_agent=user_agent,
        train_per_ticker=7,
        test_per_ticker=3,
        delay_seconds=delay_seconds,
        max_download_bytes=max_download_bytes,
        max_body_chars=max_body_chars,
    )
    write_jsonl(raw_path, records)
    write_ticker_metadata(metadata_path, metadata_rows)


def _normalize_sec_docs(raw_path: Path, metadata_path: Path, processed_path: Path) -> list[dict[str, Any]]:
    raw = read_jsonl(raw_path)
    normalized = normalize_records(raw, metadata_path, "data/source_registry/source_registry.csv")
    # Reattach split and SEC fields that are outside FinancialDocument but useful
    # for medium-corpus diagnostics.
    raw_by_doc = {str(record["doc_id"]): record for record in raw}
    for record in normalized:
        raw_record = raw_by_doc.get(str(record["doc_id"]), {})
        record["split"] = raw_record.get("split") or _split_for_available_at(str(record["available_at"]))
        record["sec_form"] = raw_record.get("sec_form", "")
        record["sec_accession_number"] = raw_record.get("sec_accession_number", "")
        record["sec_report_date"] = raw_record.get("sec_report_date", "")
    write_jsonl(processed_path, normalized)
    return normalized


def _retrieval_rows_for_split(
    *,
    split: str,
    docs_path: Path,
    metadata_path: Path,
    work_dir: Path,
    config_path: str,
    top_k: int,
) -> list[dict[str, Any]]:
    decision = TRAIN_DECISION if split == "train" else TEST_DECISION
    regime = "train_pre_oos_sec_backbone" if split == "train" else "test_oos_sec_backbone"
    rows: list[dict[str, Any]] = []
    query_rows: list[dict[str, str]] = []
    for ticker in DOW30_TICKERS:
        portfolio_path = work_dir / "portfolios" / f"dow30_{ticker.lower()}_focus.yaml"
        _write_portfolio(portfolio_path, ticker)
        query_id = f"sec_medium_{split}_{ticker.lower()}"
        query_rows.append(
            {
                "query_id": query_id,
                "portfolio": str(portfolio_path),
                "decision_datetime": decision,
                "split": split,
                "regime": regime,
                "notes": f"{ticker} focused SEC retrieval for {split} split.",
            }
        )
        retrieved = retrieval_records(
            documents_path=docs_path,
            portfolio_path=portfolio_path,
            metadata_path=metadata_path,
            decision_datetime_text=decision,
            config_path=config_path,
            top_k=top_k,
            query_id=query_id,
            method="full_hybrid",
        )
        for record in retrieved:
            record["split"] = split
            record["document_split"] = split
            record["regime"] = regime
            record["query_notes"] = f"{ticker} focused SEC retrieval for {split} split."
        rows.extend(retrieved)
    _write_query_set(work_dir / f"query_set_{split}.csv", query_rows)
    return rows


def _select_unique_context_rows(rows: list[dict[str, Any]], target_contexts: int) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen_doc_ids: set[str] = set()
    for split in ("train", "test"):
        split_rows = [row for row in rows if row.get("split") == split]
        split_rows.sort(key=lambda row: (str(row.get("portfolio_id", "")), int(row.get("rank", 999)), str(row.get("doc_id", ""))))
        for row in split_rows:
            doc_id = str(row.get("doc_id", ""))
            if doc_id in seen_doc_ids:
                continue
            seen_doc_ids.add(doc_id)
            row = dict(row)
            row["rank"] = len(selected) + 1
            selected.append(row)
            if len(selected) >= target_contexts:
                return selected
    return selected


def build_sec_medium_handoff(
    *,
    raw_path: Path,
    metadata_path: Path,
    processed_path: Path,
    work_dir: Path,
    handoff_dir: Path,
    refresh: bool,
    user_agent: str,
    delay_seconds: float,
    max_download_bytes: int,
    max_body_chars: int,
    target_contexts: int,
    config_path: str,
) -> dict[str, Any]:
    _ensure_raw_and_metadata(
        raw_path=raw_path,
        metadata_path=metadata_path,
        refresh=refresh,
        user_agent=user_agent,
        delay_seconds=delay_seconds,
        max_download_bytes=max_download_bytes,
        max_body_chars=max_body_chars,
    )
    normalized = _normalize_sec_docs(raw_path, metadata_path, processed_path)
    train_docs = _filter_docs_by_split(normalized, "train")
    test_docs = _filter_docs_by_split(normalized, "test")
    train_docs_path = processed_path.with_name(processed_path.stem + "_train.jsonl")
    test_docs_path = processed_path.with_name(processed_path.stem + "_test.jsonl")
    write_jsonl(train_docs_path, train_docs)
    write_jsonl(test_docs_path, test_docs)

    train_rows = _retrieval_rows_for_split(
        split="train",
        docs_path=train_docs_path,
        metadata_path=metadata_path,
        work_dir=work_dir,
        config_path=config_path,
        top_k=7,
    )
    test_rows = _retrieval_rows_for_split(
        split="test",
        docs_path=test_docs_path,
        metadata_path=metadata_path,
        work_dir=work_dir,
        config_path=config_path,
        top_k=3,
    )
    retrieval_rows = _select_unique_context_rows(train_rows + test_rows, target_contexts)
    retrieval_path = work_dir / "retrieved_docs_sec_medium.jsonl"
    write_jsonl(retrieval_path, retrieval_rows)
    manifest = build_handoff_package(retrieval_rows, handoff_dir, retrieval_path)
    split_counts = Counter(str(row.get("split", "")) for row in retrieval_rows)
    doc_split_counts = Counter(_split_for_available_at(str(row["available_at"])) for row in normalized)
    summary = {
        "raw_documents": len(read_jsonl(raw_path)),
        "processed_documents": len(normalized),
        "processed_split_counts": dict(doc_split_counts),
        "retrieved_context_rows": len(retrieval_rows),
        "retrieved_split_counts": dict(split_counts),
        "unique_retrieved_doc_ids": len({str(row["doc_id"]) for row in retrieval_rows}),
        "retrieval_path": str(retrieval_path),
        "handoff_contexts": str(handoff_dir / "retrieved_contexts.jsonl"),
        "handoff_manifest": str(handoff_dir / "handoff_manifest.json"),
        "handoff_status": manifest.get("status"),
    }
    (work_dir / "sec_medium_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Build a 300-context SEC medium handoff set through FinIR.")
    parser.add_argument("--raw", default=str(DEFAULT_RAW))
    parser.add_argument("--metadata", default=str(DEFAULT_METADATA))
    parser.add_argument("--processed", default=str(DEFAULT_PROCESSED))
    parser.add_argument("--work-dir", default=str(DEFAULT_WORK_DIR))
    parser.add_argument("--handoff-dir", default=str(DEFAULT_HANDOFF_DIR))
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--user-agent", default="FinPortfolioIR/0.1 research contact local@example.invalid")
    parser.add_argument("--delay-seconds", type=float, default=0.12)
    parser.add_argument("--max-download-bytes", type=int, default=700_000)
    parser.add_argument("--max-body-chars", type=int, default=80_000)
    parser.add_argument("--target-contexts", type=int, default=300)
    parser.add_argument("--config", default="configs/default.yaml")
    args = parser.parse_args(argv)

    summary = build_sec_medium_handoff(
        raw_path=Path(args.raw),
        metadata_path=Path(args.metadata),
        processed_path=Path(args.processed),
        work_dir=Path(args.work_dir),
        handoff_dir=Path(args.handoff_dir),
        refresh=args.refresh,
        user_agent=args.user_agent,
        delay_seconds=args.delay_seconds,
        max_download_bytes=args.max_download_bytes,
        max_body_chars=args.max_body_chars,
        target_contexts=args.target_contexts,
        config_path=args.config,
    )
    print(json.dumps(summary, indent=2))
    return 0 if summary["handoff_status"] == "passed" and summary["retrieved_context_rows"] == args.target_contexts else 1


if __name__ == "__main__":
    raise SystemExit(main())
