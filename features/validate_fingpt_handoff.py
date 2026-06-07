"""Validate FinPortfolio IR exports before FinGPT Feature Engine handoff."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from finportfolio_ir.io_utils import read_jsonl
from finportfolio_ir.time_utils import parse_datetime


REQUIRED_CONTEXT_FIELDS = [
    "portfolio_id",
    "decision_time",
    "retrieval_cutoff",
    "doc_id",
    "published_at",
    "available_at",
    "title",
    "body_excerpt",
    "matched_tickers",
    "document_hash",
]

RECOMMENDED_CONTEXT_FIELDS = [
    "query_id",
    "method",
    "rank",
    "source",
    "source_type",
    "url",
    "portfolio_weight_sum",
    "sparse_score",
    "dense_score",
    "entity_score",
    "portfolio_exposure_score",
    "recency_score",
    "event_importance_score",
    "source_credibility_score",
    "final_score",
    "duplicate_cluster_id",
    "matched_holdings",
    "event_tags",
    "risk_terms",
    "evidence_scope",
    "retrieval_reason_tags",
]


def _as_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    if value is None:
        return []
    text = str(value).strip()
    if not text:
        return []
    return [part.strip() for part in text.replace(";", "|").replace(",", "|").split("|") if part.strip()]


def _safe_float(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _issue(row_number: int, issue_type: str, doc_id: object, details: str) -> dict[str, object]:
    return {
        "row_number": row_number,
        "issue_type": issue_type,
        "doc_id": str(doc_id),
        "details": details,
    }


def validate_context_records(records: list[dict[str, object]]) -> dict[str, object]:
    issues: list[dict[str, object]] = []
    scope_counts: Counter[str] = Counter()
    method_counts: Counter[str] = Counter()
    query_counts: Counter[str] = Counter()
    portfolio_counts: Counter[str] = Counter()
    holding_counts: Counter[str] = Counter()
    duplicate_clusters: Counter[str] = Counter()
    seen_context_keys: set[tuple[str, str, str]] = set()

    for row_number, record in enumerate(records, start=1):
        doc_id = record.get("doc_id", "")
        missing = [field for field in REQUIRED_CONTEXT_FIELDS if field not in record]
        if missing:
            issues.append(_issue(row_number, "missing_required_fields", doc_id, ",".join(missing)))
            continue

        recommended_missing = [field for field in RECOMMENDED_CONTEXT_FIELDS if field not in record]
        if recommended_missing:
            issues.append(_issue(row_number, "missing_recommended_fields", doc_id, ",".join(recommended_missing)))

        if not str(record.get("body_excerpt", "")).strip():
            issues.append(_issue(row_number, "empty_body_excerpt", doc_id, "body_excerpt is empty"))
        if not str(record.get("document_hash", "")).strip():
            issues.append(_issue(row_number, "empty_document_hash", doc_id, "document_hash is empty"))

        try:
            decision_time = parse_datetime(str(record["decision_time"]))
            retrieval_cutoff = parse_datetime(str(record["retrieval_cutoff"]))
            published_at = parse_datetime(str(record["published_at"]))
            available_at = parse_datetime(str(record["available_at"]))
        except ValueError as exc:
            issues.append(_issue(row_number, "invalid_timestamp", doc_id, str(exc)))
            continue

        if available_at > decision_time:
            issues.append(_issue(row_number, "available_after_decision", doc_id, "available_at > decision_time"))
        if available_at > retrieval_cutoff:
            issues.append(_issue(row_number, "available_after_cutoff", doc_id, "available_at > retrieval_cutoff"))
        if retrieval_cutoff > decision_time:
            issues.append(_issue(row_number, "cutoff_after_decision", doc_id, "retrieval_cutoff > decision_time"))
        if available_at < published_at:
            issues.append(_issue(row_number, "available_before_published", doc_id, "available_at < published_at"))

        try:
            rank = int(record.get("rank", 0) or 0)
            if rank < 1:
                issues.append(_issue(row_number, "invalid_rank", doc_id, "rank must be positive"))
        except (TypeError, ValueError):
            issues.append(_issue(row_number, "invalid_rank", doc_id, "rank is not an integer"))

        query_id = str(record.get("query_id", record.get("decision_id", "")))
        method = str(record.get("method", "run") or "run")
        context_key = (query_id, method, str(doc_id))
        if context_key in seen_context_keys:
            issues.append(_issue(row_number, "duplicate_context_row", doc_id, "|".join(context_key)))
        seen_context_keys.add(context_key)

        scope_counts[str(record.get("evidence_scope", "unknown") or "unknown")] += 1
        method_counts[method] += 1
        query_counts[query_id] += 1
        portfolio_counts[str(record.get("portfolio_id", ""))] += 1
        for holding in _as_list(record.get("matched_holdings", [])):
            holding_counts[holding.upper()] += 1
        duplicate_cluster = str(record.get("duplicate_cluster_id", "") or doc_id)
        duplicate_clusters[duplicate_cluster] += 1

    hard_issue_types = {
        "missing_required_fields",
        "empty_body_excerpt",
        "empty_document_hash",
        "invalid_timestamp",
        "available_after_decision",
        "available_after_cutoff",
        "cutoff_after_decision",
        "available_before_published",
        "invalid_rank",
        "duplicate_context_row",
    }
    hard_issues = [issue for issue in issues if issue["issue_type"] in hard_issue_types]
    scores = [_safe_float(record.get("final_score", 0.0)) for record in records]

    return {
        "status": "passed" if not hard_issues else "failed",
        "row_count": len(records),
        "unique_doc_count": len({str(record.get("doc_id", "")) for record in records}),
        "query_count": len([query for query in query_counts if query]),
        "portfolio_count": len([portfolio for portfolio in portfolio_counts if portfolio]),
        "method_counts": dict(sorted(method_counts.items())),
        "scope_counts": dict(sorted(scope_counts.items())),
        "covered_holdings": dict(sorted(holding_counts.items())),
        "duplicate_cluster_count": len(duplicate_clusters),
        "duplicate_context_cluster_rows": sum(count - 1 for count in duplicate_clusters.values() if count > 1),
        "final_score_min": min(scores) if scores else 0.0,
        "final_score_max": max(scores) if scores else 0.0,
        "issue_count": len(issues),
        "hard_issue_count": len(hard_issues),
        "issues": issues,
    }


def validate_bundle_records(
    bundles: list[dict[str, object]],
    context_report: dict[str, object],
) -> dict[str, object]:
    bundle_ids = {str(bundle.get("evidence_bundle_id", "")) for bundle in bundles if bundle.get("evidence_bundle_id")}
    issues: list[dict[str, object]] = []
    for row_number, bundle in enumerate(bundles, start=1):
        if not bundle.get("evidence_bundle_id"):
            issues.append(_issue(row_number, "missing_bundle_id", "", "evidence_bundle_id is empty"))
        portfolio_evidence = bundle.get("portfolio_evidence", [])
        if not isinstance(portfolio_evidence, list) or not portfolio_evidence:
            issues.append(_issue(row_number, "empty_portfolio_evidence", bundle.get("evidence_bundle_id", ""), "bundle has no documents"))
    return {
        "status": "passed" if not issues else "failed",
        "bundle_count": len(bundles),
        "unique_bundle_count": len(bundle_ids),
        "expected_query_count": context_report.get("query_count", 0),
        "issue_count": len(issues),
        "issues": issues,
    }


def write_report(path: str | Path, report: dict[str, object]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Validate FinGPT handoff contexts from FinPortfolio IR.")
    parser.add_argument("--contexts", required=True, help="Retrieved context JSONL for FinGPT Feature Engine.")
    parser.add_argument("--bundles", default="", help="Optional evidence bundle JSONL.")
    parser.add_argument("--output-report", default="", help="Optional JSON validation report.")
    args = parser.parse_args(argv)

    context_report = validate_context_records(read_jsonl(args.contexts))
    report: dict[str, object] = {"contexts": context_report}
    if args.bundles:
        report["bundles"] = validate_bundle_records(read_jsonl(args.bundles), context_report)

    if args.output_report:
        write_report(args.output_report, report)
    print(json.dumps(report, indent=2, ensure_ascii=False))

    failed = context_report["status"] != "passed" or (
        isinstance(report.get("bundles"), dict) and report["bundles"].get("status") != "passed"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
