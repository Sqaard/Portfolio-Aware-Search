"""Collect a stratified SEC EDGAR filings corpus for FinPortfolio IR.

The collector uses official SEC submission JSON metadata and primary filing
documents. It is intentionally conservative: deterministic ticker/split/form
sampling, descriptive User-Agent, small sleep between HTTP requests, and raw
JSONL output that then goes through the normal FinIR normalization pipeline.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from finportfolio_ir.io_utils import write_jsonl


SEC_SUBMISSIONS_BASE = "https://data.sec.gov/submissions/"
SEC_ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data/"
DEFAULT_FORMS = ("10-K", "10-Q", "8-K")
DEFAULT_TRAIN_END = "2021-10-01"
DEFAULT_TEST_END = "2023-03-01"
DEFAULT_BODY_CHARS = 12000


@dataclass(frozen=True)
class TickerCik:
    ticker: str
    cik: str
    company_name: str
    sector: str


@dataclass(frozen=True)
class FilingMeta:
    ticker: str
    cik: str
    company_name: str
    sector: str
    accession: str
    form: str
    filing_date: str
    report_date: str
    accepted_at: str
    primary_document: str
    split: str

    @property
    def doc_id(self) -> str:
        accession_safe = self.accession.replace("-", "")
        return f"sec_{self.ticker.lower()}_{self.form.lower().replace('-', '')}_{accession_safe}"

    @property
    def archive_url(self) -> str:
        accession_safe = self.accession.replace("-", "")
        cik_int = str(int(self.cik))
        return f"{SEC_ARCHIVES_BASE}{cik_int}/{accession_safe}/{self.primary_document}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_date(value: str) -> datetime:
    return datetime.fromisoformat(value[:10]).replace(tzinfo=timezone.utc)


def _iso_from_sec_datetime(value: str, fallback_date: str) -> str:
    text = str(value or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?", text):
        return f"{text.split('.')[0]}Z"
    return f"{fallback_date}T16:00:00Z"


def _load_tickers(path: str | Path, requested: Iterable[str] | None = None) -> list[TickerCik]:
    requested_set = {ticker.upper() for ticker in requested or []}
    rows: list[TickerCik] = []
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            ticker = str(row.get("ticker", "")).upper().strip()
            cik = str(row.get("cik", "")).strip()
            if not ticker or ticker == "MARKET" or not cik:
                continue
            if requested_set and ticker not in requested_set:
                continue
            rows.append(
                TickerCik(
                    ticker=ticker,
                    cik=cik.zfill(10),
                    company_name=str(row.get("official_name") or row.get("company_name") or ticker),
                    sector=str(row.get("sector") or ""),
                )
            )
    return rows


def _request_json(url: str, user_agent: str, sleep_seconds: float) -> dict[str, Any]:
    time.sleep(max(0.0, sleep_seconds))
    request = urllib.request.Request(url, headers={"User-Agent": user_agent, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _request_text(url: str, user_agent: str, sleep_seconds: float, max_bytes: int = 2_500_000) -> str:
    time.sleep(max(0.0, sleep_seconds))
    request = urllib.request.Request(url, headers={"User-Agent": user_agent, "Accept": "text/html,text/plain,*/*"})
    with urllib.request.urlopen(request, timeout=45) as response:
        raw = response.read(max_bytes)
    return raw.decode("utf-8", errors="replace")


def _submission_blocks(
    payload: dict[str, Any],
    user_agent: str,
    sleep_seconds: float,
    *,
    start_date: str,
    test_end: str,
) -> list[dict[str, Any]]:
    blocks = [payload.get("filings", {}).get("recent", {})]
    for file_row in payload.get("filings", {}).get("files", []) or []:
        name = str(file_row.get("name", "")).strip()
        if not name:
            continue
        filing_from = str(file_row.get("filingFrom", "") or "")
        filing_to = str(file_row.get("filingTo", "") or "")
        if filing_to and filing_to < start_date:
            continue
        if filing_from and filing_from >= test_end:
            continue
        blocks.append(_request_json(f"{SEC_SUBMISSIONS_BASE}{name}", user_agent, sleep_seconds))
    return [block for block in blocks if isinstance(block, dict) and block.get("accessionNumber")]


def _filing_rows(block: dict[str, Any]) -> list[dict[str, Any]]:
    accessions = block.get("accessionNumber", []) or []
    rows: list[dict[str, Any]] = []
    for index, accession in enumerate(accessions):
        row = {}
        for key, values in block.items():
            if isinstance(values, list) and index < len(values):
                row[key] = values[index]
        row["accessionNumber"] = accession
        rows.append(row)
    return rows


def _collect_candidate_meta(
    ticker: TickerCik,
    *,
    user_agent: str,
    sleep_seconds: float,
    forms: set[str],
    start_date: str,
    train_end: str,
    test_end: str,
) -> list[FilingMeta]:
    payload = _request_json(f"{SEC_SUBMISSIONS_BASE}CIK{ticker.cik}.json", user_agent, sleep_seconds)
    metas: list[FilingMeta] = []
    for block in _submission_blocks(payload, user_agent, sleep_seconds, start_date=start_date, test_end=test_end):
        for row in _filing_rows(block):
            form = str(row.get("form", "")).upper()
            filing_date = str(row.get("filingDate", "") or "")[:10]
            primary = str(row.get("primaryDocument", "") or "").strip()
            accession = str(row.get("accessionNumber", "") or "").strip()
            if form not in forms or not filing_date or not primary or not accession:
                continue
            if filing_date < start_date or filing_date >= test_end:
                continue
            split = "train" if filing_date < train_end else "test"
            accepted_at = _iso_from_sec_datetime(str(row.get("acceptanceDateTime", "")), filing_date)
            metas.append(
                FilingMeta(
                    ticker=ticker.ticker,
                    cik=ticker.cik,
                    company_name=ticker.company_name,
                    sector=ticker.sector,
                    accession=accession,
                    form=form,
                    filing_date=filing_date,
                    report_date=str(row.get("reportDate", "") or ""),
                    accepted_at=accepted_at,
                    primary_document=primary,
                    split=split,
                )
            )
    metas.sort(key=lambda item: (item.filing_date, item.form, item.accession))
    return metas


def _evenly_pick(items: list[FilingMeta], count: int) -> list[FilingMeta]:
    if count <= 0 or not items:
        return []
    if len(items) <= count:
        return list(items)
    if count == 1:
        return [items[len(items) // 2]]
    picked: list[FilingMeta] = []
    used: set[int] = set()
    for slot in range(count):
        index = round(slot * (len(items) - 1) / (count - 1))
        while index in used and index + 1 < len(items):
            index += 1
        while index in used and index > 0:
            index -= 1
        used.add(index)
        picked.append(items[index])
    picked.sort(key=lambda item: (item.filing_date, item.form, item.accession))
    return picked


def _stratified_pick(
    candidates: list[FilingMeta],
    *,
    train_per_ticker: int,
    test_per_ticker: int,
) -> list[FilingMeta]:
    selected: list[FilingMeta] = []
    for split, count in (("train", train_per_ticker), ("test", test_per_ticker)):
        split_items = [item for item in candidates if item.split == split]
        quotas = {
            "10-K": max(1, round(count * 0.22)),
            "10-Q": max(1, round(count * 0.45)),
        }
        quotas["8-K"] = max(0, count - quotas["10-K"] - quotas["10-Q"])
        split_selected: list[FilingMeta] = []
        for form in ("10-K", "10-Q", "8-K"):
            split_selected.extend(_evenly_pick([item for item in split_items if item.form == form], quotas[form]))
        if len(split_selected) < count:
            selected_keys = {(item.accession, item.form) for item in split_selected}
            remaining = [item for item in split_items if (item.accession, item.form) not in selected_keys]
            split_selected.extend(_evenly_pick(remaining, count - len(split_selected)))
        selected.extend(sorted(split_selected, key=lambda item: (item.filing_date, item.form, item.accession))[:count])
    return selected


def _html_to_text(raw: str) -> str:
    text = re.sub(r"(?is)<(script|style|ix:header).*?</\1>", " ", raw)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _event_tags(form: str) -> list[str]:
    if form == "10-K":
        return ["annual_report", "filing", "fundamentals"]
    if form == "10-Q":
        return ["quarterly_report", "filing", "fundamentals"]
    return ["current_report", "filing", "company_event"]


def build_raw_record(meta: FilingMeta, body: str, *, ingested_at: str, body_chars: int) -> dict[str, Any]:
    title = f"{meta.company_name} {meta.form} filing filed {meta.filing_date}"
    return {
        "doc_id": meta.doc_id,
        "title": title,
        "body": body[:body_chars],
        "source": "SEC EDGAR",
        "source_type": "sec_filing",
        "source_registry_id": "sec_edgar",
        "source_reliability_tier": "official",
        "robots_policy": "Use SEC APIs/RSS and respect SEC fair-access guidance.",
        "content_license_note": "Public SEC filing; preserve accession and source URL.",
        "source_credibility": 0.95,
        "url": meta.archive_url,
        "canonical_url": meta.archive_url,
        "published_at": meta.accepted_at,
        "first_seen_at": meta.accepted_at,
        "available_at": meta.accepted_at,
        "ingested_at": ingested_at,
        "last_url_check_at": ingested_at,
        "fetch_status": "ok",
        "version_id": meta.accession,
        "duplicate_cluster_id": meta.accession.replace("-", ""),
        "tickers_detected": [meta.ticker],
        "matched_tickers": [meta.ticker],
        "matched_holdings": [meta.ticker],
        "company_names_detected": [meta.company_name],
        "sectors_detected": [meta.sector] if meta.sector else [],
        "sector_tags": [meta.sector] if meta.sector else [],
        "event_tags": _event_tags(meta.form),
        "risk_terms": [],
        "event_type": "filing",
        "language": "en",
        "sec": {
            "ticker": meta.ticker,
            "cik": meta.cik,
            "accession": meta.accession,
            "form": meta.form,
            "filing_date": meta.filing_date,
            "report_date": meta.report_date,
            "primary_document": meta.primary_document,
        },
        "split": meta.split,
    }


def collect_sec_corpus(
    *,
    metadata_path: str | Path,
    output_path: str | Path,
    tickers: list[str],
    target_docs: int,
    train_ratio: float,
    start_date: str,
    train_end: str,
    test_end: str,
    forms: set[str],
    user_agent: str,
    sleep_seconds: float,
    body_chars: int,
    fetch_workers: int = 6,
) -> list[dict[str, Any]]:
    ticker_rows = _load_tickers(metadata_path, tickers)
    if not ticker_rows:
        raise ValueError("No tickers with CIK were found in metadata.")
    per_ticker = target_docs // len(ticker_rows)
    remainder = target_docs % len(ticker_rows)
    ingested_at = _utc_now()
    selected_all: list[FilingMeta] = []

    for index, ticker in enumerate(ticker_rows):
        ticker_target = per_ticker + (1 if index < remainder else 0)
        train_count = round(ticker_target * train_ratio)
        test_count = ticker_target - train_count
        candidates = _collect_candidate_meta(
            ticker,
            user_agent=user_agent,
            sleep_seconds=sleep_seconds,
            forms=forms,
            start_date=start_date,
            train_end=train_end,
            test_end=test_end,
        )
        selected_all.extend(_stratified_pick(candidates, train_per_ticker=train_count, test_per_ticker=test_count))

    def fetch_record(meta: FilingMeta) -> dict[str, Any] | None:
        try:
            raw = _request_text(meta.archive_url, user_agent, sleep_seconds)
            body = _html_to_text(raw)
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            print(f"skip {meta.ticker} {meta.form} {meta.accession}: {exc}", file=sys.stderr)
            return None
        if len(body) < 500:
            return None
        return build_raw_record(meta, body, ingested_at=ingested_at, body_chars=body_chars)

    records: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, fetch_workers)) as executor:
        futures = [executor.submit(fetch_record, meta) for meta in selected_all]
        for future in as_completed(futures):
            record = future.result()
            if record is not None:
                records.append(record)
    records.sort(key=lambda record: (record["split"], record["sec"]["ticker"], record["available_at"], record["doc_id"]))
    write_jsonl(output_path, records[:target_docs])
    return records[:target_docs]


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Collect SEC filings for the FinPortfolio IR medium corpus.")
    parser.add_argument("--metadata", default="data/processed_documents/ticker_metadata.csv")
    parser.add_argument("--output", default="data/raw_documents/sec_dow_sample_2010_2023_300.jsonl")
    parser.add_argument("--tickers", default="", help="Comma-separated tickers. Default: all metadata tickers with CIK except MARKET.")
    parser.add_argument("--target-docs", type=int, default=300)
    parser.add_argument("--train-ratio", type=float, default=0.75)
    parser.add_argument("--start-date", default="2010-01-01")
    parser.add_argument("--train-end", default=DEFAULT_TRAIN_END)
    parser.add_argument("--test-end", default=DEFAULT_TEST_END)
    parser.add_argument("--forms", default=",".join(DEFAULT_FORMS))
    parser.add_argument("--sleep-seconds", type=float, default=0.12)
    parser.add_argument("--body-chars", type=int, default=DEFAULT_BODY_CHARS)
    parser.add_argument("--fetch-workers", type=int, default=6)
    parser.add_argument(
        "--user-agent",
        default=os.environ.get("SEC_USER_AGENT", "FinPortfolioIR/0.1 academic research contact=ivanp@example.com"),
    )
    args = parser.parse_args(argv)

    tickers = [ticker.strip().upper() for ticker in args.tickers.split(",") if ticker.strip()]
    forms = {form.strip().upper() for form in args.forms.split(",") if form.strip()}
    records = collect_sec_corpus(
        metadata_path=args.metadata,
        output_path=args.output,
        tickers=tickers,
        target_docs=args.target_docs,
        train_ratio=args.train_ratio,
        start_date=args.start_date,
        train_end=args.train_end,
        test_end=args.test_end,
        forms=forms,
        user_agent=args.user_agent,
        sleep_seconds=args.sleep_seconds,
        body_chars=args.body_chars,
        fetch_workers=args.fetch_workers,
    )
    split_counts: dict[str, int] = {}
    ticker_counts: dict[str, int] = {}
    for record in records:
        split_counts[str(record.get("split", ""))] = split_counts.get(str(record.get("split", "")), 0) + 1
        ticker = str(record.get("sec", {}).get("ticker", ""))
        ticker_counts[ticker] = ticker_counts.get(ticker, 0) + 1
    print(json.dumps({"output": args.output, "rows": len(records), "split_counts": split_counts, "ticker_counts": ticker_counts}, indent=2))
    return 0 if len(records) == args.target_docs else 1


if __name__ == "__main__":
    raise SystemExit(main())
