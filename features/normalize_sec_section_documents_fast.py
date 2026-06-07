"""Fast normalization for SEC section/exhibit raw records.

The generic normalizer enriches entities by scanning the entire body. That is
useful for mixed web/news corpora, but too slow and unnecessary for SEC section
records because ticker, CIK, accession, form, and source metadata are already
authoritative in the raw records produced by build_sec_full_section_corpus.py.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from finportfolio_ir.schema import FinancialDocument  # noqa: E402


EXTRA_KEYS = [
    "split",
    "parent_doc_id",
    "full_fetch_status",
    "full_downloaded_bytes",
    "full_text_chars",
    "section_id",
    "sec_section_id",
    "sec_section_code",
    "sec_section_title",
    "sec_section_ordinal",
    "sec_section_start_char",
    "sec_section_end_char",
    "sec_section_chars",
    "section_truncated",
    "sec_exhibit_id",
    "sec_exhibit_name",
    "sec_exhibit_url",
    "sec_exhibit_size",
    "sec_exhibit_last_modified",
    "sec_form",
    "sec_ticker",
    "sec_accession_number",
    "sec_filing_date",
    "sec_report_date",
    "sec",
]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _sec_meta(raw: dict[str, Any]) -> dict[str, str]:
    sec = raw.get("sec") if isinstance(raw.get("sec"), dict) else {}
    return {
        "ticker": str(raw.get("sec_ticker") or sec.get("ticker") or (raw.get("matched_tickers") or [""])[0]).upper(),
        "form": str(raw.get("sec_form") or sec.get("form") or "").upper(),
        "accession": str(raw.get("sec_accession_number") or sec.get("accession") or raw.get("version_id") or ""),
        "filing_date": str(raw.get("sec_filing_date") or sec.get("filing_date") or ""),
        "report_date": str(raw.get("sec_report_date") or sec.get("report_date") or ""),
    }


def normalize_sec_sections_fast(*, input_raw: Path, output_processed: Path, summary_output: Path) -> dict[str, Any]:
    output_processed.parent.mkdir(parents=True, exist_ok=True)
    summary_output.parent.mkdir(parents=True, exist_ok=True)
    seen_hashes: set[str] = set()
    row_count = 0
    output_count = 0
    skipped_count = 0
    split_counts: Counter[str] = Counter()
    source_type_counts: Counter[str] = Counter()
    form_counts: Counter[str] = Counter()
    ticker_counts: Counter[str] = Counter()
    section_counts: Counter[str] = Counter()
    errors: list[dict[str, str]] = []

    with input_raw.open("r", encoding="utf-8") as input_handle, output_processed.open("w", encoding="utf-8") as output_handle:
        for line in input_handle:
            if not line.strip():
                continue
            row_count += 1
            raw = json.loads(line)
            meta = _sec_meta(raw)
            if meta["ticker"]:
                raw["tickers_detected"] = [meta["ticker"]]
                raw["matched_tickers"] = [meta["ticker"]]
                raw["matched_holdings"] = [meta["ticker"]]
            try:
                document = FinancialDocument.from_dict(raw)
            except (KeyError, ValueError, TypeError) as exc:
                skipped_count += 1
                if len(errors) < 50:
                    errors.append({"doc_id": str(raw.get("doc_id", "")), "error": str(exc)[:300]})
                continue
            if document.document_hash in seen_hashes:
                skipped_count += 1
                continue
            seen_hashes.add(document.document_hash)
            normalized = document.to_dict()
            for key in EXTRA_KEYS:
                if key in raw:
                    normalized[key] = raw.get(key)
            normalized["sec_ticker"] = meta["ticker"]
            normalized["sec_form"] = meta["form"]
            normalized["sec_accession_number"] = meta["accession"]
            normalized["sec_filing_date"] = meta["filing_date"]
            normalized["sec_report_date"] = meta["report_date"]
            output_handle.write(json.dumps(normalized, ensure_ascii=False) + "\n")
            output_count += 1
            split_counts[str(normalized.get("split", ""))] += 1
            source_type_counts[str(normalized.get("source_type", ""))] += 1
            form_counts[meta["form"]] += 1
            ticker_counts[meta["ticker"]] += 1
            section_counts[str(normalized.get("sec_section_id") or normalized.get("section_id") or "")] += 1

    summary = {
        "generated_at": _utc_now(),
        "input_raw": str(input_raw),
        "output_processed": str(output_processed),
        "input_rows": row_count,
        "processed_rows": output_count,
        "skipped_rows": skipped_count,
        "split_counts": dict(split_counts),
        "source_type_counts": dict(source_type_counts),
        "form_counts": dict(form_counts),
        "ticker_counts": dict(sorted(ticker_counts.items())),
        "section_counts": dict(section_counts.most_common()),
        "exhibit_rows": source_type_counts.get("sec_filing_exhibit", 0),
        "errors": errors,
        "error_count": len(errors),
    }
    summary_output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fast-normalize SEC section/exhibit JSONL records.")
    parser.add_argument("--input-raw", required=True)
    parser.add_argument("--output-processed", required=True)
    parser.add_argument("--summary-output", required=True)
    args = parser.parse_args(argv)

    summary = normalize_sec_sections_fast(
        input_raw=Path(args.input_raw),
        output_processed=Path(args.output_processed),
        summary_output=Path(args.summary_output),
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["processed_rows"] > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
