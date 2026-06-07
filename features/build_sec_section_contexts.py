"""Build FinGPT retrieved contexts from representative full SEC sections."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from features.build_sec_dow30_300_contexts import (  # noqa: E402
    canonical_utc_second,
    decision_after_available_at,
    regime_for_date,
    split_for_decision,
    target_ticker,
    write_single_ticker_portfolios,
)
from finportfolio_ir.io_utils import read_jsonl, write_jsonl  # noqa: E402
from finportfolio_ir.schema import FinancialDocument  # noqa: E402
from finportfolio_ir.time_utils import parse_datetime, parse_decision_datetime  # noqa: E402
from indexing.build_sparse_index import BM25Index  # noqa: E402
from indexing.entity_linking import load_ticker_metadata  # noqa: E402
from retrieval.hybrid_ranker import rank_documents  # noqa: E402
from retrieval.portfolio_query_builder import build_portfolio_query  # noqa: E402
from retrieval.retrieve_for_portfolio import _load_config, _load_documents_with_entities, _ranker_config  # noqa: E402


SECTION_PRIORITY = {
    "10-K": {
        "item_7_mda": 1,
        "item_1a_risk_factors": 2,
        "item_1_business": 3,
        "item_8_financial_statements": 4,
        "item_7a_market_risk": 5,
        "full_filing": 9,
    },
    "10-Q": {
        "part1_item_2_mda": 1,
        "part2_item_1a_risk_factors": 2,
        "part1_item_1_financial_statements": 3,
        "part1_item_3_market_risk": 4,
        "part2_item_1_legal_proceedings": 5,
        "part1_item_4_controls": 6,
        "full_filing": 9,
    },
    "8-K": {
        "item_2_02_results_operations_financial_condition": 2,
        "item_8_01_other_events": 3,
        "item_1_01_entry_into_material_definitive_agreement": 4,
        "item_5_02_director_or_officer_changes": 5,
        "item_7_01_regulation_fd_disclosure": 6,
        "item_9_01_financial_statements_exhibits": 7,
        "full_filing": 9,
    },
}


SECTION_METADATA_FIELDS = [
    "parent_doc_id",
    "sec_ticker",
    "sec_form",
    "sec_accession_number",
    "section_id",
    "sec_section_id",
    "sec_section_code",
    "sec_section_title",
    "sec_section_ordinal",
    "sec_section_start_char",
    "sec_section_end_char",
    "sec_section_chars",
    "section_truncated",
    "full_fetch_status",
    "full_downloaded_bytes",
    "full_text_chars",
    "sec_exhibit_id",
    "sec_exhibit_name",
    "sec_exhibit_url",
    "sec_exhibit_size",
    "sec_exhibit_last_modified",
]


def section_priority(row: dict[str, Any]) -> tuple[int, int, str]:
    form = str(row.get("sec_form", "")).upper()
    section_id = str(row.get("sec_section_id") or row.get("section_id") or "")
    if form == "8-K" and section_id.startswith("exhibit_99"):
        return (0, -int(row.get("sec_section_chars") or 0), section_id)
    if form == "8-K" and section_id.startswith("exhibit_10"):
        return (4, -int(row.get("sec_section_chars") or 0), section_id)
    if form == "8-K" and str(row.get("source_type", "")) == "sec_filing_exhibit":
        return (8, -int(row.get("sec_section_chars") or 0), section_id)
    priority = SECTION_PRIORITY.get(form, {}).get(section_id, 8)
    section_chars = int(row.get("sec_section_chars") or 0)
    return (priority, -section_chars, section_id)


def select_representative_sections(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_parent: dict[str, list[dict[str, Any]]] = {}
    for row in documents:
        parent = str(row.get("parent_doc_id") or row.get("doc_id"))
        by_parent.setdefault(parent, []).append(row)
    selected = [sorted(rows, key=section_priority)[0] for rows in by_parent.values()]
    selected.sort(
        key=lambda row: (
            str(row.get("split", "")),
            target_ticker(row),
            str(row.get("available_at", "")),
            str(row.get("parent_doc_id", "")),
            str(row.get("doc_id", "")),
        )
    )
    return selected


def _copy_section_metadata(match: dict[str, Any], source: dict[str, Any]) -> None:
    for field in SECTION_METADATA_FIELDS:
        if field in source:
            match[field] = source[field]


def _record_from_ranked_item(
    *,
    item: dict[str, Any],
    query_id: str,
    method: str,
    portfolio_id: str,
    holdings: dict[str, float],
    decision_time_utc: str,
    decision_date: str,
    retrieval_query_lex: str,
    retrieval_query_sem: str,
    body_excerpt_chars: int,
) -> dict[str, Any]:
    document = item["document"]
    assert isinstance(document, FinancialDocument)
    return {
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
        "retrieval_query_lex": retrieval_query_lex,
        "retrieval_query_sem": retrieval_query_sem,
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


def build_section_contexts(
    *,
    documents_path: Path,
    metadata_path: Path,
    config_path: Path,
    portfolios_dir: Path,
    output_count: int,
    rank_search_k: int,
    method: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    document_records = read_jsonl(documents_path)
    selected_docs = select_representative_sections(document_records)
    if len(selected_docs) < output_count:
        raise RuntimeError(f"Need at least {output_count} representative sections, found {len(selected_docs)}")
    selected_docs = selected_docs[:output_count]

    write_single_ticker_portfolios(selected_docs, portfolios_dir)
    config = _load_config(config_path)
    retrieval_config = config.get("retrieval", {}) or {}
    body_excerpt_chars = int(retrieval_config.get("body_excerpt_chars", 1200))
    decision_timezone = str(retrieval_config.get("decision_timezone", "America/New_York"))
    metadata = load_ticker_metadata(metadata_path)
    loaded_documents = _load_documents_with_entities(documents_path, metadata_path)
    sparse_index = BM25Index.from_documents(loaded_documents)
    ranker_config = _ranker_config(config, method=method)

    contexts: list[dict[str, Any]] = []
    missing_targets: list[str] = []
    seen_doc_ids: set[str] = set()
    for index, target in enumerate(selected_docs, start=1):
        ticker = target_ticker(target)
        if not ticker:
            missing_targets.append(str(target.get("doc_id", "")))
            continue
        decision_time = decision_after_available_at(str(target["available_at"]))
        query_id = f"sec_sections_{index:04d}_{split_for_decision(decision_time)}_{ticker}_{target['doc_id']}"
        decision_datetime = parse_decision_datetime(decision_time, default_timezone=decision_timezone)
        portfolio_id = f"sec_dow30_{ticker}"
        holdings = {ticker: 1.0}
        query = build_portfolio_query(
            portfolio_id,
            holdings,
            metadata,
            risk_keywords=[str(item) for item in config.get("event_keywords", []) or []],
        )
        sparse_scores = sparse_index.score_query(query.query_text)
        ranked = rank_documents(
            documents=loaded_documents,
            query=query,
            decision_datetime=decision_datetime,
            sparse_scores=sparse_scores,
            config=ranker_config,
            top_k=rank_search_k,
        )
        ranked_match = next((record for record in ranked if record["doc_id"] == target["doc_id"]), None)
        if ranked_match is None:
            missing_targets.append(str(target["doc_id"]))
            continue
        decision_time_utc = decision_datetime.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        decision_date = decision_datetime.astimezone(timezone.utc).date().isoformat()
        match = _record_from_ranked_item(
            item=ranked_match,
            query_id=query_id,
            method=method,
            portfolio_id=portfolio_id,
            holdings=holdings,
            decision_time_utc=decision_time_utc,
            decision_date=decision_date,
            retrieval_query_lex=" ".join(query.tickers),
            retrieval_query_sem=query.query_text,
            body_excerpt_chars=body_excerpt_chars,
        )
        if str(match["doc_id"]) in seen_doc_ids:
            continue

        split = split_for_decision(str(match["decision_time"]))
        available_at = parse_datetime(str(match["available_at"]))
        decision_dt = parse_datetime(str(match["decision_time"]))
        if available_at > decision_dt:
            raise RuntimeError(f"Leakage: {match['doc_id']} available_at is after decision_time")
        for timestamp_column in [
            "decision_time",
            "decision_datetime",
            "retrieval_cutoff",
            "published_at",
            "available_at",
            "first_seen_at",
            "ingested_at",
            "last_url_check_at",
        ]:
            if match.get(timestamp_column):
                match[timestamp_column] = canonical_utc_second(str(match[timestamp_column]))
        _copy_section_metadata(match, target)
        match["split"] = split
        match["document_split"] = split
        match["regime"] = regime_for_date(str(match["decision_time"])[:10])
        match["target_ticker"] = ticker
        match["source_route"] = "sec_filing_sections"
        match["query_intent_primary"] = "filing_search"
        match["route_candidate_rank"] = match["rank"]
        contexts.append(match)
        seen_doc_ids.add(str(match["doc_id"]))

    manifest = {
        "requested_contexts": output_count,
        "retrieved_contexts": len(contexts),
        "representative_section_count": len(selected_docs),
        "missing_target_count": len(missing_targets),
        "missing_targets": missing_targets[:25],
        "split_counts": {
            "train": sum(1 for row in contexts if row.get("split") == "train"),
            "test": sum(1 for row in contexts if row.get("split") == "test"),
        },
        "unique_doc_ids": len({row["doc_id"] for row in contexts}),
        "unique_parent_doc_ids": len({row.get("parent_doc_id", "") for row in contexts}),
        "unique_tickers": len({row["target_ticker"] for row in contexts}),
        "section_counts": {
            section_id: sum(1 for row in contexts if row.get("sec_section_id") == section_id)
            for section_id in sorted({str(row.get("sec_section_id", "")) for row in contexts})
        },
        "regime_counts": {
            regime: sum(1 for row in contexts if row.get("regime") == regime)
            for regime in sorted({str(row.get("regime", "")) for row in contexts})
        },
        "strict_leakage_rows": sum(
            1
            for row in contexts
            if parse_datetime(str(row["available_at"])) > parse_datetime(str(row["decision_time"]))
        ),
        "method": method,
    }
    return contexts, manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build FinGPT contexts from representative SEC sections.")
    parser.add_argument("--documents", default="data/processed_documents/sec_dow30_2010_2023_300_sections_documents.jsonl")
    parser.add_argument("--metadata", default="data/processed_documents/dow30_ticker_metadata.csv")
    parser.add_argument("--config", default="configs/default.yaml")
    parser.add_argument("--portfolios-dir", default="data/portfolios/sec_dow30_single")
    parser.add_argument("--output", default="data/exports/sec_dow30_2010_2023_sections/retrieved_contexts.jsonl")
    parser.add_argument("--manifest-output", default="data/exports/sec_dow30_2010_2023_sections/manifest.json")
    parser.add_argument("--output-count", type=int, default=300)
    parser.add_argument("--rank-search-k", type=int, default=1000)
    parser.add_argument("--method", default="full_hybrid_diversified")
    args = parser.parse_args(argv)

    contexts, manifest = build_section_contexts(
        documents_path=Path(args.documents),
        metadata_path=Path(args.metadata),
        config_path=Path(args.config),
        portfolios_dir=Path(args.portfolios_dir),
        output_count=args.output_count,
        rank_search_k=args.rank_search_k,
        method=args.method,
    )
    if len(contexts) != args.output_count:
        raise RuntimeError(f"Expected {args.output_count} contexts, built {len(contexts)}")
    if manifest["strict_leakage_rows"]:
        raise RuntimeError(f"Leakage rows detected: {manifest['strict_leakage_rows']}")
    write_jsonl(args.output, contexts)
    Path(args.manifest_output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.manifest_output).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
