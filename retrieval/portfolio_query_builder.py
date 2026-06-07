"""Build weighted portfolio information needs from holdings and ticker metadata."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from finportfolio_ir.io_utils import load_yaml
from indexing.entity_linking import TickerMetadata, load_ticker_metadata


DEFAULT_RISK_KEYWORDS = [
    "earnings",
    "guidance",
    "revenue",
    "supply chain",
    "inflation",
    "interest rates",
    "credit risk",
    "regulation",
]


@dataclass(frozen=True)
class PortfolioQuery:
    portfolio_id: str
    tickers: list[str]
    weighted_entities: dict[str, float]
    expanded_terms: dict[str, list[str]]
    query_text: str


def load_portfolio(path: Union[str, Path]) -> tuple[str, dict[str, float]]:
    payload = load_yaml(path)
    portfolio_id = str(payload.get("portfolio_id", Path(path).stem))
    holdings = {str(ticker).upper(): float(weight) for ticker, weight in payload.get("holdings", {}).items()}
    if not holdings:
        raise ValueError(f"Portfolio file has no holdings: {path}")
    return portfolio_id, holdings


def build_portfolio_query(
    portfolio_id: str,
    holdings: dict[str, float],
    metadata: dict[str, TickerMetadata],
    risk_keywords: Optional[list[str]] = None,
) -> PortfolioQuery:
    risk_keywords = risk_keywords or DEFAULT_RISK_KEYWORDS
    tickers = [ticker for ticker, _ in sorted(holdings.items(), key=lambda item: (-abs(item[1]), item[0]))]
    expanded_terms: dict[str, list[str]] = {}
    query_parts: list[str] = []

    for ticker in tickers:
        item = metadata.get(ticker)
        terms = [ticker]
        if item:
            terms.extend(item.query_terms())
        terms.extend(risk_keywords)
        deduped = list(dict.fromkeys(term for term in terms if term))
        expanded_terms[ticker] = deduped

        # Repeat high exposure ticker and company terms once more to make BM25
        # sensitive to the portfolio profile while keeping scoring inspectable.
        exposure_repeats = 2 if abs(holdings[ticker]) >= max(abs(weight) for weight in holdings.values()) else 1
        for _ in range(exposure_repeats):
            query_parts.extend(deduped)

    return PortfolioQuery(
        portfolio_id=portfolio_id,
        tickers=tickers,
        weighted_entities=dict(holdings),
        expanded_terms=expanded_terms,
        query_text=" ".join(query_parts),
    )


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Build a structured portfolio query.")
    parser.add_argument("--portfolio", required=True)
    parser.add_argument("--metadata", required=True)
    args = parser.parse_args(argv)

    portfolio_id, holdings = load_portfolio(args.portfolio)
    query = build_portfolio_query(portfolio_id, holdings, load_ticker_metadata(args.metadata))
    print(json.dumps(query.__dict__, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
