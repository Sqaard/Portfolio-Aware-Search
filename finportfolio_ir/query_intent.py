"""ConFIRM-inspired query intent routing for financial IR.

The first implementation is deterministic and auditable. It mirrors the role
ConFIRM assigns to a fine-tuned classifier: map a natural-language financial
query to knowledge-base labels and retrieval routes before ranking documents.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Iterable

from finportfolio_ir.dow30 import DOW30_COMPANIES, DOW30_TICKER_SET
from finportfolio_ir.text_utils import tokenize


@dataclass(frozen=True)
class QueryIntent:
    raw_query: str
    normalized_query: str
    primary_intent: str
    source_routes: list[str]
    kb_labels: list[str]
    field_labels: list[str]
    matched_tickers: list[str]
    needs_structured_data: bool
    needs_point_in_time_guard: bool
    external_or_user_source: bool
    confidence: float
    reason_tags: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


SEC_TERMS = {
    "10-k",
    "10k",
    "10-q",
    "10q",
    "8-k",
    "8k",
    "filing",
    "filings",
    "annual",
    "quarterly",
    "md&a",
    "risk factors",
    "sec",
}

COMPANY_IR_TERMS = {
    "earnings",
    "guidance",
    "presentation",
    "investor day",
    "press release",
    "buyback",
    "dividend",
    "margin",
    "revenue",
}

MACRO_TERMS = {
    "fed",
    "fomc",
    "rates",
    "rate",
    "yield",
    "yields",
    "inflation",
    "cpi",
    "pce",
    "payrolls",
    "unemployment",
    "credit spread",
    "treasury",
    "dxy",
    "oil",
    "housing",
    "mortgage",
    "ism",
}

STRUCTURED_TERMS = {
    "xbrl",
    "company facts",
    "eps",
    "ebitda",
    "cash flow",
    "free cash flow",
    "fcf",
    "revenue",
    "margin",
    "debt",
    "assets",
    "liabilities",
    "table",
    "number",
    "how much",
    "what was",
}

NEWS_SENTIMENT_TERMS = {
    "news",
    "sentiment",
    "mood",
    "twitter",
    "reddit",
    "google trends",
    "trend",
    "media",
    "headline",
}

PORTFOLIO_TERMS = {
    "portfolio",
    "holding",
    "holdings",
    "my stocks",
    "my positions",
    "risk",
    "exposure",
    "impact",
    "affect",
    "sensitivity",
}

FAVORITE_TERMS = {
    "favorite",
    "preferred",
    "my vibe",
    "website",
    "blog",
    "post",
    "posts",
}

EXTERNAL_TERMS = {
    "reddit",
    "twitter",
    "x.com",
    "youtube",
    "telegram",
    "blog",
    "forum",
    "opinion",
}

FIELD_KEYWORDS = {
    "earnings": {"earnings", "eps", "profit", "net income", "guidance"},
    "revenue": {"revenue", "sales", "top line"},
    "capital_return": {"buyback", "repurchase", "dividend", "cash return"},
    "credit": {"credit", "loan", "debt", "spread", "default", "deposit"},
    "rates": {"fed", "rate", "rates", "yield", "treasury", "fomc"},
    "inflation": {"inflation", "cpi", "pce", "prices"},
    "labor": {"payrolls", "jobs", "unemployment", "wages"},
    "housing": {"housing", "mortgage", "starts", "permits", "home sales"},
    "energy": {"oil", "brent", "wti", "opec", "gas"},
    "legal_regulatory": {"lawsuit", "investigation", "regulation", "sec", "doj", "ftc"},
}

def _company_aliases(name: str, ticker: str) -> set[str]:
    lowered = name.lower()
    aliases = {ticker.lower(), lowered}
    aliases.add(re.sub(r"\b(inc|inc\.|corp|corporation|company|co\.|the)\b", "", lowered).strip())
    first = lowered.split(",")[0].split(" incorporated")[0].split(" corporation")[0].strip()
    if first:
        aliases.add(first)
    if ticker == "AAPL":
        aliases.add("apple")
    if ticker == "MSFT":
        aliases.add("microsoft")
    if ticker == "JPM":
        aliases.add("jpmorgan")
        aliases.add("jp morgan")
    if ticker == "NVDA":
        aliases.add("nvidia")
    return {alias for alias in aliases if alias}


COMPANY_NAME_ALIASES = {
    company["ticker"]: _company_aliases(company["name"], company["ticker"])
    for company in DOW30_COMPANIES
}


def _contains_phrase(text: str, phrases: Iterable[str]) -> bool:
    return any(phrase in text for phrase in phrases)


def _matched_terms(text: str, terms: Iterable[str]) -> list[str]:
    return sorted(term for term in terms if term in text)


def _matched_tickers(query: str) -> list[str]:
    lowered = f" {query.lower()} "
    tokens = {token.upper().strip("$") for token in tokenize(query)}
    matched = {token for token in tokens if token in DOW30_TICKER_SET}
    for ticker, aliases in COMPANY_NAME_ALIASES.items():
        if any(re.search(r"(?<![a-z0-9])" + re.escape(alias) + r"(?:'s)?(?![a-z0-9])", lowered) for alias in aliases):
            matched.add(ticker)
    return sorted(matched)


def _field_labels(normalized_query: str) -> list[str]:
    labels = []
    for label, keywords in FIELD_KEYWORDS.items():
        if _contains_phrase(normalized_query, keywords):
            labels.append(label)
    return labels


def classify_query_intent(query: str) -> QueryIntent:
    raw_query = str(query or "").strip()
    normalized = " ".join(raw_query.lower().split())
    matched_tickers = _matched_tickers(raw_query)

    sec_hits = _matched_terms(normalized, SEC_TERMS)
    ir_hits = _matched_terms(normalized, COMPANY_IR_TERMS)
    macro_hits = _matched_terms(normalized, MACRO_TERMS)
    structured_hits = _matched_terms(normalized, STRUCTURED_TERMS)
    news_hits = _matched_terms(normalized, NEWS_SENTIMENT_TERMS)
    portfolio_hits = _matched_terms(normalized, PORTFOLIO_TERMS)
    favorite_hits = _matched_terms(normalized, FAVORITE_TERMS)
    external_hits = _matched_terms(normalized, EXTERNAL_TERMS)
    fields = _field_labels(normalized)

    routes: list[str] = []
    kb_labels: list[str] = []
    reason_tags: list[str] = []

    def add_route(route: str, label: str) -> None:
        if route not in routes:
            routes.append(route)
        if label not in kb_labels:
            kb_labels.append(label)

    if sec_hits:
        add_route("sec_filings", "filings")
        reason_tags.append("sec_filing_language")
    if ir_hits:
        add_route("company_ir", "company_events")
        reason_tags.append("company_ir_language")
    if macro_hits:
        add_route("official_macro", "macro_data")
        reason_tags.append("macro_language")
    if structured_hits or re.search(r"\d", normalized):
        add_route("structured_facts", "numeric_or_table_data")
        reason_tags.append("structured_numeric_language")
    if news_hits:
        add_route("market_news", "market_news_and_sentiment")
        reason_tags.append("news_or_sentiment_language")
    if favorite_hits:
        add_route("favorite_websites", "user_favorite_sources")
        reason_tags.append("favorite_source_language")
    if external_hits:
        add_route("external_web", "external_or_untrusted_sources")
        reason_tags.append("external_source_language")

    if matched_tickers:
        reason_tags.append("dow30_entity_match")
        if not routes:
            add_route("company_ir", "company_events")
    if portfolio_hits:
        reason_tags.append("portfolio_context_language")

    if not routes:
        add_route("local_corpus", "general_financial_text")

    if portfolio_hits:
        primary = "portfolio_impact"
    elif sec_hits and structured_hits:
        primary = "filing_fact_lookup"
    elif sec_hits:
        primary = "filing_search"
    elif structured_hits:
        primary = "structured_numeric_lookup"
    elif macro_hits:
        primary = "macro_regime_lookup"
    elif news_hits or external_hits:
        primary = "news_sentiment_lookup"
    elif favorite_hits:
        primary = "favorite_source_lookup"
    elif matched_tickers:
        primary = "company_event_search"
    else:
        primary = "general_financial_search"

    signal_count = sum(
        bool(group)
        for group in (sec_hits, ir_hits, macro_hits, structured_hits, news_hits, portfolio_hits, favorite_hits, external_hits, matched_tickers)
    )
    confidence = min(0.95, 0.35 + 0.10 * signal_count + 0.05 * min(len(fields), 3))
    if not raw_query:
        confidence = 0.0

    return QueryIntent(
        raw_query=raw_query,
        normalized_query=normalized,
        primary_intent=primary,
        source_routes=routes,
        kb_labels=kb_labels,
        field_labels=fields,
        matched_tickers=matched_tickers,
        needs_structured_data=bool(structured_hits or re.search(r"\d", normalized)),
        needs_point_in_time_guard=True,
        external_or_user_source=bool(external_hits or favorite_hits),
        confidence=round(confidence, 3),
        reason_tags=list(dict.fromkeys(reason_tags)),
    )
