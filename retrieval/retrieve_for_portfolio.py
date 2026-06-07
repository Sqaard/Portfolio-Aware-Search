"""CLI entry point for causal portfolio-aware retrieval."""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import timezone
from pathlib import Path
from typing import Optional, Union

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from finportfolio_ir.io_utils import load_yaml, read_jsonl, write_jsonl
from finportfolio_ir.schema import FinancialDocument, load_documents
from finportfolio_ir.time_utils import parse_decision_datetime
from indexing.build_sparse_index import BM25Index
from indexing.entity_linking import enrich_document_entities, load_ticker_metadata
from retrieval.hybrid_ranker import DiversificationConfig, RankerConfig, RankingWeights, rank_documents
from retrieval.portfolio_query_builder import build_portfolio_query, load_portfolio


def _load_config(path: Union[str, Path]) -> dict[str, object]:
    return load_yaml(path)


def _ranker_config(config: dict[str, object], method: str = "full_hybrid") -> RankerConfig:
    ranking_methods = config.get("ranking_methods", {}) or {}
    ranking_weights = config.get("ranking_weights", {}) or {}
    method_config: dict[str, object] = {}
    if method and isinstance(ranking_methods, dict) and method in ranking_methods:
        raw_method_config = ranking_methods[method] or {}
        if isinstance(raw_method_config, dict):
            method_config = raw_method_config
            ranking_weights = method_config.get("weights", method_config) or {}
    retrieval_config = config.get("retrieval", {}) or {}
    diversification_config = config.get("diversification", {}) or {}
    if not isinstance(diversification_config, dict):
        diversification_config = {}
    enabled_methods = {
        str(item)
        for item in diversification_config.get("enabled_methods", []) or []
    }
    method_diversification = bool(method_config.get("diversification", False))
    global_diversification = bool(diversification_config.get("enabled", False))
    diversification_enabled = method_diversification or (
        global_diversification and (not enabled_methods or method in enabled_methods)
    )
    return RankerConfig(
        weights=RankingWeights(
            sparse=float(ranking_weights.get("sparse", 0.40)),
            dense=float(ranking_weights.get("dense", 0.0)),
            entity=float(ranking_weights.get("entity", 0.20)),
            portfolio_exposure=float(ranking_weights.get("portfolio_exposure", 0.25)),
            recency=float(ranking_weights.get("recency", 0.10)),
            event_importance=float(ranking_weights.get("event_importance", 0.05)),
            source_credibility=float(ranking_weights.get("source_credibility", 0.0)),
        ),
        recency_lambda=float(retrieval_config.get("recency_lambda", 0.10)),
        event_keywords=tuple(str(item) for item in config.get("event_keywords", []) or ()),
        diversification=DiversificationConfig(
            enabled=diversification_enabled,
            max_per_duplicate_cluster=int(diversification_config.get("max_per_duplicate_cluster", 1)),
            max_per_holding=int(diversification_config.get("max_per_holding", 4)),
            min_market_evidence=int(diversification_config.get("min_market_evidence", 0)),
            min_sector_evidence=int(diversification_config.get("min_sector_evidence", 0)),
        ),
    )


def _load_documents_with_entities(
    documents_path: Union[str, Path],
    metadata_path: Union[str, Path],
) -> list[FinancialDocument]:
    metadata = load_ticker_metadata(metadata_path)
    records = []
    for record in read_jsonl(documents_path):
        if not record.get("tickers_detected"):
            record = enrich_document_entities(record, metadata)
        records.append(record)
    return load_documents(records)


def retrieval_records(
    documents_path: Union[str, Path],
    portfolio_path: Union[str, Path],
    metadata_path: Union[str, Path],
    decision_datetime_text: str,
    config_path: Union[str, Path],
    top_k: Optional[int] = None,
    query_id: Optional[str] = None,
    method: str = "full_hybrid",
) -> list[dict[str, object]]:
    config = _load_config(config_path)
    retrieval_config = config.get("retrieval", {}) or {}
    decision_datetime = parse_decision_datetime(
        decision_datetime_text,
        default_timezone=str(retrieval_config.get("decision_timezone", "America/New_York")),
    )
    top_k = int(top_k or retrieval_config.get("top_k", 10))
    body_excerpt_chars = int(retrieval_config.get("body_excerpt_chars", 1200))

    metadata = load_ticker_metadata(metadata_path)
    portfolio_id, holdings = load_portfolio(portfolio_path)
    query = build_portfolio_query(
        portfolio_id,
        holdings,
        metadata,
        risk_keywords=[str(item) for item in config.get("event_keywords", []) or []],
    )

    documents = _load_documents_with_entities(documents_path, metadata_path)
    sparse_scores = BM25Index.from_documents(documents).score_query(query.query_text)
    ranked = rank_documents(
        documents=documents,
        query=query,
        decision_datetime=decision_datetime,
        sparse_scores=sparse_scores,
        config=_ranker_config(config, method=method),
        top_k=top_k,
    )

    decision_time_utc = decision_datetime.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    decision_date = decision_datetime.astimezone(timezone.utc).date().isoformat()
    query_id = query_id or f"{portfolio_id}_{decision_date}"

    output: list[dict[str, object]] = []
    for item in ranked:
        document = item["document"]
        assert isinstance(document, FinancialDocument)
        record = {
            "query_id": query_id,
            "decision_id": query_id,
            "method": method,
            "portfolio_id": portfolio_id,
            "portfolio_snapshot_id": portfolio_id,
            "portfolio_holdings": sorted(holdings.keys()),
            "decision_date": decision_date,
            "decision_time": decision_time_utc,
            "decision_datetime": decision_time_utc,
            "retrieval_cutoff": decision_time_utc,
            "retrieval_query_lex": " ".join(query.tickers),
            "retrieval_query_sem": query.query_text,
            "evidence_bundle_id": f"{query_id}:{method}",
            "rank": item["rank"],
            "doc_id": document.doc_id,
            "source": document.source,
            "source_type": document.source_type,
            "source_registry_id": document.source_registry_id,
            "source_reliability_tier": document.source_reliability_tier,
            "robots_policy": document.robots_policy,
            "content_license_note": document.content_license_note,
            "published_at": document.published_at,
            "first_seen_at": document.first_seen_at,
            "available_at": document.available_at,
            "ingested_at": document.ingested_at,
            "version_id": document.version_id,
            "is_revision": document.is_revision,
            "revision_of": document.revision_of,
            "duplicate_cluster_id": document.duplicate_cluster_id,
            "title": document.title,
            "body_excerpt": document.body_excerpt(body_excerpt_chars),
            "url": document.url,
            "canonical_url": document.canonical_url,
            "last_url_check_at": document.last_url_check_at,
            "fetch_status": document.fetch_status,
            "matched_tickers": item["matched_tickers"],
            "matched_holdings": document.matched_holdings,
            "event_tags": document.event_tags,
            "risk_terms": document.risk_terms,
            "source_credibility": round(float(document.source_credibility), 6),
            "evidence_scope": item["evidence_scope"],
            "portfolio_weight_sum": round(float(item["portfolio_weight_sum"]), 6),
            "sparse_score": round(float(item["sparse_score"]), 6),
            "dense_score": round(float(item["dense_score"]), 6),
            "entity_score": round(float(item["entity_score"]), 6),
            "portfolio_exposure_score": round(float(item["portfolio_exposure_score"]), 6),
            "recency_score": round(float(item["recency_score"]), 6),
            "event_importance_score": round(float(item["event_importance_score"]), 6),
            "source_credibility_score": round(float(item["source_credibility_score"]), 6),
            "final_score": round(float(item["final_score"]), 6),
            "retrieval_reason_tags": item["retrieval_reason_tags"],
            "diversification_applied": item["diversification_applied"],
            "ranking_stage": "diversified_rerank" if item["diversification_applied"] else "linear_score",
            "reason": item["reason"],
            "document_hash": document.document_hash,
        }
        output.append(record)
    return output


def write_run_csv(
    path: Union[str, Path],
    records: list[dict[str, object]],
    method: str = "full_hybrid",
) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["query_id", "doc_id", "rank", "score", "method"])
        writer.writeheader()
        for record in records:
            writer.writerow(
                {
                    "query_id": record["query_id"],
                    "doc_id": record["doc_id"],
                    "rank": record["rank"],
                    "score": record["final_score"],
                    "method": record.get("method", method),
                }
            )


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Retrieve portfolio-relevant causal documents.")
    parser.add_argument("--documents", required=True, help="Processed financial documents JSONL.")
    parser.add_argument("--portfolio", required=True, help="Portfolio YAML.")
    parser.add_argument("--metadata", default="data/processed_documents/ticker_metadata.csv")
    parser.add_argument("--config", default="configs/default.yaml")
    parser.add_argument("--decision-datetime", required=True)
    parser.add_argument("--top-k", type=int, default=None)
    parser.add_argument("--output", required=True, help="FinPortfolio IR retrieval JSONL.")
    parser.add_argument("--run-csv", default="", help="Optional TREC-like run CSV for evaluation.")
    parser.add_argument("--query-id", default=None)
    parser.add_argument(
        "--method",
        default="full_hybrid",
        help="Ranking method from configs/default.yaml ranking_methods.",
    )
    args = parser.parse_args(argv)

    records = retrieval_records(
        documents_path=args.documents,
        portfolio_path=args.portfolio,
        metadata_path=args.metadata,
        decision_datetime_text=args.decision_datetime,
        config_path=args.config,
        top_k=args.top_k,
        query_id=args.query_id,
        method=args.method,
    )
    write_jsonl(args.output, records)
    if args.run_csv:
        write_run_csv(args.run_csv, records)
    print(f"Wrote {len(records)} retrieved documents to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
