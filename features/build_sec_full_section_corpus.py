"""Build a full-text SEC section corpus from the 300 filing backbone."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from crawler.normalize_documents import normalize_records  # noqa: E402
from crawler.sec_section_parser import extract_sec_sections, html_to_sec_text  # noqa: E402
from finportfolio_ir.io_utils import read_jsonl, write_jsonl  # noqa: E402


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value)[:160]


def _sec_meta(record: dict[str, Any]) -> dict[str, str]:
    sec = record.get("sec") if isinstance(record.get("sec"), dict) else {}
    ticker = (
        record.get("ticker")
        or record.get("sec_ticker")
        or sec.get("ticker")
        or (record.get("matched_tickers") or [""])[0]
    )
    accession = (
        record.get("sec_accession_number")
        or record.get("version_id")
        or sec.get("accession")
        or ""
    )
    form = record.get("sec_form") or sec.get("form") or ""
    primary_document = str(sec.get("primary_document", "") or "")
    if not primary_document:
        primary_document = Path(urlsplit(str(record.get("canonical_url") or record.get("url") or "")).path).name
    return {
        "ticker": str(ticker).upper(),
        "accession": str(accession),
        "form": str(form).upper(),
        "cik": str(sec.get("cik", "")),
        "filing_date": str(record.get("sec_filing_date") or sec.get("filing_date") or ""),
        "report_date": str(record.get("sec_report_date") or sec.get("report_date") or ""),
        "primary_document": primary_document,
    }


def _fetch_full_html(
    url: str,
    *,
    cache_path: Path,
    user_agent: str,
    sleep_seconds: float,
    max_download_bytes: int,
) -> tuple[str, str, int]:
    if cache_path.exists():
        text = cache_path.read_text(encoding="utf-8", errors="replace")
        return text, "cached_full", len(text.encode("utf-8", errors="replace"))

    time.sleep(max(0.0, sleep_seconds))
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": user_agent,
            "Accept": "text/html,text/plain,*/*",
            "Accept-Encoding": "identity",
        },
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        raw = response.read(max_download_bytes + 1)
    status = "ok_full" if len(raw) <= max_download_bytes else "truncated_at_max_download_bytes"
    if len(raw) > max_download_bytes:
        raw = raw[:max_download_bytes]
    text = raw.decode("utf-8", errors="replace")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(text, encoding="utf-8")
    return text, status, len(raw)


def _read_or_fetch_json(
    url: str,
    *,
    cache_path: Path,
    user_agent: str,
    sleep_seconds: float,
) -> tuple[dict[str, Any], str]:
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8")), "cached_index"

    time.sleep(max(0.0, sleep_seconds))
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": user_agent,
            "Accept": "application/json",
            "Accept-Encoding": "identity",
        },
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        payload = json.loads(response.read().decode("utf-8"))
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload, "ok_index"


def _archive_directory_url(primary_url: str) -> str:
    return primary_url.rsplit("/", 1)[0] + "/"


def _filing_index_url(primary_url: str) -> str:
    return urljoin(_archive_directory_url(primary_url), "index.json")


def _textual_exhibit_items(
    index_payload: dict[str, Any],
    *,
    primary_document: str,
    max_exhibits: int,
) -> list[dict[str, Any]]:
    primary = primary_document.lower().strip()
    items = index_payload.get("directory", {}).get("item", []) or []
    selected: list[dict[str, Any]] = []
    for item in items:
        name = str(item.get("name", "")).strip()
        lower = name.lower()
        if not name or lower == primary:
            continue
        if lower.endswith((".xml", ".xsd", ".jpg", ".jpeg", ".png", ".gif", ".zip", ".css", ".js")):
            continue
        if lower.endswith(("-index.html", "-index-headers.html")) or "-index" in lower:
            continue
        if lower.endswith(".txt") and lower.count("-") >= 2:
            # The complete-submission text duplicates the filing package. Keep
            # exhibit documents instead of one huge mixed container.
            continue
        if not lower.endswith((".htm", ".html", ".txt")):
            continue
        if not _looks_like_exhibit_filename(lower):
            continue
        selected.append(item)

    selected.sort(key=lambda item: (_exhibit_priority(str(item.get("name", ""))), str(item.get("name", "")).lower()))
    return selected[:max_exhibits]


def _looks_like_exhibit_filename(name: str) -> bool:
    stem = Path(name).stem.lower()
    compact = "".join(ch for ch in stem if ch.isalnum())
    return bool(
        re.search(r"(?:^|[-_])ex(?:hibit|h)?[-_.]?\d", stem)
        or re.search(r"ex(?:99|10|3|\d)", compact)
        or re.search(r"dex\d", compact)
        or re.search(r"exhibit\d", compact)
        or re.search(r"exh\d", compact)
    )


def _exhibit_priority(name: str) -> tuple[int, str]:
    exhibit_id = _exhibit_id_from_name(name)
    if exhibit_id.startswith("exhibit_99"):
        return (0, exhibit_id)
    if exhibit_id.startswith("exhibit_10"):
        return (1, exhibit_id)
    if exhibit_id.startswith("exhibit_3"):
        return (2, exhibit_id)
    return (5, exhibit_id)


def _exhibit_id_from_name(name: str) -> str:
    stem = Path(name).stem.lower()
    compact = "".join(ch for ch in stem if ch.isalnum())
    match = re.search(r"(?:exhibit|exh|ex|dex)[-_]?(\d{1,3})[-_.](\d{1,3})", stem)
    if not match:
        match = re.search(r"(?:exhibit|exh|ex|dex)(99|10|3)(\d{1,3})", compact)
    if not match:
        match = re.search(r"(?:exhibit|exh|ex|dex)(\d{1,3})", compact)
    if match and len(match.groups()) >= 2 and match.group(2):
        first = match.group(1)
        second = match.group(2)
        if len(first) == 3 and first.startswith("99"):
            return f"exhibit_99_{int(first[2:])}"
        if len(first) == 3 and first.startswith("10"):
            return f"exhibit_10_{int(first[2:])}"
        return f"exhibit_{int(first)}_{int(second)}"
    if match:
        value = match.group(1)
        if len(value) >= 3 and value.startswith(("99", "10")):
            return f"exhibit_{int(value[:2])}_{int(value[2:])}" if value[2:] else f"exhibit_{int(value[:2])}"
        return f"exhibit_{int(value)}"
    return f"exhibit_attachment_{compact[:50] or 'unknown'}"


def _exhibit_title(exhibit_id: str, name: str) -> str:
    if exhibit_id.startswith("exhibit_99"):
        return f"Exhibit {exhibit_id.removeprefix('exhibit_').replace('_', '.')} Earnings Release / Investor Material"
    if exhibit_id.startswith("exhibit_10"):
        return f"Exhibit {exhibit_id.removeprefix('exhibit_').replace('_', '.')} Material Agreement"
    if exhibit_id.startswith("exhibit_3"):
        return f"Exhibit {exhibit_id.removeprefix('exhibit_').replace('_', '.')} Charter or Bylaws"
    return f"SEC Exhibit Attachment {name}"


def _section_tags(section_id: str, form: str) -> list[str]:
    tags = ["filing", "sec_section", form.lower()]
    if section_id.startswith("exhibit_"):
        tags.extend(["sec_exhibit", "exhibit"])
    if section_id.startswith("exhibit_99"):
        tags.extend(["earnings_release_candidate", "investor_material"])
    if section_id.startswith("exhibit_10"):
        tags.append("material_agreement")
    if section_id.startswith("exhibit_3"):
        tags.append("governance")
    if "risk_factors" in section_id:
        tags.append("risk_factors")
    if "mda" in section_id:
        tags.extend(["mda", "management_discussion"])
    if "financial_statements" in section_id:
        tags.append("financial_statements")
    if "market_risk" in section_id:
        tags.append("market_risk")
    if form == "8-K":
        tags.append("current_report")
    return list(dict.fromkeys(tags))


def build_exhibit_record_for_filing(
    record: dict[str, Any],
    *,
    exhibit_item: dict[str, Any],
    exhibit_html: str,
    exhibit_url: str,
    fetch_status: str,
    downloaded_bytes: int,
    max_section_chars: int,
    ordinal: int,
) -> dict[str, Any]:
    meta = _sec_meta(record)
    parent_doc_id = str(record.get("doc_id", ""))
    exhibit_name = str(exhibit_item.get("name", "")).strip()
    exhibit_id = _exhibit_id_from_name(exhibit_name)
    exhibit_text = html_to_sec_text(exhibit_html)
    title = _exhibit_title(exhibit_id, exhibit_name)
    body = f"{title}\n{exhibit_text}".strip()
    base = dict(record)
    base.update(
        {
            "doc_id": f"{parent_doc_id}__{exhibit_id}",
            "parent_doc_id": parent_doc_id,
            "title": f"{record.get('title', parent_doc_id)} - {title}",
            "body": body[:max_section_chars].rstrip(),
            "source": "SEC EDGAR",
            "source_type": "sec_filing_exhibit",
            "fetch_status": fetch_status,
            "full_fetch_status": fetch_status,
            "full_downloaded_bytes": downloaded_bytes,
            "full_text_chars": len(exhibit_text),
            "section_id": exhibit_id,
            "sec_section_id": exhibit_id,
            "sec_section_code": exhibit_id.removeprefix("exhibit_").replace("_", ".").upper(),
            "sec_section_title": title,
            "sec_section_ordinal": ordinal,
            "sec_section_start_char": 0,
            "sec_section_end_char": len(exhibit_text),
            "sec_section_chars": len(body),
            "section_truncated": len(body) > max_section_chars,
            "sec_exhibit_id": exhibit_id,
            "sec_exhibit_name": exhibit_name,
            "sec_exhibit_url": exhibit_url,
            "sec_exhibit_size": str(exhibit_item.get("size", "") or ""),
            "sec_exhibit_last_modified": str(exhibit_item.get("last-modified", "") or ""),
            "url": exhibit_url,
            "canonical_url": exhibit_url,
            "source_registry_id": record.get("source_registry_id", "sec_edgar") or "sec_edgar",
            "source_reliability_tier": record.get("source_reliability_tier", "official"),
            "content_license_note": record.get("content_license_note", "Public SEC EDGAR filing exhibit; retain source URL."),
            "version_id": f"{meta['accession']}:{exhibit_id}",
            "duplicate_cluster_id": f"{meta['accession'].replace('-', '')}:{exhibit_id}",
            "tickers_detected": [meta["ticker"]] if meta["ticker"] else [],
            "matched_tickers": [meta["ticker"]] if meta["ticker"] else [],
            "matched_holdings": [meta["ticker"]] if meta["ticker"] else [],
            "event_tags": _section_tags(exhibit_id, meta["form"]),
            "event_type": exhibit_id,
            "sec_form": meta["form"],
            "sec_ticker": meta["ticker"],
            "sec_accession_number": meta["accession"],
            "sec_filing_date": meta["filing_date"],
            "sec_report_date": meta["report_date"],
            "sec": {
                "ticker": meta["ticker"],
                "cik": meta["cik"],
                "accession": meta["accession"],
                "form": meta["form"],
                "filing_date": meta["filing_date"],
                "report_date": meta["report_date"],
                "primary_document": meta["primary_document"],
                "section_id": exhibit_id,
                "section_code": exhibit_id,
                "section_title": title,
                "exhibit_name": exhibit_name,
                "exhibit_url": exhibit_url,
            },
        }
    )
    return base


def build_section_records_for_filing(
    record: dict[str, Any],
    full_html: str,
    *,
    fetch_status: str,
    downloaded_bytes: int,
    max_section_chars: int,
) -> list[dict[str, Any]]:
    meta = _sec_meta(record)
    text = html_to_sec_text(full_html)
    sections = extract_sec_sections(text, meta["form"])
    parent_doc_id = str(record.get("doc_id", ""))
    output: list[dict[str, Any]] = []
    for index, section in enumerate(sections, start=1):
        section_doc_id = f"{parent_doc_id}__{section.section_id}"
        body = section.body[:max_section_chars].rstrip()
        base = dict(record)
        base.update(
            {
                "doc_id": section_doc_id,
                "parent_doc_id": parent_doc_id,
                "title": f"{record.get('title', parent_doc_id)} - {section.label}",
                "body": body,
                "source": "SEC EDGAR",
                "source_type": "sec_filing_section",
                "fetch_status": fetch_status,
                "full_fetch_status": fetch_status,
                "full_downloaded_bytes": downloaded_bytes,
                "full_text_chars": len(text),
                "section_id": section.section_id,
                "sec_section_id": section.section_id,
                "sec_section_code": section.item_code,
                "sec_section_title": section.title,
                "sec_section_ordinal": index,
                "sec_section_start_char": section.start_char,
                "sec_section_end_char": section.end_char,
                "sec_section_chars": len(section.body),
                "section_truncated": len(section.body) > max_section_chars,
                "url": record.get("canonical_url") or record.get("url", ""),
                "canonical_url": record.get("canonical_url") or record.get("url", ""),
                "source_registry_id": record.get("source_registry_id", "sec_edgar") or "sec_edgar",
                "source_reliability_tier": record.get("source_reliability_tier", "official"),
                "content_license_note": record.get("content_license_note", "Public SEC EDGAR filing; retain source URL."),
                "version_id": f"{meta['accession']}:{section.section_id}",
                "duplicate_cluster_id": f"{meta['accession'].replace('-', '')}:{section.section_id}",
                "tickers_detected": [meta["ticker"]] if meta["ticker"] else [],
                "matched_tickers": [meta["ticker"]] if meta["ticker"] else [],
                "matched_holdings": [meta["ticker"]] if meta["ticker"] else [],
                "event_tags": _section_tags(section.section_id, meta["form"]),
                "event_type": section.section_id,
                "sec_form": meta["form"],
                "sec_ticker": meta["ticker"],
                "sec_accession_number": meta["accession"],
                "sec_filing_date": meta["filing_date"],
                "sec_report_date": meta["report_date"],
                "sec": {
                    "ticker": meta["ticker"],
                    "cik": meta["cik"],
                    "accession": meta["accession"],
                    "form": meta["form"],
                    "filing_date": meta["filing_date"],
                    "report_date": meta["report_date"],
                    "primary_document": meta["primary_document"],
                    "section_id": section.section_id,
                    "section_code": section.item_code,
                    "section_title": section.title,
                },
            }
        )
        output.append(base)
    return output


def build_sec_full_section_corpus(
    *,
    input_raw: Path,
    output_raw: Path,
    output_processed: Path,
    metadata: Path,
    source_registry: Path,
    summary_output: Path,
    cache_dir: Path,
    user_agent: str,
    sleep_seconds: float,
    max_download_bytes: int,
    max_section_chars: int,
    max_exhibits_per_filing: int,
    exhibit_forms: set[str],
    limit: int,
) -> dict[str, Any]:
    base_rows = read_jsonl(input_raw)
    if limit > 0:
        base_rows = base_rows[:limit]

    records: list[dict[str, Any]] = []
    fetch_statuses: Counter[str] = Counter()
    index_statuses: Counter[str] = Counter()
    exhibit_fetch_statuses: Counter[str] = Counter()
    errors: list[dict[str, str]] = []
    exhibit_errors: list[dict[str, str]] = []
    for row in base_rows:
        url = str(row.get("canonical_url") or row.get("url") or "")
        parent_doc_id = str(row.get("doc_id", ""))
        if not url:
            errors.append({"doc_id": parent_doc_id, "error": "missing_url"})
            continue
        meta = _sec_meta(row)
        cache_name = _safe_name(meta["accession"] or parent_doc_id) + ".html"
        try:
            full_html, fetch_status, downloaded_bytes = _fetch_full_html(
                url,
                cache_path=cache_dir / cache_name,
                user_agent=user_agent,
                sleep_seconds=sleep_seconds,
                max_download_bytes=max_download_bytes,
            )
            section_records = build_section_records_for_filing(
                row,
                full_html,
                fetch_status=fetch_status,
                downloaded_bytes=downloaded_bytes,
                max_section_chars=max_section_chars,
            )
            records.extend(section_records)
            fetch_statuses[fetch_status] += 1
            if meta["form"] in exhibit_forms and max_exhibits_per_filing > 0:
                try:
                    index_payload, index_status = _read_or_fetch_json(
                        _filing_index_url(url),
                        cache_path=cache_dir / "indexes" / f"{_safe_name(meta['accession'] or parent_doc_id)}.json",
                        user_agent=user_agent,
                        sleep_seconds=sleep_seconds,
                    )
                    index_statuses[index_status] += 1
                    exhibit_items = _textual_exhibit_items(
                        index_payload,
                        primary_document=meta["primary_document"],
                        max_exhibits=max_exhibits_per_filing,
                    )
                    for exhibit_index, exhibit_item in enumerate(exhibit_items, start=1):
                        exhibit_name = str(exhibit_item.get("name", "")).strip()
                        exhibit_url = urljoin(_archive_directory_url(url), exhibit_name)
                        exhibit_html, exhibit_status, exhibit_bytes = _fetch_full_html(
                            exhibit_url,
                            cache_path=cache_dir / "exhibits" / f"{_safe_name(meta['accession'] or parent_doc_id)}__{_safe_name(exhibit_name)}",
                            user_agent=user_agent,
                            sleep_seconds=sleep_seconds,
                            max_download_bytes=max_download_bytes,
                        )
                        exhibit_fetch_statuses[exhibit_status] += 1
                        records.append(
                            build_exhibit_record_for_filing(
                                row,
                                exhibit_item=exhibit_item,
                                exhibit_html=exhibit_html,
                                exhibit_url=exhibit_url,
                                fetch_status=exhibit_status,
                                downloaded_bytes=exhibit_bytes,
                                max_section_chars=max_section_chars,
                                ordinal=10_000 + exhibit_index,
                            )
                        )
                except (urllib.error.URLError, TimeoutError, ValueError, OSError, json.JSONDecodeError) as exc:
                    index_statuses["error"] += 1
                    exhibit_errors.append({"doc_id": parent_doc_id, "url": url, "error": str(exc)[:300]})
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
            fetch_statuses["error"] += 1
            errors.append({"doc_id": parent_doc_id, "url": url, "error": str(exc)[:300]})

    write_jsonl(output_raw, records)

    normalized = normalize_records(records, metadata, source_registry)
    raw_by_doc = {str(row.get("doc_id")): row for row in records}
    for row in normalized:
        raw = raw_by_doc.get(str(row.get("doc_id")), {})
        meta = _sec_meta(raw or row)
        row["split"] = raw.get("split", row.get("split", ""))
        row["sec_ticker"] = meta["ticker"]
        row["sec_form"] = meta["form"]
        row["sec_accession_number"] = meta["accession"]
        for key in [
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
        ]:
            row[key] = raw.get(key, "")
        if meta["ticker"]:
            row["matched_tickers"] = [meta["ticker"]]
            row["matched_holdings"] = [meta["ticker"]]
            row["tickers_detected"] = [meta["ticker"]]
    write_jsonl(output_processed, normalized)

    summary = {
        "generated_at": _utc_now(),
        "input_raw": str(input_raw),
        "base_rows": len(base_rows),
        "section_raw_rows": len(records),
        "section_processed_rows": len(normalized),
        "fetch_status_counts": dict(fetch_statuses),
        "index_status_counts": dict(index_statuses),
        "exhibit_fetch_status_counts": dict(exhibit_fetch_statuses),
        "split_counts": dict(Counter(str(row.get("split", "")) for row in normalized)),
        "form_counts": dict(Counter(str(row.get("sec_form", "")) for row in normalized)),
        "section_counts": dict(Counter(str(row.get("sec_section_id", "")) for row in normalized)),
        "ticker_counts": dict(sorted(Counter(str(row.get("sec_ticker", "")) for row in normalized).items())),
        "exhibit_rows": sum(1 for row in normalized if str(row.get("source_type", "")) == "sec_filing_exhibit"),
        "truncated_section_rows": sum(1 for row in normalized if row.get("section_truncated") is True),
        "errors": errors[:50],
        "error_count": len(errors),
        "exhibit_errors": exhibit_errors[:50],
        "exhibit_error_count": len(exhibit_errors),
        "cache_dir": str(cache_dir),
        "output_raw": str(output_raw),
        "output_processed": str(output_processed),
    }
    summary_output.parent.mkdir(parents=True, exist_ok=True)
    summary_output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build full SEC filing section corpus.")
    parser.add_argument("--input-raw", default="data/raw_documents/sec_dow30_2010_2023_300.jsonl")
    parser.add_argument("--output-raw", default="data/raw_documents/sec_dow30_2010_2023_300_sections.jsonl")
    parser.add_argument("--output-processed", default="data/processed_documents/sec_dow30_2010_2023_300_sections_documents.jsonl")
    parser.add_argument("--metadata", default="data/processed_documents/dow30_ticker_metadata.csv")
    parser.add_argument("--source-registry", default="data/source_registry/source_registry.csv")
    parser.add_argument("--summary-output", default="data/processed_documents/sec_dow30_2010_2023_300_sections_summary.json")
    parser.add_argument("--cache-dir", default="data/raw_documents/sec_full_html_cache")
    parser.add_argument("--sleep-seconds", type=float, default=0.12)
    parser.add_argument("--max-download-bytes", type=int, default=50_000_000)
    parser.add_argument("--max-section-chars", type=int, default=250_000)
    parser.add_argument("--max-exhibits-per-filing", type=int, default=6)
    parser.add_argument("--exhibit-forms", default="8-K,10-K,10-Q")
    parser.add_argument("--limit", type=int, default=0, help="Optional number of base filings for a dry run.")
    parser.add_argument(
        "--user-agent",
        default=os.environ.get("SEC_USER_AGENT", "FinPortfolioIR/0.1 academic research contact=ivanp@example.com"),
    )
    args = parser.parse_args(argv)

    summary = build_sec_full_section_corpus(
        input_raw=Path(args.input_raw),
        output_raw=Path(args.output_raw),
        output_processed=Path(args.output_processed),
        metadata=Path(args.metadata),
        source_registry=Path(args.source_registry),
        summary_output=Path(args.summary_output),
        cache_dir=Path(args.cache_dir),
        user_agent=args.user_agent,
        sleep_seconds=args.sleep_seconds,
        max_download_bytes=args.max_download_bytes,
        max_section_chars=args.max_section_chars,
        max_exhibits_per_filing=args.max_exhibits_per_filing,
        exhibit_forms={item.strip().upper() for item in args.exhibit_forms.split(",") if item.strip()},
        limit=args.limit,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["section_processed_rows"] > 0 and summary["error_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
