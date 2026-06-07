"""Compute portfolio-aware retrieval diagnostics from retrieval JSONL."""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path
from typing import Optional, Union

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from finportfolio_ir.io_utils import read_jsonl
from finportfolio_ir.time_utils import parse_datetime


def _as_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    if value is None:
        return []
    text = str(value).strip()
    if not text:
        return []
    return [part.strip() for part in text.replace(";", "|").split("|") if part.strip()]


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def group_records(records: list[dict[str, object]]) -> dict[tuple[str, str], list[dict[str, object]]]:
    grouped: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    for record in records:
        method = str(record.get("method", "run") or "run")
        query_id = str(record.get("query_id", ""))
        grouped[(method, query_id)].append(record)
    for rows in grouped.values():
        rows.sort(key=lambda row: int(row.get("rank", 0) or 0))
    return grouped


def evaluate_query_diagnostics(records: list[dict[str, object]], k: int) -> dict[str, object]:
    top = records[:k]
    denominator = len(top)
    if denominator == 0:
        return {
            "causal_validity_at_k": 0.0,
            "duplicate_rate_at_k": 0.0,
            "portfolio_coverage_at_k": 0.0,
            "stock_scope_rate_at_k": 0.0,
            "sector_scope_rate_at_k": 0.0,
            "market_scope_rate_at_k": 0.0,
            "source_credibility_at_k": 0.0,
            "unique_duplicate_clusters_at_k": 0,
            "covered_holdings_at_k": 0,
            "portfolio_holding_count": 0,
        }

    causal_safe = 0
    duplicate_keys: list[str] = []
    covered_holdings: set[str] = set()
    portfolio_holdings: set[str] = set()
    scope_counts: dict[str, int] = defaultdict(int)
    source_credibility_values: list[float] = []

    for record in top:
        try:
            if parse_datetime(str(record["available_at"])) <= parse_datetime(str(record["retrieval_cutoff"])):
                causal_safe += 1
        except (KeyError, ValueError):
            pass

        duplicate_key = str(record.get("duplicate_cluster_id", "") or record.get("doc_id", ""))
        duplicate_keys.append(duplicate_key)
        covered_holdings.update(ticker.upper() for ticker in _as_list(record.get("matched_holdings", [])))
        portfolio_holdings.update(ticker.upper() for ticker in _as_list(record.get("portfolio_holdings", [])))
        scope_counts[str(record.get("evidence_scope", "") or "unknown")] += 1
        try:
            source_credibility_values.append(float(record.get("source_credibility_score", record.get("source_credibility", 0.0)) or 0.0))
        except ValueError:
            source_credibility_values.append(0.0)

    unique_clusters = len(set(duplicate_keys))
    duplicate_rate = 1.0 - (unique_clusters / denominator)
    if portfolio_holdings:
        coverage = len(covered_holdings.intersection(portfolio_holdings)) / len(portfolio_holdings)
    else:
        coverage = 0.0

    return {
        "causal_validity_at_k": causal_safe / denominator,
        "duplicate_rate_at_k": duplicate_rate,
        "portfolio_coverage_at_k": coverage,
        "stock_scope_rate_at_k": scope_counts.get("stock", 0) / denominator,
        "sector_scope_rate_at_k": scope_counts.get("sector", 0) / denominator,
        "market_scope_rate_at_k": scope_counts.get("market", 0) / denominator,
        "source_credibility_at_k": _mean(source_credibility_values),
        "unique_duplicate_clusters_at_k": unique_clusters,
        "covered_holdings_at_k": len(covered_holdings.intersection(portfolio_holdings)) if portfolio_holdings else len(covered_holdings),
        "portfolio_holding_count": len(portfolio_holdings),
    }


def evaluate_diagnostics(records: list[dict[str, object]], k: int = 10) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for (method, query_id), group in sorted(group_records(records).items()):
        diagnostics = evaluate_query_diagnostics(group, k)
        rows.append(
            {
                "query_id": query_id,
                "method": method,
                "k": k,
                **diagnostics,
            }
        )
    return rows


def summarize_diagnostics(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["method"])].append(row)

    summary: list[dict[str, object]] = []
    metric_names = [
        "causal_validity_at_k",
        "duplicate_rate_at_k",
        "portfolio_coverage_at_k",
        "stock_scope_rate_at_k",
        "sector_scope_rate_at_k",
        "market_scope_rate_at_k",
        "source_credibility_at_k",
    ]
    for method, method_rows in sorted(grouped.items()):
        aggregate: dict[str, object] = {
            "method": method,
            "query_count": len(method_rows),
            "k": method_rows[0].get("k", ""),
        }
        for metric in metric_names:
            aggregate[metric] = _mean([float(row[metric]) for row in method_rows])
        summary.append(aggregate)
    return summary


def write_csv(path: Union[str, Path], rows: list[dict[str, object]]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys()) if rows else ["query_id", "method"]
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Compute causal, duplicate, and coverage diagnostics.")
    parser.add_argument("--input", required=True, help="Retrieval JSONL.")
    parser.add_argument("--output", required=True, help="Per-query diagnostics CSV.")
    parser.add_argument("--summary-output", default="", help="Per-method diagnostics summary CSV.")
    parser.add_argument("--k", type=int, default=10)
    args = parser.parse_args(argv)

    rows = evaluate_diagnostics(read_jsonl(args.input), k=args.k)
    write_csv(args.output, rows)
    if args.summary_output:
        write_csv(args.summary_output, summarize_diagnostics(rows))
    for row in rows:
        print(row)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
