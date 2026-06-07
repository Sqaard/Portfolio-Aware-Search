"""Create company-official fallback docs from SEC-attached issuer exhibits."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Bad JSON in {path} line {line_no}: {exc}") from exc
            if isinstance(row, dict):
                rows.append(row)
    return rows


def word_count(text: str) -> int:
    return len((text or "").split())


def is_good_exhibit(row: dict[str, Any], ticker: str, start_year: int, end_year: int) -> bool:
    if row.get("source_type") != "sec_filing_exhibit":
        return False
    tickers = [str(item).upper() for item in (row.get("matched_tickers") or [])]
    if ticker not in tickers:
        return False
    available_at = str(row.get("available_at") or row.get("published_at") or "")
    if not available_at[:4].isdigit():
        return False
    year = int(available_at[:4])
    if year < start_year or year > end_year:
        return False
    text = f"{row.get('title', '')} {row.get('section_type', '')} {row.get('body', '')[:1000]}".lower()
    if not any(term in text for term in ("exhibit 99.1", "exhibit_99_1", "earnings", "investor", "presentation", "results")):
        return False
    return word_count(str(row.get("body") or "")) >= 120


def convert(row: dict[str, Any], ticker: str, ingested_at: str) -> dict[str, Any]:
    out = dict(row)
    out["doc_id"] = f"company_{ticker.lower()}_sec_exhibit_fallback_{str(row.get('doc_id') or '')[-16:]}"
    out["source"] = f"company_official_{ticker.lower()}_sec_exhibit_fallback"
    out["source_type"] = "company_earnings_release"
    out["source_registry_id"] = "sec_edgar_company_exhibit_fallback"
    out["source_reliability_tier"] = "official_sec_company_exhibit"
    out["source_credibility"] = 0.9
    out["content_license_note"] = "Official issuer exhibit attached to SEC filing; preserve SEC URL and accession provenance."
    out["fetch_status"] = "ok"
    out["last_url_check_at"] = ingested_at
    out["ingested_at"] = ingested_at
    out["discovery_method"] = "sec_exhibit_fallback"
    out["discovery_source_url"] = str(row.get("parent_filing_url") or row.get("url") or "")
    out["discovery_anchor_text"] = str(row.get("title") or "")
    out["api_payload_url"] = str(row.get("url") or "")
    out["published_at_source"] = "sec_filing_available_at"
    out["fallback_source_type"] = "sec_filing_exhibit"
    out["fallback_reason"] = "company_ir_site_blocked_or_timed_out"
    out["event_type"] = "company_earnings_release"
    out["body_word_count"] = word_count(str(row.get("body") or ""))
    tags = list(dict.fromkeys([*(row.get("event_tags") or []), "company_official", "sec_exhibit_fallback"]))
    out["event_tags"] = tags
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sec-documents", required=True)
    parser.add_argument("--tickers", required=True, help="Comma-separated tickers.")
    parser.add_argument("--output", required=True)
    parser.add_argument("--summary-output", required=True)
    parser.add_argument("--start-year", type=int, default=2010)
    parser.add_argument("--end-year", type=int, default=2023)
    parser.add_argument("--max-documents-per-ticker", type=int, default=20)
    args = parser.parse_args()

    sec_rows = read_jsonl(Path(args.sec_documents))
    ingested_at = utc_now_iso()
    output_rows: list[dict[str, Any]] = []
    rejected_by_ticker: Counter[str] = Counter()
    for ticker in [item.strip().upper() for item in args.tickers.split(",") if item.strip()]:
        candidates = [row for row in sec_rows if is_good_exhibit(row, ticker, args.start_year, args.end_year)]
        candidates.sort(
            key=lambda row: (
                "exhibit_99_1" in str(row.get("section_type") or "").lower() or "exhibit 99.1" in str(row.get("title") or "").lower(),
                str(row.get("available_at") or ""),
                word_count(str(row.get("body") or "")),
            ),
            reverse=True,
        )
        selected = candidates[: args.max_documents_per_ticker]
        rejected_by_ticker[ticker] = max(0, len(candidates) - len(selected))
        output_rows.extend(convert(row, ticker, ingested_at) for row in selected)

    output_rows.sort(key=lambda row: (str(row.get("available_at") or ""), str(row.get("doc_id") or "")))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="\n") as handle:
        for row in output_rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    summary = {
        "generated_at": ingested_at,
        "rows": len(output_rows),
        "tickers": sorted({(row.get("matched_tickers") or [""])[0] for row in output_rows}),
        "ticker_counts": dict(sorted(Counter((row.get("matched_tickers") or [""])[0] for row in output_rows).items())),
        "rejected_candidate_overflow_by_ticker": dict(sorted(rejected_by_ticker.items())),
        "source_type_counts": dict(sorted(Counter(row.get("source_type") for row in output_rows).items())),
        "discovery_method_counts": dict(sorted(Counter(row.get("discovery_method") for row in output_rows).items())),
    }
    summary_path = Path(args.summary_output)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
