"""Collect a reproducible SEC EDGAR Dow 30 medium corpus.

This collector intentionally starts with official SEC filings. It is a corpus
backbone for route/retrieval testing, not a replacement for later company IR,
macro, and news ingestion.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from finportfolio_ir.dow30 import DOW30_COMPANIES  # noqa: E402
from finportfolio_ir.io_utils import write_jsonl  # noqa: E402


SEC_TICKER_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik10}.json"
SEC_ARCHIVES_URL = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession_no_dash}/{primary_doc}"
DEFAULT_USER_AGENT = "FinPortfolioIR/0.1 research contact local@example.invalid"
TARGET_FORMS = {"10-K", "10-Q", "8-K"}
TRAIN_START = date(2010, 1, 1)
TEST_START = date(2021, 10, 1)
TEST_END = date(2023, 3, 1)


@dataclass(frozen=True)
class FilingCandidate:
    ticker: str
    cik: str
    company_name: str
    sector: str
    accession_number: str
    filing_date: date
    report_date: str
    form: str
    primary_document: str


class SECTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "ix:header"}:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "ix:header"} and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            text = " ".join(data.split())
            if text:
                self.parts.append(text)

    def text(self) -> str:
        return " ".join(self.parts)


def _request_json(url: str, user_agent: str, timeout: int = 30) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": user_agent,
            "Accept": "application/json,text/plain,*/*",
            "Accept-Encoding": "identity",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _request_text_prefix(url: str, user_agent: str, max_bytes: int, timeout: int = 45) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": user_agent,
            "Accept": "text/html,text/plain,*/*",
            "Accept-Encoding": "identity",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read(max_bytes)
    return raw.decode("utf-8", errors="replace")


def _clean_filing_text(raw: str, max_chars: int) -> str:
    if "<" in raw and ">" in raw:
        parser = SECTextExtractor()
        try:
            parser.feed(raw)
            text = parser.text()
        except Exception:
            text = raw
    else:
        text = raw
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]


def load_sec_ticker_map(user_agent: str) -> dict[str, dict[str, str]]:
    payload = _request_json(SEC_TICKER_URL, user_agent)
    fields = payload.get("fields", [])
    mapping: dict[str, dict[str, str]] = {}
    for row in payload.get("data", []):
        item = {str(field): row[index] for index, field in enumerate(fields)}
        ticker = str(item.get("ticker", "")).upper()
        if ticker:
            mapping[ticker] = {
                "cik": str(item.get("cik", "")).zfill(10),
                "name": str(item.get("name", "")),
                "exchange": str(item.get("exchange", "")),
            }
    return mapping


def dow30_metadata_rows(sec_map: dict[str, dict[str, str]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for company in DOW30_COMPANIES:
        ticker = company["ticker"]
        sec = sec_map.get(ticker, {})
        name = sec.get("name") or company["name"]
        sector = company["sector"]
        common_name = re.sub(r"\b(inc\.?|corp\.?|corporation|company|co\.?|the)\b", "", company["name"], flags=re.I)
        common_name = re.sub(r"[, ]+", " ", common_name).strip()
        sector_risks = {
            "Financials": "credit risk|interest rates|capital requirements|deposits",
            "Information Technology": "AI demand|cloud|enterprise software|supply chain|valuation",
            "Health Care": "policy risk|medical costs|drug pricing|regulation",
            "Energy": "oil prices|refining margins|OPEC|commodity supply",
            "Consumer Discretionary": "consumer demand|labor costs|housing|margins",
            "Consumer Staples": "consumer demand|input costs|pricing power",
            "Industrials": "capex cycle|supply chain|orders|global demand",
            "Communication Services": "advertising demand|subscriber growth|regulation",
            "Materials": "input costs|construction demand|industrial demand",
        }.get(sector, "earnings|guidance|demand|margin")
        rows.append(
            {
                "ticker": ticker,
                "cik": sec.get("cik", ""),
                "official_name": company["name"],
                "company_name": name,
                "common_name": common_name,
                "sector": sector,
                "exchange": sec.get("exchange", ""),
                "active_from": "1900-01-01",
                "active_to": "",
                "peer_group": sector,
                "aliases": "|".join(sorted({ticker, f"${ticker}", common_name, company["name"], name})),
                "products": "",
                "risk_terms": sector_risks,
                "source_credibility": "0.8",
            }
        )
    rows.append(
        {
            "ticker": "MARKET",
            "cik": "",
            "official_name": "Market Macro",
            "company_name": "Market Macro",
            "common_name": "Market",
            "sector": "Macro",
            "exchange": "",
            "active_from": "1900-01-01",
            "active_to": "",
            "peer_group": "Macro",
            "aliases": "Fed|Federal Reserve|inflation|interest rates|recession|Treasury yields",
            "products": "market volatility|Treasury yields|rate decision",
            "risk_terms": "macro risk|rate risk|market volatility|geopolitics|credit stress",
            "source_credibility": "0.6",
        }
    )
    return rows


def write_ticker_metadata(path: Path, rows: list[dict[str, str]]) -> None:
    fieldnames = [
        "ticker",
        "cik",
        "official_name",
        "company_name",
        "common_name",
        "sector",
        "exchange",
        "active_from",
        "active_to",
        "peer_group",
        "aliases",
        "products",
        "risk_terms",
        "source_credibility",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _parse_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _filings_from_recent(ticker: str, cik: str, company_name: str, sector: str, recent: dict[str, list[Any]]) -> list[FilingCandidate]:
    filings: list[FilingCandidate] = []
    accession_numbers = recent.get("accessionNumber", [])
    forms = recent.get("form", [])
    filing_dates = recent.get("filingDate", [])
    report_dates = recent.get("reportDate", [])
    primary_documents = recent.get("primaryDocument", [])
    for index, accession in enumerate(accession_numbers):
        form = str(forms[index] if index < len(forms) else "")
        filing_date = _parse_date(filing_dates[index] if index < len(filing_dates) else "")
        primary_doc = str(primary_documents[index] if index < len(primary_documents) else "")
        if not filing_date or form not in TARGET_FORMS or not primary_doc:
            continue
        if filing_date < TRAIN_START or filing_date >= TEST_END:
            continue
        filings.append(
            FilingCandidate(
                ticker=ticker,
                cik=cik,
                company_name=company_name,
                sector=sector,
                accession_number=str(accession),
                filing_date=filing_date,
                report_date=str(report_dates[index] if index < len(report_dates) else ""),
                form=form,
                primary_document=primary_doc,
            )
        )
    return filings


def fetch_company_filings(ticker: str, cik10: str, company_name: str, sector: str, user_agent: str, delay_seconds: float) -> list[FilingCandidate]:
    payload = _request_json(SEC_SUBMISSIONS_URL.format(cik10=cik10), user_agent)
    filings = _filings_from_recent(ticker, cik10, company_name, sector, payload.get("filings", {}).get("recent", {}))
    for file_info in payload.get("filings", {}).get("files", []) or []:
        name = file_info.get("name")
        if not name:
            continue
        time.sleep(delay_seconds)
        archive_url = f"https://data.sec.gov/submissions/{name}"
        archive_payload = _request_json(archive_url, user_agent)
        filings.extend(_filings_from_recent(ticker, cik10, company_name, sector, archive_payload))
    filings = sorted({(item.accession_number, item.form): item for item in filings}.values(), key=lambda item: item.filing_date)
    return filings


def _spread_select(candidates: list[FilingCandidate], count: int, preferred_forms: list[str]) -> list[FilingCandidate]:
    selected: list[FilingCandidate] = []
    selected_keys: set[str] = set()
    for form in preferred_forms:
        form_candidates = [item for item in candidates if item.form == form and item.accession_number not in selected_keys]
        if form_candidates:
            index = min(len(form_candidates) - 1, round((len(selected) + 1) * (len(form_candidates) - 1) / max(count, 1)))
            item = form_candidates[index]
            selected.append(item)
            selected_keys.add(item.accession_number)
        if len(selected) >= count:
            return sorted(selected, key=lambda item: item.filing_date)
    remaining = [item for item in candidates if item.accession_number not in selected_keys]
    while remaining and len(selected) < count:
        index = round((len(selected) + 1) * (len(remaining) - 1) / max(count, 1))
        item = remaining.pop(index)
        selected.append(item)
        selected_keys.add(item.accession_number)
    return sorted(selected, key=lambda item: item.filing_date)


def select_balanced_filings(filings: list[FilingCandidate], train_per_ticker: int, test_per_ticker: int) -> list[FilingCandidate]:
    train = [item for item in filings if item.filing_date < TEST_START]
    test = [item for item in filings if TEST_START <= item.filing_date < TEST_END]
    selected_train = _spread_select(train, train_per_ticker, ["10-K", "10-Q", "8-K", "10-Q", "8-K", "10-Q", "10-K"])
    selected_test = _spread_select(test, test_per_ticker, ["10-K", "10-Q", "8-K"])
    return selected_train + selected_test


def _sec_url(filing: FilingCandidate) -> str:
    return SEC_ARCHIVES_URL.format(
        cik_int=int(filing.cik),
        accession_no_dash=filing.accession_number.replace("-", ""),
        primary_doc=filing.primary_document,
    )


def _iso_end_of_filing_day(filing_date: date) -> str:
    return f"{filing_date.isoformat()}T23:59:59Z"


def _body_from_filing(filing: FilingCandidate, text: str) -> str:
    header = (
        f"{filing.company_name} ({filing.ticker}) filed SEC form {filing.form} "
        f"on {filing.filing_date.isoformat()} for report period {filing.report_date or 'unknown'}. "
        f"This official filing may contain risk factors, MD&A, financial statements, earnings context, "
        f"capital allocation, liquidity, credit, margin, revenue, and regulatory disclosures. "
    )
    return f"{header}\n\n{text}".strip()


def filing_to_record(
    filing: FilingCandidate,
    *,
    sequence: int,
    user_agent: str,
    max_download_bytes: int,
    max_body_chars: int,
) -> dict[str, Any]:
    url = _sec_url(filing)
    try:
        raw = _request_text_prefix(url, user_agent, max_download_bytes)
        body_text = _clean_filing_text(raw, max_body_chars)
        fetch_status = "ok"
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        body_text = f"Unable to fetch primary document text: {exc}"
        fetch_status = "metadata_only"
    available_at = _iso_end_of_filing_day(filing.filing_date)
    split = "train" if filing.filing_date < TEST_START else "test"
    return {
        "doc_id": f"sec_{sequence:06d}",
        "title": f"{filing.ticker} {filing.form} filed {filing.filing_date.isoformat()}",
        "body": _body_from_filing(filing, body_text),
        "source": "sec_edgar",
        "source_type": "sec_filing",
        "url": url,
        "source_registry_id": "sec_edgar",
        "canonical_url": url,
        "source_reliability_tier": "official",
        "robots_policy": "Use SEC APIs/Archives with descriptive User-Agent and fair-access rate limits.",
        "last_url_check_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "fetch_status": fetch_status,
        "content_license_note": "Public SEC filing; preserve accession metadata and source URL.",
        "published_at": available_at,
        "first_seen_at": available_at,
        "available_at": available_at,
        "ingested_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "version_id": filing.accession_number,
        "is_revision": False,
        "revision_of": "",
        "tickers_detected": [filing.ticker],
        "matched_tickers": [filing.ticker],
        "matched_holdings": [filing.ticker],
        "company_names_detected": [filing.company_name],
        "sectors_detected": [filing.sector],
        "sector_tags": [filing.sector],
        "event_tags": ["filing", filing.form.lower().replace("-", "")],
        "risk_terms": [],
        "sentiment_score": 0.0,
        "uncertainty_score": 0.0,
        "source_credibility": 0.95,
        "event_type": "sec_filing",
        "language": "en",
        "sec_form": filing.form,
        "sec_accession_number": filing.accession_number,
        "sec_report_date": filing.report_date,
        "split": split,
    }


def collect_sec_dow30_records(
    *,
    user_agent: str,
    train_per_ticker: int,
    test_per_ticker: int,
    delay_seconds: float,
    max_download_bytes: int,
    max_body_chars: int,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    sec_map = load_sec_ticker_map(user_agent)
    metadata_rows = dow30_metadata_rows(sec_map)
    sequence = 1
    records: list[dict[str, Any]] = []
    for company in DOW30_COMPANIES:
        ticker = company["ticker"]
        meta = next(row for row in metadata_rows if row["ticker"] == ticker)
        cik = meta["cik"]
        if not cik:
            continue
        filings = fetch_company_filings(ticker, cik, meta["company_name"], meta["sector"], user_agent, delay_seconds)
        selected = select_balanced_filings(filings, train_per_ticker, test_per_ticker)
        for filing in selected:
            time.sleep(delay_seconds)
            records.append(
                filing_to_record(
                    filing,
                    sequence=sequence,
                    user_agent=user_agent,
                    max_download_bytes=max_download_bytes,
                    max_body_chars=max_body_chars,
                )
            )
            sequence += 1
    return records, metadata_rows


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Collect a balanced SEC Dow 30 medium corpus.")
    parser.add_argument("--output", default="data/raw_documents/sec_dow30_2010_2023_raw.jsonl")
    parser.add_argument("--metadata-output", default="data/processed_documents/dow30_sec_ticker_metadata.csv")
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT)
    parser.add_argument("--train-per-ticker", type=int, default=7)
    parser.add_argument("--test-per-ticker", type=int, default=3)
    parser.add_argument("--delay-seconds", type=float, default=0.12)
    parser.add_argument("--max-download-bytes", type=int, default=700_000)
    parser.add_argument("--max-body-chars", type=int, default=80_000)
    args = parser.parse_args(argv)

    records, metadata_rows = collect_sec_dow30_records(
        user_agent=args.user_agent,
        train_per_ticker=args.train_per_ticker,
        test_per_ticker=args.test_per_ticker,
        delay_seconds=args.delay_seconds,
        max_download_bytes=args.max_download_bytes,
        max_body_chars=args.max_body_chars,
    )
    write_jsonl(args.output, records)
    write_ticker_metadata(Path(args.metadata_output), metadata_rows)
    split_counts = {
        "train": sum(1 for row in records if row.get("split") == "train"),
        "test": sum(1 for row in records if row.get("split") == "test"),
    }
    print(json.dumps({"records": len(records), "split_counts": split_counts, "output": args.output}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
