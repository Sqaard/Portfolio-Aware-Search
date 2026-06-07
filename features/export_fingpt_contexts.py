"""Convert retrieval results into FinGPT-ready evidence contexts."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from finportfolio_ir.io_utils import read_jsonl, write_jsonl


REQUIRED_FIELDS = [
    "portfolio_id",
    "decision_date",
    "decision_time",
    "retrieval_cutoff",
    "rank",
    "doc_id",
    "published_at",
    "available_at",
    "title",
    "body_excerpt",
    "matched_tickers",
    "portfolio_weight_sum",
    "sparse_score",
    "dense_score",
    "entity_score",
    "recency_score",
    "event_importance_score",
    "final_score",
    "document_hash",
]


def build_fingpt_context(record: dict[str, object]) -> str:
    tickers = ", ".join(str(ticker) for ticker in record.get("matched_tickers", [])) or "MARKET"
    reason_tags = ", ".join(str(tag) for tag in record.get("retrieval_reason_tags", []))
    return "\n".join(
        [
            f"Document title: {record.get('title', '')}",
            f"Published at: {record.get('published_at', '')}",
            f"Available at: {record.get('available_at', '')}",
            f"Related tickers: {tickers}",
            f"Evidence scope: {record.get('evidence_scope', '')}",
            f"Portfolio exposure: {record.get('portfolio_weight_sum', 0)}",
            f"Retrieval score: {record.get('final_score', 0)}",
            f"Retrieval reasons: {reason_tags}",
            f"Text excerpt: {record.get('body_excerpt', '')}",
        ]
    )


def export_context_records(records: list[dict[str, object]]) -> list[dict[str, object]]:
    exported: list[dict[str, object]] = []
    for record in records:
        missing = [field for field in REQUIRED_FIELDS if field not in record]
        if missing:
            raise ValueError(f"Retrieval record {record.get('doc_id', '<missing>')} missing fields: {missing}")
        context = {field: record[field] for field in REQUIRED_FIELDS}
        context["source"] = record.get("source", "")
        context["source_type"] = record.get("source_type", "")
        context["source_registry_id"] = record.get("source_registry_id", "")
        context["source_reliability_tier"] = record.get("source_reliability_tier", "")
        context["robots_policy"] = record.get("robots_policy", "")
        context["content_license_note"] = record.get("content_license_note", "")
        context["url"] = record.get("url", "")
        context["canonical_url"] = record.get("canonical_url", record.get("url", ""))
        context["last_url_check_at"] = record.get("last_url_check_at", "")
        context["fetch_status"] = record.get("fetch_status", "")
        context["method"] = record.get("method", "")
        context["decision_id"] = record.get("decision_id", record.get("query_id", ""))
        context["query_id"] = record.get("query_id", "")
        context["portfolio_snapshot_id"] = record.get("portfolio_snapshot_id", record.get("portfolio_id", ""))
        context["evidence_bundle_id"] = record.get("evidence_bundle_id", "")
        context["retrieval_query_lex"] = record.get("retrieval_query_lex", "")
        context["retrieval_query_sem"] = record.get("retrieval_query_sem", "")
        context["first_seen_at"] = record.get("first_seen_at", "")
        context["ingested_at"] = record.get("ingested_at", "")
        context["duplicate_cluster_id"] = record.get("duplicate_cluster_id", "")
        context["matched_holdings"] = record.get("matched_holdings", [])
        context["event_tags"] = record.get("event_tags", [])
        context["risk_terms"] = record.get("risk_terms", [])
        context["evidence_scope"] = record.get("evidence_scope", "")
        context["source_credibility"] = record.get("source_credibility", 0.0)
        context["source_credibility_score"] = record.get("source_credibility_score", 0.0)
        context["portfolio_exposure_score"] = record.get("portfolio_exposure_score", 0.0)
        context["retrieval_reason_tags"] = record.get("retrieval_reason_tags", [])
        context["diversification_applied"] = record.get("diversification_applied", False)
        context["ranking_stage"] = record.get("ranking_stage", "")
        context["reason"] = record.get("reason", "")
        context["split"] = record.get("split", "")
        context["document_split"] = record.get("document_split", "")
        context["regime"] = record.get("regime", "")
        context["query_notes"] = record.get("query_notes", "")
        context["fingpt_context"] = build_fingpt_context(record)
        exported.append(context)
    return exported


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Create FinGPT-ready context JSONL.")
    parser.add_argument("--input", required=True, help="Retrieval results JSONL.")
    parser.add_argument("--output", required=True, help="FinGPT context JSONL.")
    args = parser.parse_args(argv)

    records = export_context_records(read_jsonl(args.input))
    write_jsonl(args.output, records)
    print(f"Wrote {len(records)} FinGPT contexts to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
