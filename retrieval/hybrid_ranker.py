"""Hybrid ranking components for portfolio-aware financial retrieval."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime

from finportfolio_ir.schema import FinancialDocument
from finportfolio_ir.text_utils import normalize_scores
from finportfolio_ir.time_utils import parse_datetime
from retrieval.portfolio_query_builder import PortfolioQuery


DEFAULT_EVENT_KEYWORDS = [
    "earnings",
    "guidance",
    "revenue",
    "profit warning",
    "lawsuit",
    "investigation",
    "downgrade",
    "upgrade",
    "merger",
    "acquisition",
    "bankruptcy",
    "fed",
    "interest rates",
    "inflation",
    "recession",
    "credit risk",
    "regulation",
    "supply chain",
    "deposits",
]


@dataclass(frozen=True)
class RankingWeights:
    sparse: float = 0.40
    dense: float = 0.0
    entity: float = 0.20
    portfolio_exposure: float = 0.25
    recency: float = 0.10
    event_importance: float = 0.05
    source_credibility: float = 0.0


@dataclass(frozen=True)
class DiversificationConfig:
    enabled: bool = False
    max_per_duplicate_cluster: int = 1
    max_per_holding: int = 4
    min_market_evidence: int = 0
    min_sector_evidence: int = 0


@dataclass(frozen=True)
class RankerConfig:
    weights: RankingWeights = field(default_factory=RankingWeights)
    recency_lambda: float = 0.10
    event_keywords: tuple[str, ...] = tuple(DEFAULT_EVENT_KEYWORDS)
    diversification: DiversificationConfig = field(default_factory=DiversificationConfig)


def filter_causal_documents(
    documents: list[FinancialDocument],
    decision_datetime: datetime,
) -> list[FinancialDocument]:
    safe_documents: list[FinancialDocument] = []
    for document in documents:
        if parse_datetime(document.available_at) <= decision_datetime:
            safe_documents.append(document)
    return safe_documents


def dense_embedding_score(_: FinancialDocument, __: PortfolioQuery) -> float:
    return 0.0


def entity_match_score(document: FinancialDocument, query: PortfolioQuery) -> float:
    matched = set(document.tickers_detected).intersection(query.tickers)
    if not matched:
        return 0.0
    return min(1.0, len(matched) / max(1, len(query.tickers)))


def portfolio_exposure_score(document: FinancialDocument, query: PortfolioQuery) -> float:
    matched = set(document.tickers_detected).intersection(query.weighted_entities)
    if not matched:
        return 0.0
    max_weight = max(abs(weight) for weight in query.weighted_entities.values()) or 1.0
    exposure = sum(abs(query.weighted_entities[ticker]) for ticker in matched)
    return min(1.0, exposure / max_weight)


def portfolio_weight_sum(document: FinancialDocument, query: PortfolioQuery) -> float:
    matched = set(document.tickers_detected).intersection(query.weighted_entities)
    return sum(abs(query.weighted_entities[ticker]) for ticker in matched)


def recency_score(document: FinancialDocument, decision_datetime: datetime, recency_lambda: float) -> float:
    published_at = parse_datetime(document.published_at)
    age_days = max((decision_datetime - published_at).total_seconds() / 86400.0, 0.0)
    return math.exp(-recency_lambda * age_days)


def event_importance_score(document: FinancialDocument, event_keywords: tuple[str, ...]) -> float:
    text = f"{document.title} {document.body} {document.event_type}".lower()
    hits = sum(1 for keyword in event_keywords if keyword.lower() in text)
    return min(1.0, hits / 3.0)


def source_credibility_score(document: FinancialDocument) -> float:
    return min(1.0, max(0.0, float(document.source_credibility)))


def evidence_scope(document: FinancialDocument, matched_tickers: list[str]) -> str:
    if matched_tickers:
        return "stock"
    if document.sector_tags or document.sectors_detected:
        return "sector"
    return "market"


def _freshness_tag(document: FinancialDocument, decision_datetime: datetime) -> str:
    published_at = parse_datetime(document.published_at)
    age_hours = max((decision_datetime - published_at).total_seconds() / 3600.0, 0.0)
    if age_hours <= 2:
        return "fresh_2h"
    if age_hours <= 24:
        return "fresh_24h"
    if age_hours <= 72:
        return "fresh_3d"
    return "older_context"


def _safe_tag(prefix: str, value: str) -> str:
    cleaned = "_".join(value.lower().replace("/", " ").replace("-", " ").split())
    return f"{prefix}_{cleaned}" if cleaned else prefix


def deterministic_reason_tags(
    document: FinancialDocument,
    query: PortfolioQuery,
    matched_tickers: list[str],
    event_score: float,
    decision_datetime: datetime,
) -> list[str]:
    tags: list[str] = []
    scope = evidence_scope(document, matched_tickers)
    tags.append(f"{scope}_scope")
    if matched_tickers:
        tags.append("exact_ticker")
        max_weight = max(abs(weight) for weight in query.weighted_entities.values()) or 1.0
        exposure = portfolio_weight_sum(document, query)
        if exposure >= 0.75 * max_weight:
            tags.append("high_exposure")
    if document.company_names_detected:
        tags.append("company_alias")
    for event in document.event_tags[:3]:
        tags.append(_safe_tag("event", event))
    for risk in document.risk_terms[:3]:
        tags.append(_safe_tag("risk", risk))
    if event_score > 0 and not any(tag.startswith("event_") for tag in tags):
        tags.append("event_language")
    tags.append(_freshness_tag(document, decision_datetime))
    if source_credibility_score(document) >= 0.80:
        tags.append("high_source_credibility")
    return list(dict.fromkeys(tags))


def _reason(document: FinancialDocument, query: PortfolioQuery, event_score: float) -> str:
    matched = set(document.tickers_detected).intersection(query.tickers)
    if matched:
        tickers = ", ".join(sorted(matched))
        exposure = portfolio_weight_sum(document, query)
        if event_score > 0:
            return f"Matches portfolio holding(s) {tickers}, exposure {exposure:.2f}, with event/risk language."
        return f"Matches portfolio holding(s) {tickers}, exposure {exposure:.2f}."
    if event_score > 0:
        return "Macro or sector document with event/risk language."
    return "Lexical match to the portfolio query."


def _duplicate_cluster(row: dict[str, object]) -> str:
    document = row["document"]
    assert isinstance(document, FinancialDocument)
    return document.duplicate_cluster_id or str(row["doc_id"])


def _row_holdings(row: dict[str, object]) -> list[str]:
    return [str(ticker).upper() for ticker in row.get("matched_tickers", [])]


def _can_select(
    row: dict[str, object],
    cluster_counts: dict[str, int],
    holding_counts: dict[str, int],
    config: DiversificationConfig,
    enforce_duplicate_cap: bool = True,
    enforce_holding_cap: bool = True,
) -> bool:
    if enforce_duplicate_cap and config.max_per_duplicate_cluster > 0:
        if cluster_counts.get(_duplicate_cluster(row), 0) >= config.max_per_duplicate_cluster:
            return False
    holdings = _row_holdings(row)
    if enforce_holding_cap and holdings and config.max_per_holding > 0:
        if all(holding_counts.get(holding, 0) >= config.max_per_holding for holding in holdings):
            return False
    return True


def _add_selected(
    selected: list[dict[str, object]],
    row: dict[str, object],
    cluster_counts: dict[str, int],
    holding_counts: dict[str, int],
) -> None:
    selected.append(row)
    cluster_counts[_duplicate_cluster(row)] = cluster_counts.get(_duplicate_cluster(row), 0) + 1
    for holding in _row_holdings(row):
        holding_counts[holding] = holding_counts.get(holding, 0) + 1


def _select_with_caps(rows: list[dict[str, object]], top_k: int, config: DiversificationConfig) -> list[dict[str, object]]:
    selected: list[dict[str, object]] = []
    selected_ids: set[str] = set()
    cluster_counts: dict[str, int] = {}
    holding_counts: dict[str, int] = {}

    for row in rows:
        if len(selected) >= top_k:
            break
        if _can_select(row, cluster_counts, holding_counts, config):
            _add_selected(selected, row, cluster_counts, holding_counts)
            selected_ids.add(str(row["doc_id"]))

    for enforce_duplicate, enforce_holding in [(True, False), (False, False)]:
        if len(selected) >= top_k:
            break
        for row in rows:
            if len(selected) >= top_k:
                break
            if str(row["doc_id"]) in selected_ids:
                continue
            if _can_select(row, cluster_counts, holding_counts, config, enforce_duplicate, enforce_holding):
                _add_selected(selected, row, cluster_counts, holding_counts)
                selected_ids.add(str(row["doc_id"]))
    return selected


def _ensure_scope_slots(
    selected: list[dict[str, object]],
    rows: list[dict[str, object]],
    top_k: int,
    scope: str,
    minimum: int,
) -> list[dict[str, object]]:
    if minimum <= 0:
        return selected
    selected_ids = {str(row["doc_id"]) for row in selected}
    selected_clusters = {_duplicate_cluster(row) for row in selected}
    while sum(1 for row in selected if row.get("evidence_scope") == scope) < minimum:
        candidate = next(
            (
                row
                for row in rows
                if row.get("evidence_scope") == scope
                and str(row["doc_id"]) not in selected_ids
                and _duplicate_cluster(row) not in selected_clusters
            ),
            None,
        )
        if candidate is None:
            break
        if len(selected) < top_k:
            selected.append(candidate)
        else:
            removable = [row for row in selected if row.get("evidence_scope") != scope]
            if not removable:
                break
            victim = min(removable, key=lambda row: (float(row["final_score"]), str(row["doc_id"])))
            selected.remove(victim)
            selected_ids.remove(str(victim["doc_id"]))
            selected_clusters.remove(_duplicate_cluster(victim))
            selected.append(candidate)
        selected_ids.add(str(candidate["doc_id"]))
        selected_clusters.add(_duplicate_cluster(candidate))
    return selected


def diversify_ranked_rows(
    rows: list[dict[str, object]],
    top_k: int,
    config: DiversificationConfig,
) -> list[dict[str, object]]:
    selected = _select_with_caps(rows, top_k, config)
    selected = _ensure_scope_slots(selected, rows, top_k, "market", config.min_market_evidence)
    selected = _ensure_scope_slots(selected, rows, top_k, "sector", config.min_sector_evidence)
    selected_ids = {str(row["doc_id"]) for row in selected}
    for row in rows:
        if len(selected) >= top_k:
            break
        if str(row["doc_id"]) not in selected_ids:
            selected.append(row)
            selected_ids.add(str(row["doc_id"]))
    selected.sort(key=lambda row: (-float(row["final_score"]), str(row["doc_id"])))
    return selected[:top_k]


def rank_documents(
    documents: list[FinancialDocument],
    query: PortfolioQuery,
    decision_datetime: datetime,
    sparse_scores: dict[str, float],
    config: RankerConfig,
    top_k: int,
) -> list[dict[str, object]]:
    safe_documents = filter_causal_documents(documents, decision_datetime)
    normalized_sparse = normalize_scores({doc.doc_id: sparse_scores.get(doc.doc_id, 0.0) for doc in safe_documents})
    rows: list[dict[str, object]] = []

    for document in safe_documents:
        sparse = normalized_sparse.get(document.doc_id, 0.0)
        dense = dense_embedding_score(document, query)
        entity = entity_match_score(document, query)
        exposure = portfolio_exposure_score(document, query)
        recency = recency_score(document, decision_datetime, config.recency_lambda)
        event = event_importance_score(document, config.event_keywords)
        source = source_credibility_score(document)

        weights = config.weights
        final = (
            weights.sparse * sparse
            + weights.dense * dense
            + weights.entity * entity
            + weights.portfolio_exposure * exposure
            + weights.recency * recency
            + weights.event_importance * event
            + weights.source_credibility * source
        )
        matched_tickers = sorted(set(document.tickers_detected).intersection(query.weighted_entities))
        rows.append(
            {
                "doc_id": document.doc_id,
                "document": document,
                "matched_tickers": matched_tickers,
                "evidence_scope": evidence_scope(document, matched_tickers),
                "portfolio_weight_sum": portfolio_weight_sum(document, query),
                "sparse_score": sparse,
                "dense_score": dense,
                "entity_score": entity,
                "portfolio_exposure_score": exposure,
                "recency_score": recency,
                "event_importance_score": event,
                "source_credibility_score": source,
                "final_score": final,
                "reason": _reason(document, query, event),
                "retrieval_reason_tags": deterministic_reason_tags(
                    document,
                    query,
                    matched_tickers,
                    event,
                    decision_datetime,
                ),
                "diversification_applied": config.diversification.enabled,
            }
        )

    rows.sort(key=lambda row: (-float(row["final_score"]), str(row["doc_id"])))
    selected_rows = (
        diversify_ranked_rows(rows, top_k, config.diversification)
        if config.diversification.enabled
        else rows[:top_k]
    )
    for index, row in enumerate(selected_rows, start=1):
        row["rank"] = index
    return selected_rows
