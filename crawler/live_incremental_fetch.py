"""Incrementally fetch live official evidence for FinPortfolio IR.

This module is intentionally separate from the historical demo corpus.  It can
be run repeatedly: already seen document ids are skipped, new raw records are
normalized, and the new normalized documents are appended to a live JSONL file.
LLM extraction is left as a queued/background step so search can become fresh
quickly without waiting for expensive model calls.

``--streaming-inbox DIR`` additionally drops each run's new documents into DIR
as one NEW JSONL file (see :mod:`crawler.streaming_delivery`) -- the only form
Spark Structured Streaming can see, since its file source never re-reads the
live JSONL this module rewrites. ``--simulate-from CORPUS`` replaces the network
collectors with a reproducible random sample of an existing corpus, for
benchmarking the downstream consumers.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from crawler.company_source_archive_discovery import (  # noqa: E402
    DiscoveryConfig,
    discover_documents_from_sources,
    fetch_url,
    make_session,
    read_sources_csv,
)
from crawler.collect_sec_filings import (  # noqa: E402
    FilingMeta,
    SEC_SUBMISSIONS_BASE,
    _filing_rows,
    _html_to_text,
    _iso_from_sec_datetime,
    _load_tickers,
    _request_json,
    _request_text,
    build_raw_record,
)
from crawler.normalize_documents import normalize_records  # noqa: E402
from features.build_official_macro_documents import (  # noqa: E402
    DEFAULT_SERIES,
    MacroSeriesSpec,
    _download_fred_rows,
    _value_to_float,
    build_macro_record,
)
from finportfolio_ir.io_utils import read_jsonl, write_jsonl  # noqa: E402
from crawler.streaming_delivery import (  # noqa: E402
    append_unique_records,
    assert_outside_inbox,
    emit_streaming_batch,
    simulate_live_fetch,
)


DEFAULT_METADATA = ROOT / "data" / "processed_documents" / "dow30_ticker_metadata.csv"
DEFAULT_SOURCE_REGISTRY = ROOT / "data" / "source_registry" / "source_registry.csv"
DEFAULT_COMPANY_SOURCES = ROOT / "data" / "source_registry" / "official_company_sources_unified_static_ready_2026-05-13.csv"
DEFAULT_LIVE_DIR = ROOT / "data" / "live_ir"
DEFAULT_RAW_OUTPUT = DEFAULT_LIVE_DIR / "live_raw_documents.jsonl"
DEFAULT_PROCESSED_OUTPUT = DEFAULT_LIVE_DIR / "live_processed_documents.jsonl"
DEFAULT_QUEUE_OUTPUT = DEFAULT_LIVE_DIR / "live_llm_queue.jsonl"
DEFAULT_STATE_OUTPUT = DEFAULT_LIVE_DIR / "live_fetch_state.json"
DEFAULT_USER_AGENT = "FinPortfolioIR/0.1 research contact@example.com"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_csv_values(value: str | Iterable[str] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        items = value.split(",")
    else:
        items = list(value)
    return [str(item).strip() for item in items if str(item).strip()]


def load_fetch_state(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"seen_doc_ids": [], "seen_document_hashes": [], "runs": []}
    if not isinstance(data, dict):
        return {"seen_doc_ids": [], "seen_document_hashes": [], "runs": []}
    data.setdefault("seen_doc_ids", [])
    data.setdefault("seen_document_hashes", [])
    data.setdefault("runs", [])
    return data


def save_fetch_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


def existing_identity(*paths: Path, state: dict[str, Any] | None = None) -> tuple[set[str], set[str]]:
    doc_ids = {str(value) for value in (state or {}).get("seen_doc_ids", []) if str(value)}
    hashes = {str(value) for value in (state or {}).get("seen_document_hashes", []) if str(value)}
    for path in paths:
        if not path.exists():
            continue
        for record in read_jsonl(path):
            doc_id = str(record.get("doc_id", "") or "")
            document_hash = str(record.get("document_hash", "") or "")
            if doc_id:
                doc_ids.add(doc_id)
            if document_hash:
                hashes.add(document_hash)
    return doc_ids, hashes


def _collect_recent_sec_meta(
    ticker_row: Any,
    *,
    forms: set[str],
    since: date,
    until: date,
    user_agent: str,
    sleep_seconds: float,
) -> list[FilingMeta]:
    payload = _request_json(f"{SEC_SUBMISSIONS_BASE}CIK{ticker_row.cik}.json", user_agent, sleep_seconds)
    recent = payload.get("filings", {}).get("recent", {})
    metas: list[FilingMeta] = []
    for row in _filing_rows(recent):
        form = str(row.get("form", "")).upper()
        filing_date_text = str(row.get("filingDate", "") or "")[:10]
        primary = str(row.get("primaryDocument", "") or "").strip()
        accession = str(row.get("accessionNumber", "") or "").strip()
        if form not in forms or not filing_date_text or not primary or not accession:
            continue
        filing_date = date.fromisoformat(filing_date_text)
        if filing_date < since or filing_date > until:
            continue
        metas.append(
            FilingMeta(
                ticker=ticker_row.ticker,
                cik=ticker_row.cik,
                company_name=ticker_row.company_name,
                sector=ticker_row.sector,
                accession=accession,
                form=form,
                filing_date=filing_date_text,
                report_date=str(row.get("reportDate", "") or ""),
                accepted_at=_iso_from_sec_datetime(str(row.get("acceptanceDateTime", "")), filing_date_text),
                primary_document=primary,
                split="live",
            )
        )
    metas.sort(key=lambda item: (item.filing_date, item.form, item.accession), reverse=True)
    return metas


def collect_live_sec_records(
    *,
    metadata_path: Path,
    tickers: list[str],
    forms: set[str],
    lookback_days: int,
    max_docs: int,
    user_agent: str,
    sleep_seconds: float,
    body_chars: int,
    seen_doc_ids: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    if max_docs <= 0:
        return [], []
    today = datetime.now(timezone.utc).date()
    since = today - timedelta(days=max(0, lookback_days))
    ticker_rows = _load_tickers(metadata_path, tickers)
    records: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    ingested_at = utc_now()
    metas: list[FilingMeta] = []
    for ticker_row in ticker_rows:
        try:
            metas.extend(
                _collect_recent_sec_meta(
                    ticker_row,
                    forms=forms,
                    since=since,
                    until=today,
                    user_agent=user_agent,
                    sleep_seconds=sleep_seconds,
                )
            )
        except Exception as exc:  # noqa: BLE001 - keep partial live fetch useful.
            errors.append({"source": "sec", "ticker": ticker_row.ticker, "error": str(exc)[:300]})
    metas.sort(key=lambda item: (item.accepted_at, item.form, item.accession), reverse=True)
    for meta in metas:
        if len(records) >= max_docs:
            break
        if meta.doc_id in seen_doc_ids:
            continue
        try:
            raw_html = _request_text(meta.archive_url, user_agent, sleep_seconds)
            body = _html_to_text(raw_html)
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
            errors.append({"source": "sec", "ticker": meta.ticker, "accession": meta.accession, "error": str(exc)[:300]})
            continue
        record = build_raw_record(meta, body, ingested_at=ingested_at, body_chars=body_chars)
        record["split"] = "live"
        record["retrieval_layer"] = "live_sec_incremental"
        records.append(record)
        seen_doc_ids.add(meta.doc_id)
    return records, errors


def select_macro_specs(series_ids: list[str]) -> tuple[MacroSeriesSpec, ...]:
    if not series_ids:
        return DEFAULT_SERIES
    requested = {series_id.upper() for series_id in series_ids}
    return tuple(spec for spec in DEFAULT_SERIES if spec.series_id.upper() in requested)


def collect_live_macro_records(
    *,
    series_specs: tuple[MacroSeriesSpec, ...],
    lookback_days: int,
    max_observations: int,
    user_agent: str,
    seen_doc_ids: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    if max_observations <= 0:
        return [], []
    today = datetime.now(timezone.utc).date()
    start_date = (today - timedelta(days=max(0, lookback_days))).isoformat()
    end_date = today.isoformat()
    records: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for spec in series_specs:
        try:
            rows = _download_fred_rows(spec.series_id, start_date, end_date, user_agent)
        except Exception as exc:  # noqa: BLE001 - keep other macro series.
            errors.append({"source": "fred", "series_id": spec.series_id, "error": str(exc)[:300]})
            continue
        for row in rows:
            if len(records) >= max_observations:
                break
            value = _value_to_float(row.get("value", ""))
            if value is None:
                continue
            observation_date = date.fromisoformat(str(row["DATE"]))
            record = build_macro_record(spec, observation_date, value)
            if str(record.get("doc_id", "")) in seen_doc_ids:
                continue
            record["split"] = "live"
            record["retrieval_layer"] = "live_macro_incremental"
            records.append(record)
            seen_doc_ids.add(str(record["doc_id"]))
        if len(records) >= max_observations:
            break
    records.sort(key=lambda row: (str(row.get("available_at", "")), str(row.get("macro_series_id", ""))), reverse=True)
    return records, errors


def _date_from_available_at(value: str) -> date | None:
    text = str(value or "").strip()
    if len(text) < 10:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def collect_live_company_ir_records(
    *,
    company_sources_path: Path,
    metadata_path: Path,
    tickers: list[str],
    lookback_days: int,
    max_sources: int,
    max_docs: int,
    max_pages_per_source: int,
    max_candidates_per_source: int,
    max_docs_per_source: int,
    min_body_words: int,
    user_agent: str,
    sleep_seconds: float,
    timeout_seconds: int,
    seen_doc_ids: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    if max_sources <= 0 or max_docs <= 0:
        return [], []
    if not company_sources_path.exists():
        return [], [{"source": "company_ir", "error": f"company sources file not found: {company_sources_path}"}]

    today = datetime.now(timezone.utc).date()
    since = today - timedelta(days=max(0, lookback_days))
    selected_tickers = {ticker.upper() for ticker in tickers if ticker} or None
    config = DiscoveryConfig(
        start_year=since.year,
        end_year=today.year,
        max_archive_pages_per_source=max(0, max_pages_per_source),
        max_detail_candidates_per_source=max(1, max_candidates_per_source),
        max_documents_per_source=max(1, max_docs_per_source),
        min_body_words=max(20, min_body_words),
        timeout_seconds=max(3, timeout_seconds),
        sleep_seconds=max(0.0, sleep_seconds),
        q4_page_size=25,
        rss_item_limit=max(10, max_candidates_per_source),
        wp_page_size=min(25, max(5, max_candidates_per_source)),
        wp_max_pages_per_year=1,
        enable_q4_feed=True,
        enable_rss=True,
        enable_wordpress=True,
        enable_generic_html=True,
        include_source_grades=("crawler_ready", ""),
    )
    sources = [
        row
        for row in read_sources_csv(company_sources_path)
        if str(row.get("needs_js", "")).lower() not in {"yes", "true", "1"}
    ]
    session = make_session(user_agent)

    def fetcher(url: str):
        return fetch_url(url, session=session, timeout_seconds=config.timeout_seconds, max_bytes=1_500_000)

    try:
        documents, _detail_manifest, source_manifest, vendor_queue = discover_documents_from_sources(
            sources,
            metadata_path=metadata_path,
            config=config,
            fetcher=fetcher,
            source_limit=max_sources,
            tickers=selected_tickers,
        )
    except Exception as exc:  # noqa: BLE001 - live refresh should keep SEC/FRED usable.
        return [], [{"source": "company_ir", "error": str(exc)[:300]}]

    fresh_documents: list[dict[str, Any]] = []
    for document in sorted(documents, key=lambda row: str(row.get("available_at", "")), reverse=True):
        if len(fresh_documents) >= max_docs:
            break
        doc_id = str(document.get("doc_id", "") or "")
        available_date = _date_from_available_at(str(document.get("available_at", "")))
        if not doc_id or doc_id in seen_doc_ids or available_date is None or available_date < since:
            continue
        enriched = dict(document)
        enriched["split"] = "live"
        enriched["retrieval_layer"] = "live_company_ir_incremental"
        fresh_documents.append(enriched)
        seen_doc_ids.add(doc_id)

    errors: list[dict[str, str]] = []
    blocked_or_failed = [
        row
        for row in vendor_queue
        if str(row.get("priority", "")).startswith(("high", "medium"))
    ][:10]
    for row in blocked_or_failed:
        errors.append(
            {
                "source": "company_ir",
                "ticker": str(row.get("ticker", "")),
                "url": str(row.get("url", "")),
                "error": f"{row.get('vendor_profile', '')}:{row.get('priority', '')}",
            }
        )
    if not fresh_documents and source_manifest and not errors:
        errors.append({"source": "company_ir", "error": "no fresh company IR documents accepted within limits"})
    return fresh_documents, errors


def restore_live_extra_fields(raw_records: list[dict[str, Any]], normalized_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    raw_by_doc_id = {str(row.get("doc_id", "")): row for row in raw_records}
    extra_keys = [
        "sec",
        "split",
        "retrieval_layer",
        "macro_series_id",
        "macro_series_title",
        "macro_family",
        "macro_frequency",
        "macro_observation_date",
        "macro_value",
        "macro_units",
        "macro_release_lag_days",
        "discovery_source_url",
        "discovery_method",
        "discovery_anchor_text",
        "api_payload_url",
        "published_at_source",
        "body_word_count",
    ]
    for record in normalized_records:
        raw = raw_by_doc_id.get(str(record.get("doc_id", "")), {})
        for key in extra_keys:
            if key in raw:
                record[key] = raw[key]
        if raw.get("source_type") == "official_macro_release":
            record["matched_tickers"] = ["MARKET"]
            record["matched_holdings"] = []
            record["tickers_detected"] = ["MARKET"]
    return normalized_records


def append_unique_jsonl(path: Path, new_records: list[dict[str, Any]]) -> int:
    return len(append_unique_records(path, new_records))


def queue_priority(record: dict[str, Any]) -> tuple[int, str]:
    source_type = str(record.get("source_type", "") or "")
    sec = record.get("sec") if isinstance(record.get("sec"), dict) else {}
    form = str(sec.get("form", "") if sec else "").upper()
    if source_type == "sec_filing" and form in {"10-K", "10-Q"}:
        return 95, f"{form} filing: high-value financial/risk extraction"
    if source_type == "sec_filing" and form == "8-K":
        return 85, "8-K filing: fresh event evidence"
    if source_type == "official_macro_release":
        return 70, "official macro release: update portfolio regime signals"
    if source_type.startswith("company_"):
        return 60, "company IR document: summarize investor-facing claims"
    return 50, "new trusted document"


def append_llm_queue(path: Path, records: list[dict[str, Any]]) -> int:
    existing = read_jsonl(path) if path.exists() else []
    queued_doc_ids = {str(row.get("doc_id", "")) for row in existing if str(row.get("doc_id", ""))}
    now = utc_now()
    new_rows: list[dict[str, Any]] = []
    for record in records:
        doc_id = str(record.get("doc_id", "") or "")
        if not doc_id or doc_id in queued_doc_ids:
            continue
        priority, reason = queue_priority(record)
        new_rows.append(
            {
                "doc_id": doc_id,
                "status": "pending",
                "priority": priority,
                "reason": reason,
                "source_type": record.get("source_type", ""),
                "title": record.get("title", ""),
                "available_at": record.get("available_at", ""),
                "queued_at": now,
            }
        )
        queued_doc_ids.add(doc_id)
    if new_rows:
        write_jsonl(path, [*existing, *sorted(new_rows, key=lambda row: (-int(row["priority"]), str(row["available_at"])))])
    elif not path.exists():
        write_jsonl(path, [])
    return len(new_rows)


def fetch_live_incremental(
    *,
    metadata_path: Path,
    source_registry_path: Path,
    raw_output: Path,
    processed_output: Path,
    queue_output: Path,
    state_output: Path,
    tickers: list[str],
    forms: set[str],
    lookback_days: int,
    max_sec_docs: int,
    macro_series: list[str],
    macro_lookback_days: int,
    max_macro_observations: int,
    company_sources_path: Path,
    company_lookback_days: int,
    max_company_sources: int,
    max_company_docs: int,
    max_company_pages_per_source: int,
    max_company_candidates_per_source: int,
    max_company_docs_per_source: int,
    company_min_body_words: int,
    company_timeout_seconds: int,
    user_agent: str,
    sleep_seconds: float,
    body_chars: int,
    streaming_inbox: Path | None = None,
) -> dict[str, Any]:
    for path in [raw_output, processed_output, queue_output, state_output]:
        path.parent.mkdir(parents=True, exist_ok=True)
    state = load_fetch_state(state_output)
    seen_doc_ids, seen_hashes = existing_identity(raw_output, processed_output, state=state)

    sec_records, sec_errors = collect_live_sec_records(
        metadata_path=metadata_path,
        tickers=tickers,
        forms=forms,
        lookback_days=lookback_days,
        max_docs=max_sec_docs,
        user_agent=user_agent,
        sleep_seconds=sleep_seconds,
        body_chars=body_chars,
        seen_doc_ids=seen_doc_ids,
    )
    macro_records, macro_errors = collect_live_macro_records(
        series_specs=select_macro_specs(macro_series),
        lookback_days=macro_lookback_days,
        max_observations=max_macro_observations,
        user_agent=user_agent,
        seen_doc_ids=seen_doc_ids,
    )
    company_records, company_errors = collect_live_company_ir_records(
        company_sources_path=company_sources_path,
        metadata_path=metadata_path,
        tickers=tickers,
        lookback_days=company_lookback_days,
        max_sources=max_company_sources,
        max_docs=max_company_docs,
        max_pages_per_source=max_company_pages_per_source,
        max_candidates_per_source=max_company_candidates_per_source,
        max_docs_per_source=max_company_docs_per_source,
        min_body_words=company_min_body_words,
        user_agent=user_agent,
        sleep_seconds=sleep_seconds,
        timeout_seconds=company_timeout_seconds,
        seen_doc_ids=seen_doc_ids,
    )
    raw_records = [*sec_records, *macro_records, *company_records]
    raw_records.sort(key=lambda row: (str(row.get("available_at", "")), str(row.get("doc_id", ""))))
    raw_appended = append_unique_jsonl(raw_output, raw_records)

    normalized = normalize_records(raw_records, metadata_path, source_registry_path)
    normalized = restore_live_extra_fields(raw_records, normalized)
    normalized = [
        row
        for row in normalized
        if str(row.get("document_hash", "") or "") not in seen_hashes
        or str(row.get("doc_id", "") or "") not in state.get("seen_doc_ids", [])
    ]
    processed_records = append_unique_records(processed_output, normalized)
    processed_appended = len(processed_records)
    # Hand the genuinely new documents to the streaming consumers as a NEW file:
    # Spark's file source never re-reads the live JSONL rewritten above.
    streaming_batch = emit_streaming_batch(streaming_inbox, processed_records) if streaming_inbox else None
    queued = append_llm_queue(queue_output, normalized)

    for row in normalized:
        doc_id = str(row.get("doc_id", "") or "")
        document_hash = str(row.get("document_hash", "") or "")
        if doc_id:
            seen_doc_ids.add(doc_id)
        if document_hash:
            seen_hashes.add(document_hash)
    run_summary = {
        "run_at": utc_now(),
        "raw_candidates": len(raw_records),
        "raw_appended": raw_appended,
        "processed_appended": processed_appended,
        "queued_for_llm": queued,
        "sec_errors": len(sec_errors),
        "macro_errors": len(macro_errors),
        "company_ir_errors": len(company_errors),
    }
    state["seen_doc_ids"] = sorted(seen_doc_ids)
    state["seen_document_hashes"] = sorted(seen_hashes)
    state.setdefault("runs", []).append(run_summary)
    state["runs"] = state["runs"][-50:]
    save_fetch_state(state_output, state)

    source_counts = Counter(str(row.get("source_type", "")) for row in normalized)
    return {
        "status": "completed",
        **run_summary,
        "raw_output": str(raw_output),
        "processed_output": str(processed_output),
        "llm_queue": str(queue_output),
        "state": str(state_output),
        "streaming_batch": str(streaming_batch) if streaming_batch else None,
        "source_type_counts": dict(source_counts),
        "errors": [*sec_errors, *macro_errors, *company_errors][:20],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch fresh official SEC/FRED evidence into the live IR layer.")
    parser.add_argument("--metadata", default=str(DEFAULT_METADATA))
    parser.add_argument("--source-registry", default=str(DEFAULT_SOURCE_REGISTRY))
    parser.add_argument("--raw-output", default=str(DEFAULT_RAW_OUTPUT))
    parser.add_argument("--processed-output", default=str(DEFAULT_PROCESSED_OUTPUT))
    parser.add_argument("--queue-output", default=str(DEFAULT_QUEUE_OUTPUT))
    parser.add_argument("--state-output", default=str(DEFAULT_STATE_OUTPUT))
    parser.add_argument("--tickers", default="", help="Comma-separated tickers. Empty means all tickers in metadata.")
    parser.add_argument("--forms", default="10-K,10-Q,8-K")
    parser.add_argument("--lookback-days", type=int, default=14)
    parser.add_argument("--max-sec-docs", type=int, default=80)
    parser.add_argument("--macro-series", default="", help="Comma-separated FRED series ids. Empty means default macro set.")
    parser.add_argument("--macro-lookback-days", type=int, default=14)
    parser.add_argument("--max-macro-observations", type=int, default=120)
    parser.add_argument("--company-sources", default=str(DEFAULT_COMPANY_SOURCES), help="Official company source registry CSV.")
    parser.add_argument("--company-lookback-days", type=int, default=30)
    parser.add_argument("--max-company-sources", type=int, default=10)
    parser.add_argument("--max-company-docs", type=int, default=20)
    parser.add_argument("--max-company-pages-per-source", type=int, default=1)
    parser.add_argument("--max-company-candidates-per-source", type=int, default=12)
    parser.add_argument("--max-company-docs-per-source", type=int, default=2)
    parser.add_argument("--company-min-body-words", type=int, default=80)
    parser.add_argument("--company-timeout-seconds", type=int, default=10)
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT)
    parser.add_argument("--sleep-seconds", type=float, default=0.15)
    parser.add_argument("--body-chars", type=int, default=16000)
    parser.add_argument("--streaming-inbox", default="",
                        help="Also drop each run's new documents here as a NEW JSONL file (Spark Structured Streaming).")
    parser.add_argument("--simulate-from", default="",
                        help="Skip the network: sample --simulate-count random documents from this corpus instead.")
    parser.add_argument("--simulate-count", type=int, default=12)
    parser.add_argument("--simulate-seed", type=int, default=0)
    args = parser.parse_args(argv)
    streaming_inbox = Path(args.streaming_inbox) if args.streaming_inbox else None
    if streaming_inbox is not None:
        assert_outside_inbox(streaming_inbox, Path(args.processed_output), Path(args.raw_output))

    if args.simulate_from:
        summary = simulate_live_fetch(
            corpus=Path(args.simulate_from),
            count=args.simulate_count,
            seed=args.simulate_seed,
            processed_output=Path(args.processed_output),
            streaming_inbox=streaming_inbox,
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    summary = fetch_live_incremental(
        metadata_path=Path(args.metadata),
        source_registry_path=Path(args.source_registry),
        raw_output=Path(args.raw_output),
        processed_output=Path(args.processed_output),
        queue_output=Path(args.queue_output),
        state_output=Path(args.state_output),
        tickers=[ticker.upper() for ticker in parse_csv_values(args.tickers)],
        forms={form.upper() for form in parse_csv_values(args.forms)},
        lookback_days=args.lookback_days,
        max_sec_docs=args.max_sec_docs,
        macro_series=[series.upper() for series in parse_csv_values(args.macro_series)],
        macro_lookback_days=args.macro_lookback_days,
        max_macro_observations=args.max_macro_observations,
        company_sources_path=Path(args.company_sources),
        company_lookback_days=args.company_lookback_days,
        max_company_sources=args.max_company_sources,
        max_company_docs=args.max_company_docs,
        max_company_pages_per_source=args.max_company_pages_per_source,
        max_company_candidates_per_source=args.max_company_candidates_per_source,
        max_company_docs_per_source=args.max_company_docs_per_source,
        company_min_body_words=args.company_min_body_words,
        company_timeout_seconds=args.company_timeout_seconds,
        user_agent=args.user_agent,
        sleep_seconds=args.sleep_seconds,
        body_chars=args.body_chars,
        streaming_inbox=streaming_inbox,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
