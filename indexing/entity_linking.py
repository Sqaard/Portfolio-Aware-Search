"""Rule-based ticker, company, alias, and sector linking."""

from __future__ import annotations

import argparse
import csv
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Union

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from finportfolio_ir.io_utils import local_project_path, read_jsonl, write_jsonl


@dataclass(frozen=True)
class TickerMetadata:
    ticker: str
    cik: str = ""
    official_name: str = ""
    company_name: str = ""
    common_name: str = ""
    sector: str = ""
    exchange: str = ""
    active_from: str = ""
    active_to: str = ""
    peer_group: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    products: tuple[str, ...] = ()
    risk_terms: tuple[str, ...] = ()
    source_credibility: float = 0.5

    def query_terms(self) -> list[str]:
        terms = [self.ticker, self.official_name, self.company_name, self.common_name, self.sector]
        terms.extend(self.aliases)
        terms.extend(self.products)
        terms.extend(self.peer_group)
        terms.extend(self.risk_terms)
        return [term for term in terms if term]


def _split_terms(value: str) -> tuple[str, ...]:
    if not value:
        return ()
    parts = re.split(r"[|;]", value)
    return tuple(part.strip() for part in parts if part.strip())


def load_ticker_metadata(path: Union[str, Path]) -> dict[str, TickerMetadata]:
    metadata: dict[str, TickerMetadata] = {}
    with local_project_path(path).open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            ticker = row["ticker"].strip().upper()
            metadata[ticker] = TickerMetadata(
                ticker=ticker,
                cik=row.get("cik", "").strip(),
                official_name=row.get("official_name", "").strip(),
                company_name=row.get("company_name", "").strip(),
                common_name=row.get("common_name", "").strip(),
                sector=row.get("sector", "").strip(),
                exchange=row.get("exchange", "").strip(),
                active_from=row.get("active_from", "").strip(),
                active_to=row.get("active_to", "").strip(),
                peer_group=_split_terms(row.get("peer_group", "")),
                aliases=_split_terms(row.get("aliases", "")),
                products=_split_terms(row.get("products", "")),
                risk_terms=_split_terms(row.get("risk_terms", "")),
                source_credibility=float(row.get("source_credibility", 0.5) or 0.5),
            )
    return metadata


def _contains_case_insensitive_phrase(text: str, phrase: str) -> bool:
    pattern = r"(?<![A-Za-z0-9])" + re.escape(phrase) + r"(?![A-Za-z0-9])"
    return bool(re.search(pattern, text, flags=re.IGNORECASE))


def _contains_ticker(text: str, ticker: str) -> bool:
    # Short tickers are noisy as lowercase words, so require uppercase or $ prefix.
    if len(ticker) <= 3:
        return bool(re.search(r"(?<![A-Za-z0-9])\$?" + re.escape(ticker) + r"(?![A-Za-z0-9])", text))
    return _contains_case_insensitive_phrase(text, ticker)


def link_entities_in_text(text: str, metadata: dict[str, TickerMetadata]) -> dict[str, list[str]]:
    tickers: list[str] = []
    company_names: list[str] = []
    sectors: list[str] = []
    matched_products: list[str] = []
    matched_risk_terms: list[str] = []

    for ticker, item in metadata.items():
        matched = False if ticker == "MARKET" else _contains_ticker(text, ticker)
        for company_phrase in (item.official_name, item.company_name, item.common_name):
            if ticker != "MARKET" and company_phrase and _contains_case_insensitive_phrase(text, company_phrase):
                matched = True
        if not matched:
            for alias in item.aliases:
                if _contains_case_insensitive_phrase(text, alias):
                    matched = True
                    break
        product_hit = ""
        for product in item.products:
            if _contains_case_insensitive_phrase(text, product):
                product_hit = product
                break
        if product_hit:
            # Product-only matches are allowed only when the document has a
            # confirming company/ticker/sector cue. This avoids linking broad
            # product words without a financial context.
            has_sector_cue = item.sector and _contains_case_insensitive_phrase(text, item.sector)
            if matched or has_sector_cue:
                matched = True
                matched_products.append(product_hit)
        for risk_term in item.risk_terms:
            if _contains_case_insensitive_phrase(text, risk_term):
                matched_risk_terms.append(risk_term)
                if ticker == "MARKET":
                    matched = True
        if matched:
            tickers.append(ticker)
            company_name = item.official_name or item.company_name or item.common_name
            if company_name:
                company_names.append(company_name)
            if item.sector:
                sectors.append(item.sector)

    return {
        "tickers_detected": sorted(set(tickers)),
        "matched_tickers": sorted(set(tickers)),
        "matched_holdings": sorted(ticker for ticker in set(tickers) if ticker != "MARKET"),
        "company_names_detected": sorted(set(company_names)),
        "sectors_detected": sorted(set(sectors)),
        "sector_tags": sorted(set(sectors)),
        "matched_products": sorted(set(matched_products)),
        "risk_terms": sorted(set(matched_risk_terms)),
    }


def enrich_document_entities(record: dict[str, Any], metadata: dict[str, TickerMetadata]) -> dict[str, Any]:
    text = f"{record.get('title', '')} {record.get('body', '')}"
    linked = link_entities_in_text(text, metadata)
    enriched = dict(record)
    for key, values in linked.items():
        existing = [
            str(value).upper() if key in {"tickers_detected", "matched_tickers", "matched_holdings"} else str(value)
            for value in enriched.get(key, [])
        ]
        enriched[key] = sorted(set(existing).union(values))
    return enriched


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Link financial document text to ticker metadata.")
    parser.add_argument("--input", required=True, help="Input JSONL documents.")
    parser.add_argument("--metadata", required=True, help="Ticker metadata CSV.")
    parser.add_argument("--output", required=True, help="Output JSONL with entity fields.")
    args = parser.parse_args(argv)

    metadata = load_ticker_metadata(args.metadata)
    records = [enrich_document_entities(record, metadata) for record in read_jsonl(args.input)]
    write_jsonl(args.output, records)
    print(f"Wrote {len(records)} enriched documents to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
