"""Aggregate retrieval outputs into diagnostic numeric features."""

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


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def build_portfolio_features(records: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    for record in records:
        grouped[(str(record["decision_date"]), str(record["portfolio_id"]))].append(record)

    rows: list[dict[str, object]] = []
    for (date, portfolio_id), items in sorted(grouped.items()):
        scores = sorted((float(item.get("final_score", 0.0)) for item in items), reverse=True)
        rows.append(
            {
                "date": date,
                "portfolio_id": portfolio_id,
                "portfolio_news_volume": len(items),
                "portfolio_relevance_top1": scores[0] if scores else 0.0,
                "portfolio_relevance_top5_mean": _mean(scores[:5]),
                "portfolio_event_intensity": _mean([float(item.get("event_importance_score", 0.0)) for item in items]),
                "portfolio_source_credibility_mean": _mean(
                    [float(item.get("source_credibility_score", item.get("source_credibility", 0.0)) or 0.0) for item in items]
                ),
                "portfolio_recency_weighted_score": _mean(
                    [
                        float(item.get("final_score", 0.0)) * float(item.get("recency_score", 0.0))
                        for item in items
                    ]
                ),
                "portfolio_entity_match_count": sum(1 for item in items if item.get("matched_tickers")),
                "portfolio_stock_evidence_count": sum(1 for item in items if item.get("evidence_scope") == "stock"),
                "portfolio_sector_evidence_count": sum(1 for item in items if item.get("evidence_scope") == "sector"),
                "portfolio_market_evidence_count": sum(1 for item in items if item.get("evidence_scope") == "market"),
            }
        )
    return rows


def build_ticker_features(records: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str, str], list[dict[str, object]]] = defaultdict(list)
    for record in records:
        tickers = record.get("matched_tickers") or ["MARKET"]
        for ticker in tickers:
            grouped[(str(record["decision_date"]), str(record["portfolio_id"]), str(ticker).upper())].append(record)

    rows: list[dict[str, object]] = []
    for (date, portfolio_id, ticker), items in sorted(grouped.items()):
        scores = sorted((float(item.get("final_score", 0.0)) for item in items), reverse=True)
        rows.append(
            {
                "date": date,
                "portfolio_id": portfolio_id,
                "tic": ticker,
                "ticker_news_volume": len(items),
                "ticker_relevance_top1": scores[0] if scores else 0.0,
                "ticker_relevance_top5_mean": _mean(scores[:5]),
                "ticker_event_count": sum(1 for item in items if float(item.get("event_importance_score", 0.0)) > 0),
            }
        )
    return rows


def _write_csv(path: Union[str, Path], rows: list[dict[str, object]]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Build diagnostic retrieval features.")
    parser.add_argument("--input", required=True, help="Retrieval results JSONL.")
    parser.add_argument("--portfolio-output", required=True)
    parser.add_argument("--ticker-output", required=True)
    args = parser.parse_args(argv)

    records = read_jsonl(args.input)
    _write_csv(args.portfolio_output, build_portfolio_features(records))
    _write_csv(args.ticker_output, build_ticker_features(records))
    print(f"Wrote retrieval feature CSVs to {args.portfolio_output} and {args.ticker_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
