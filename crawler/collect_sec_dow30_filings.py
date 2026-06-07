"""Collect a reproducible SEC EDGAR Dow 30 filings corpus.

The collector intentionally uses official SEC JSON endpoints and filing URLs.
It samples filings deterministically across tickers and time splits, producing
raw FinPortfolio IR JSONL plus expanded ticker metadata.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from finportfolio_ir.dow30 import DOW30_COMPANIES  # noqa: E402
from finportfolio_ir.io_utils import write_jsonl  # noqa: E402


SEC_COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik10}.json"
SEC_ARCHIVES_URL = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession_no_dash}/{primary_document}"
TRAIN_START = "2010-01-01"
TRAIN_END = "2021-10-01"
TEST_END = "2023-03-01"
DEFAULT_FORMS = ("10-K", "10-Q", "8-K")


@dataclass(frozen=True)
class FilingCandidate:
    ticker: str
    cik: str
    company_name: str
    sector: str
    form: str
    accession_number: str
    filing_date: str
    report_date: str
    accepted_at: str
    primary_document: str
    split: str

    @property
    def url(self) -> str:
        return SEC_ARCHIVES_URL.format(
            cik_int=str(int(self.cik)),
            accession_no_dash=self.accession_number.replace("-", ""),
            primary_document=self.primary_document,
        )


def _request_json(url: str, user_agent: str, pause_seconds: float) -> Any:
    time.sleep(pause_seconds)
    request = urllib.request.Request(url, headers={"User-Agent": user_agent, "Accept-Encoding": "identity"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _request_text(url: str, user_agent: str, pause_seconds: float, max_bytes: int) -> str:
    time.sleep(pause_seconds)
    request = urllib.request.Request(url, headers={"User-Agent": user_agent, "Accept-Encoding": "identity"})
    with urllib.request.urlopen(request, timeout=45) as response:
        raw = response.read(max_bytes)
    return raw.decode("utf-8", errors="replace")


def _accepted_to_iso(value: str, fallback_date: str) -> str:
    text = str(value or "").strip()
    if text:
        text = text.replace("Z", "")
        if "." in text:
            text = text.split(".", 1)[0]
        try:
            return datetime.fromisoformat(text).replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
        except ValueError:
            pass
    return f"{fallback_date}T21:00:00Z"


def _split_for_available_at(available_at: str) -> str | None:
    stamp = available_at[:10]
    if TRAIN_START <= stamp < TRAIN_END:
        return "train"
    if TRAIN_END <= stamp <= TEST_END:
        return "test"
    return None


def _strip_sec_html(text: str, max_chars: int) -> str:
    text = re.sub(r"(?is)<script.*?</script>", " ", text)
    text = re.sub(r"(?is)<style.*?</style>", " ", text)
    text = re.sub(r"(?is)<ix:header.*?</ix:header>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars].rstrip()


def _event_type(form: str) -> str:
    if form == "10-K":
        return "annual_report"
    if form == "10-Q":
        return "quarterly_report"
    if form == "8-K":
        return "current_report"
    return "sec_filing"


def _event_tags(form: str) -> list[str]:
    if form == "10-K":
        return ["filing", "annual_report", "risk_factors"]
    if form == "10-Q":
        return ["filing", "quarterly_report", "earnings"]
    if form == "8-K":
        return ["filing", "current_report", "company_event"]
    return ["filing"]


def _base_aliases(name: str, ticker: str) -> str:
    cleaned = re.sub(r"\b(inc\.?|corporation|corp\.?|company|co\.?|the)\b", "", name, flags=re.IGNORECASE)
    aliases = [ticker, f"${ticker}", name, cleaned.strip(" ,.")]
    return "|".join(dict.fromkeys(alias for alias in aliases if alias))


def _metadata_rows(sec_tickers: dict[str, dict[str, Any]]) -> list[dict[str, str]]:
    rows = []
    for company in DOW30_COMPANIES:
        ticker = company["ticker"]
        sec_row = sec_tickers[ticker]
        official_name = str(sec_row["title"])
        rows.append(
            {
                "ticker": ticker,
                "cik": f"{int(sec_row['cik_str']):010d}",
                "official_name": official_name,
                "company_name": official_name,
                "common_name": company["name"].split(",")[0],
                "sector": company["sector"],
                "exchange": "",
                "active_from": "1900-01-01",
                "active_to": "",
                "peer_group": company["sector"],
                "aliases": _base_aliases(official_name, ticker),
                "products": "",
                "risk_terms": "earnings|revenue|guidance|margin|cash flow|regulation|litigation|supply chain|interest rates|inflation",
                "source_credibility": "0.9",
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
            "products": "",
            "risk_terms": "macro risk|rate risk|market volatility|credit stress",
            "source_credibility": "0.7",
        }
    )
    return rows


def write_metadata(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def sec_ticker_map(user_agent: str, pause_seconds: float) -> dict[str, dict[str, Any]]:
    payload = _request_json(SEC_COMPANY_TICKERS_URL, user_agent, pause_seconds)
    by_ticker = {str(row["ticker"]).upper(): row for row in payload.values()}
    missing = [company["ticker"] for company in DOW30_COMPANIES if company["ticker"] not in by_ticker]
    if missing:
        raise RuntimeError(f"SEC ticker map missing Dow 30 tickers: {missing}")
    return by_ticker


def _rows_from_submissions(data: dict[str, Any], ticker: str, cik: str, company_name: str, sector: str, forms: set[str]) -> list[FilingCandidate]:
    filings = data.get("filings", {}).get("recent", {})
    accession_numbers = filings.get("accessionNumber", []) or []
    output = []
    for index, accession in enumerate(accession_numbers):
        form = str((filings.get("form", []) or [""])[index])
        if form not in forms:
            continue
        filing_date = str((filings.get("filingDate", []) or [""])[index])
        if not filing_date:
            continue
        accepted_at = _accepted_to_iso(str((filings.get("acceptanceDateTime", []) or [""])[index]), filing_date)
        split = _split_for_available_at(accepted_at)
        if split is None:
            continue
        primary_document = str((filings.get("primaryDocument", []) or [""])[index])
        if not primary_document:
            continue
        output.append(
            FilingCandidate(
                ticker=ticker,
                cik=cik,
                company_name=company_name,
                sector=sector,
                form=form,
                accession_number=str(accession),
                filing_date=filing_date,
                report_date=str((filings.get("reportDate", []) or [""])[index]),
                accepted_at=accepted_at,
                primary_document=primary_document,
                split=split,
            )
        )
    return output


def filing_candidates_for_ticker(
    ticker: str,
    sec_row: dict[str, Any],
    sector: str,
    forms: set[str],
    user_agent: str,
    pause_seconds: float,
) -> list[FilingCandidate]:
    cik = f"{int(sec_row['cik_str']):010d}"
    company_name = str(sec_row["title"])
    main = _request_json(SEC_SUBMISSIONS_URL.format(cik10=cik), user_agent, pause_seconds)
    candidates = _rows_from_submissions(main, ticker, cik, company_name, sector, forms)
    for file_info in main.get("filings", {}).get("files", []) or []:
        name = str(file_info.get("name", ""))
        if not name:
            continue
        try:
            older = _request_json(f"https://data.sec.gov/submissions/{name}", user_agent, pause_seconds)
        except (urllib.error.URLError, TimeoutError):
            continue
        candidates.extend(_rows_from_submissions({"filings": {"recent": older}}, ticker, cik, company_name, sector, forms))
    candidates = list({candidate.accession_number: candidate for candidate in candidates}.values())
    candidates.sort(key=lambda item: (item.accepted_at, item.form, item.accession_number))
    return candidates


def _evenly_spaced(candidates: list[FilingCandidate], limit: int) -> list[FilingCandidate]:
    if len(candidates) <= limit:
        return list(candidates)
    if limit <= 1:
        return [candidates[-1]]
    indices = [round(i * (len(candidates) - 1) / (limit - 1)) for i in range(limit)]
    selected = []
    seen = set()
    for index in indices:
        if index not in seen:
            selected.append(candidates[index])
            seen.add(index)
    cursor = len(candidates) - 1
    while len(selected) < limit and cursor >= 0:
        if cursor not in seen:
            selected.append(candidates[cursor])
            seen.add(cursor)
        cursor -= 1
    selected.sort(key=lambda item: item.accepted_at)
    return selected[:limit]


def select_candidates(
    by_ticker: dict[str, list[FilingCandidate]],
    train_per_ticker: int,
    test_per_ticker: int,
) -> list[FilingCandidate]:
    selected = []
    for company in DOW30_COMPANIES:
        ticker = company["ticker"]
        candidates = by_ticker[ticker]
        train_candidates = [candidate for candidate in candidates if candidate.split == "train"]
        test_candidates = [candidate for candidate in candidates if candidate.split == "test"]
        if len(train_candidates) < train_per_ticker or len(test_candidates) < test_per_ticker:
            raise RuntimeError(
                f"Not enough SEC filings for {ticker}: train={len(train_candidates)}, test={len(test_candidates)}"
            )
        selected.extend(_evenly_spaced(train_candidates, train_per_ticker))
        selected.extend(_evenly_spaced(test_candidates, test_per_ticker))
    selected.sort(key=lambda item: (item.split, item.ticker, item.accepted_at))
    return selected


def build_raw_documents(
    candidates: Iterable[FilingCandidate],
    user_agent: str,
    pause_seconds: float,
    max_body_chars: int,
    max_download_bytes: int,
    existing_accessions: set[str] | None = None,
) -> list[dict[str, Any]]:
    records = []
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    for index, candidate in enumerate(candidates, start=1):
        if existing_accessions and candidate.accession_number in existing_accessions:
            continue
        try:
            raw_text = _request_text(candidate.url, user_agent, pause_seconds, max_download_bytes)
            body = _strip_sec_html(raw_text, max_body_chars)
            fetch_status = "ok"
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            body = f"SEC filing fetch failed for {candidate.ticker} {candidate.form} {candidate.filing_date}: {exc}"
            fetch_status = "fetch_error"
        title = f"{candidate.ticker} {candidate.form} filing accepted {candidate.accepted_at[:10]} ({candidate.company_name})"
        records.append(
            {
                "doc_id": f"sec_dow30_{index:04d}_{candidate.ticker}_{candidate.form.replace('-', '')}_{candidate.accession_number.replace('-', '')}",
                "title": title,
                "body": body,
                "source": "sec_edgar",
                "source_type": "sec_filing",
                "url": candidate.url,
                "source_registry_id": "sec_edgar",
                "canonical_url": candidate.url,
                "source_reliability_tier": "tier_1_official",
                "robots_policy": "SEC fair access policy; official EDGAR archive URL",
                "last_url_check_at": now,
                "fetch_status": fetch_status,
                "content_license_note": "Public SEC EDGAR filing; retain source URL and access timestamp.",
                "published_at": candidate.accepted_at,
                "first_seen_at": candidate.accepted_at,
                "available_at": candidate.accepted_at,
                "ingested_at": now,
                "version_id": candidate.accession_number,
                "duplicate_cluster_id": candidate.accession_number,
                "tickers_detected": [candidate.ticker],
                "matched_tickers": [candidate.ticker],
                "matched_holdings": [candidate.ticker],
                "company_names_detected": [candidate.company_name],
                "sectors_detected": [candidate.sector],
                "sector_tags": [candidate.sector],
                "event_tags": _event_tags(candidate.form),
                "risk_terms": [],
                "source_credibility": 0.95,
                "event_type": _event_type(candidate.form),
                "language": "en",
                "sec_form": candidate.form,
                "sec_accession_number": candidate.accession_number,
                "sec_filing_date": candidate.filing_date,
                "sec_report_date": candidate.report_date,
                "split": candidate.split,
            }
        )
    return records


def _existing_accessions(path: Path) -> set[str]:
    if not path.exists():
        return set()
    accessions = set()
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            accession = str(record.get("sec_accession_number", ""))
            if accession:
                accessions.add(accession)
    return accessions


def append_raw_documents(
    output_path: Path,
    candidates: list[FilingCandidate],
    user_agent: str,
    pause_seconds: float,
    max_body_chars: int,
    max_download_bytes: int,
    resume: bool,
) -> list[dict[str, Any]]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    existing = _existing_accessions(output_path) if resume else set()
    if not resume and output_path.exists():
        output_path.unlink()
    mode = "a" if resume else "w"
    records = []
    with output_path.open(mode, encoding="utf-8") as handle:
        for index, candidate in enumerate(candidates, start=1):
            if candidate.accession_number in existing:
                continue
            record = build_raw_documents(
                [candidate],
                user_agent=user_agent,
                pause_seconds=pause_seconds,
                max_body_chars=max_body_chars,
                max_download_bytes=max_download_bytes,
            )[0]
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            records.append(record)
            if len(records) % 10 == 0:
                print(f"downloaded {len(records)} new filings ({index}/{len(candidates)} selected)", flush=True)
    if resume and output_path.exists():
        from finportfolio_ir.io_utils import read_jsonl

        return read_jsonl(output_path)
    return records


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Collect SEC EDGAR Dow 30 filings for FinPortfolio IR.")
    parser.add_argument("--output", default="data/raw_documents/sec_dow30_filings_2010_2023.jsonl")
    parser.add_argument("--metadata-output", default="data/processed_documents/dow30_ticker_metadata.csv")
    parser.add_argument("--manifest-output", default="data/raw_documents/sec_dow30_filings_manifest.json")
    parser.add_argument("--user-agent", default="FinPortfolioIR research contact ivanp@example.com")
    parser.add_argument("--pause-seconds", type=float, default=0.12)
    parser.add_argument("--train-per-ticker", type=int, default=8)
    parser.add_argument("--test-per-ticker", type=int, default=2)
    parser.add_argument("--max-body-chars", type=int, default=16000)
    parser.add_argument("--max-download-bytes", type=int, default=350000)
    parser.add_argument("--forms", default=",".join(DEFAULT_FORMS))
    parser.add_argument("--no-resume", action="store_true")
    args = parser.parse_args(argv)

    forms = {item.strip().upper() for item in args.forms.split(",") if item.strip()}
    sec_map = sec_ticker_map(args.user_agent, args.pause_seconds)
    metadata_rows = _metadata_rows(sec_map)
    write_metadata(Path(args.metadata_output), metadata_rows)

    by_ticker = {}
    for company in DOW30_COMPANIES:
        ticker = company["ticker"]
        by_ticker[ticker] = filing_candidates_for_ticker(
            ticker=ticker,
            sec_row=sec_map[ticker],
            sector=company["sector"],
            forms=forms,
            user_agent=args.user_agent,
            pause_seconds=args.pause_seconds,
        )
        print(f"{ticker}: {len(by_ticker[ticker])} candidate filings", flush=True)

    selected = select_candidates(by_ticker, args.train_per_ticker, args.test_per_ticker)
    records = append_raw_documents(
        output_path=Path(args.output),
        candidates=selected,
        user_agent=args.user_agent,
        pause_seconds=args.pause_seconds,
        max_body_chars=args.max_body_chars,
        max_download_bytes=args.max_download_bytes,
        resume=not args.no_resume,
    )

    manifest = {
        "source": "SEC EDGAR official submissions/archive",
        "forms": sorted(forms),
        "train_period": [TRAIN_START, TRAIN_END],
        "test_period": [TRAIN_END, TEST_END],
        "train_per_ticker": args.train_per_ticker,
        "test_per_ticker": args.test_per_ticker,
        "document_count": len(records),
        "split_counts": {
            "train": sum(1 for record in records if record.get("split") == "train"),
            "test": sum(1 for record in records if record.get("split") == "test"),
        },
        "tickers": [company["ticker"] for company in DOW30_COMPANIES],
        "metadata_output": args.metadata_output,
        "raw_output": args.output,
    }
    Path(args.manifest_output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.manifest_output).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
