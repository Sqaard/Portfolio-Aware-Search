"""Dow 30 ticker whitelist for the local portfolio UI.

The list is intentionally explicit so the first UI slice can reject unknown
portfolio tickers before they reach retrieval or FinGPT handoff code.
"""

from __future__ import annotations

from typing import Any


DOW30_COMPANIES: tuple[dict[str, str], ...] = (
    {"ticker": "MMM", "name": "3M Company", "sector": "Industrials"},
    {"ticker": "AXP", "name": "American Express Company", "sector": "Financials"},
    {"ticker": "AMGN", "name": "Amgen Inc.", "sector": "Health Care"},
    {"ticker": "AMZN", "name": "Amazon.com, Inc.", "sector": "Consumer Discretionary"},
    {"ticker": "AAPL", "name": "Apple Inc.", "sector": "Information Technology"},
    {"ticker": "BA", "name": "Boeing Company", "sector": "Industrials"},
    {"ticker": "CAT", "name": "Caterpillar Inc.", "sector": "Industrials"},
    {"ticker": "CRM", "name": "Salesforce, Inc.", "sector": "Information Technology"},
    {"ticker": "CSCO", "name": "Cisco Systems, Inc.", "sector": "Information Technology"},
    {"ticker": "CVX", "name": "Chevron Corporation", "sector": "Energy"},
    {"ticker": "DIS", "name": "The Walt Disney Company", "sector": "Communication Services"},
    {"ticker": "GS", "name": "The Goldman Sachs Group, Inc.", "sector": "Financials"},
    {"ticker": "HD", "name": "The Home Depot, Inc.", "sector": "Consumer Discretionary"},
    {"ticker": "HON", "name": "Honeywell International Inc.", "sector": "Industrials"},
    {"ticker": "IBM", "name": "International Business Machines Corporation", "sector": "Information Technology"},
    {"ticker": "JNJ", "name": "Johnson & Johnson", "sector": "Health Care"},
    {"ticker": "JPM", "name": "JPMorgan Chase & Co.", "sector": "Financials"},
    {"ticker": "KO", "name": "The Coca-Cola Company", "sector": "Consumer Staples"},
    {"ticker": "MCD", "name": "McDonald's Corporation", "sector": "Consumer Discretionary"},
    {"ticker": "MRK", "name": "Merck & Co., Inc.", "sector": "Health Care"},
    {"ticker": "MSFT", "name": "Microsoft Corporation", "sector": "Information Technology"},
    {"ticker": "NKE", "name": "NIKE, Inc.", "sector": "Consumer Discretionary"},
    {"ticker": "NVDA", "name": "NVIDIA Corporation", "sector": "Information Technology"},
    {"ticker": "PG", "name": "The Procter & Gamble Company", "sector": "Consumer Staples"},
    {"ticker": "SHW", "name": "The Sherwin-Williams Company", "sector": "Materials"},
    {"ticker": "TRV", "name": "The Travelers Companies, Inc.", "sector": "Financials"},
    {"ticker": "UNH", "name": "UnitedHealth Group Incorporated", "sector": "Health Care"},
    {"ticker": "V", "name": "Visa Inc.", "sector": "Financials"},
    {"ticker": "VZ", "name": "Verizon Communications Inc.", "sector": "Communication Services"},
    {"ticker": "WMT", "name": "Walmart Inc.", "sector": "Consumer Staples"},
)

DOW30_TICKERS: tuple[str, ...] = tuple(company["ticker"] for company in DOW30_COMPANIES)
DOW30_TICKER_SET = frozenset(DOW30_TICKERS)
DOW30_SECTOR_BY_TICKER = {company["ticker"]: company["sector"] for company in DOW30_COMPANIES}


def dow30_options() -> list[dict[str, Any]]:
    return [dict(company) for company in DOW30_COMPANIES]
