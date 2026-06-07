"""Group retrieved documents into FinGPT-ready evidence bundles."""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from finportfolio_ir.io_utils import read_jsonl, write_jsonl


def _compact_document(record: dict[str, object]) -> dict[str, object]:
    return {
        "rank": record.get("rank", 0),
        "doc_id": record.get("doc_id", ""),
        "title": record.get("title", ""),
        "published_at": record.get("published_at", ""),
        "available_at": record.get("available_at", ""),
        "source": record.get("source", ""),
        "source_type": record.get("source_type", ""),
        "source_registry_id": record.get("source_registry_id", ""),
        "source_reliability_tier": record.get("source_reliability_tier", ""),
        "url": record.get("url", ""),
        "canonical_url": record.get("canonical_url", record.get("url", "")),
        "fetch_status": record.get("fetch_status", ""),
        "duplicate_cluster_id": record.get("duplicate_cluster_id", ""),
        "matched_tickers": record.get("matched_tickers", []),
        "matched_holdings": record.get("matched_holdings", []),
        "event_tags": record.get("event_tags", []),
        "risk_terms": record.get("risk_terms", []),
        "evidence_scope": record.get("evidence_scope", ""),
        "retrieval_reason_tags": record.get("retrieval_reason_tags", []),
        "portfolio_weight_sum": record.get("portfolio_weight_sum", 0.0),
        "final_score": record.get("final_score", 0.0),
        "body_excerpt": record.get("body_excerpt", ""),
    }


def _scope(records: list[dict[str, object]], scope: str) -> list[dict[str, object]]:
    return [_compact_document(record) for record in records if record.get("evidence_scope") == scope]


def _unique_count(values: list[object]) -> int:
    return len({str(value) for value in values if str(value)})


def build_evidence_bundles(records: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    for record in records:
        key = (str(record.get("query_id", "")), str(record.get("method", "run") or "run"))
        grouped[key].append(record)

    bundles: list[dict[str, object]] = []
    for (query_id, method), rows in sorted(grouped.items()):
        rows.sort(key=lambda row: int(row.get("rank", 0) or 0))
        first = rows[0] if rows else {}
        covered_holdings = sorted(
            {
                str(ticker).upper()
                for row in rows
                for ticker in (row.get("matched_holdings", []) or [])
                if str(ticker)
            }
        )
        duplicate_clusters = [
            row.get("duplicate_cluster_id", row.get("doc_id", ""))
            for row in rows
        ]
        bundle_id = str(first.get("evidence_bundle_id") or f"{query_id}:{method}")
        bundles.append(
            {
                "evidence_bundle_id": bundle_id,
                "query_id": query_id,
                "decision_id": first.get("decision_id", query_id),
                "method": method,
                "portfolio_id": first.get("portfolio_id", ""),
                "portfolio_snapshot_id": first.get("portfolio_snapshot_id", first.get("portfolio_id", "")),
                "portfolio_holdings": first.get("portfolio_holdings", []),
                "decision_time": first.get("decision_time", ""),
                "retrieval_cutoff": first.get("retrieval_cutoff", ""),
                "retrieval_query_lex": first.get("retrieval_query_lex", ""),
                "retrieval_query_sem": first.get("retrieval_query_sem", ""),
                "stock_evidence": _scope(rows, "stock"),
                "sector_evidence": _scope(rows, "sector"),
                "market_evidence": _scope(rows, "market"),
                "portfolio_evidence": [_compact_document(row) for row in rows],
                "diagnostics": {
                    "document_count": len(rows),
                    "unique_duplicate_clusters": _unique_count(duplicate_clusters),
                    "covered_holdings": covered_holdings,
                    "covered_holding_count": len(covered_holdings),
                    "stock_evidence_count": sum(1 for row in rows if row.get("evidence_scope") == "stock"),
                    "sector_evidence_count": sum(1 for row in rows if row.get("evidence_scope") == "sector"),
                    "market_evidence_count": sum(1 for row in rows if row.get("evidence_scope") == "market"),
                },
            }
        )
    return bundles


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Create grouped evidence bundles from retrieval JSONL.")
    parser.add_argument("--input", required=True, help="Retrieval results JSONL.")
    parser.add_argument("--output", required=True, help="Evidence bundle JSONL.")
    args = parser.parse_args(argv)

    bundles = build_evidence_bundles(read_jsonl(args.input))
    write_jsonl(args.output, bundles)
    print(f"Wrote {len(bundles)} evidence bundles to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
