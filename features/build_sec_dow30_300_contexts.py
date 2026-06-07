"""Build a 300-row Dow 30 SEC retrieved_contexts.jsonl through FinIR."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from finportfolio_ir.io_utils import read_jsonl, write_jsonl  # noqa: E402
from finportfolio_ir.time_utils import parse_datetime  # noqa: E402
from retrieval.retrieve_for_portfolio import retrieval_records  # noqa: E402


SPLIT_BOUNDARY = parse_datetime("2021-10-01T00:00:00Z")


def target_ticker(document: dict[str, Any]) -> str:
    for ticker in document.get("matched_tickers", []) or []:
        ticker_text = str(ticker).upper()
        if ticker_text and ticker_text != "MARKET":
            return ticker_text
    for ticker in document.get("tickers_detected", []) or []:
        ticker_text = str(ticker).upper()
        if ticker_text and ticker_text != "MARKET":
            return ticker_text
    sec = document.get("sec") if isinstance(document.get("sec"), dict) else {}
    return str(sec.get("ticker", "")).upper()


def split_for_decision(decision_time: str) -> str:
    return "train" if parse_datetime(decision_time) < SPLIT_BOUNDARY else "test"


def regime_for_date(date_text: str) -> str:
    if date_text < "2011-08-01":
        return "post_gfc_recovery"
    if date_text < "2013-06-01":
        return "eurozone_us_debt_volatility"
    if date_text < "2015-08-01":
        return "taper_low_volatility"
    if date_text < "2017-01-01":
        return "china_oil_growth_scare"
    if date_text < "2018-10-01":
        return "tax_reform_late_cycle"
    if date_text < "2020-02-15":
        return "trade_war_late_cycle"
    if date_text < "2020-07-01":
        return "covid_crash"
    if date_text < "2021-10-01":
        return "reopening_inflation"
    if date_text < "2022-03-01":
        return "early_fed_pivot_to_tightening"
    if date_text < "2022-07-01":
        return "inflation_bear_market"
    if date_text < "2023-01-01":
        return "aggressive_fed_tightening"
    return "pre_banking_stress_oos"


def write_single_ticker_portfolios(documents: list[dict[str, Any]], output_dir: Path) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    tickers = sorted({str(ticker) for document in documents for ticker in document.get("matched_tickers", []) if ticker != "MARKET"})
    paths = {}
    for ticker in tickers:
        path = output_dir / f"{ticker}.yaml"
        path.write_text(f"portfolio_id: sec_dow30_{ticker}\nholdings:\n  {ticker}: 1.0\n", encoding="utf-8")
        paths[ticker] = path
    return paths


def decision_after_available_at(available_at: str) -> str:
    decision = parse_datetime(available_at) + timedelta(minutes=1)
    return decision.isoformat().replace("+00:00", "Z")


def canonical_utc_second(value: str) -> str:
    return parse_datetime(value).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def build_contexts(
    documents_path: Path,
    metadata_path: Path,
    config_path: Path,
    portfolios_dir: Path,
    output_count: int,
    rank_search_k: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    documents = read_jsonl(documents_path)
    portfolio_paths = write_single_ticker_portfolios(documents, portfolios_dir)
    selected_docs = sorted(
        documents,
        key=lambda row: (
            str(row.get("split", "")),
            target_ticker(row),
            str(row.get("available_at", "")),
            str(row.get("doc_id", "")),
        ),
    )
    if len(selected_docs) < output_count:
        raise RuntimeError(f"Need at least {output_count} documents, found {len(selected_docs)}")

    contexts: list[dict[str, Any]] = []
    missing_targets: list[str] = []
    seen_doc_ids: set[str] = set()
    for index, target in enumerate(selected_docs[:output_count], start=1):
        ticker = target_ticker(target)
        if ticker not in portfolio_paths:
            missing_targets.append(str(target.get("doc_id", "")))
            continue
        decision_time = decision_after_available_at(str(target["available_at"]))
        query_id = f"sec300_{index:04d}_{split_for_decision(decision_time)}_{ticker}_{target['doc_id']}"
        records = retrieval_records(
            documents_path=documents_path,
            portfolio_path=portfolio_paths[ticker],
            metadata_path=metadata_path,
            decision_datetime_text=decision_time,
            config_path=config_path,
            top_k=rank_search_k,
            query_id=query_id,
            method="full_hybrid_diversified",
        )
        match = next((record for record in records if record["doc_id"] == target["doc_id"]), None)
        if match is None:
            missing_targets.append(str(target["doc_id"]))
            continue
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
        match["split"] = split
        match["document_split"] = split
        match["regime"] = regime_for_date(str(match["decision_time"])[:10])
        match["target_ticker"] = ticker
        match["source_route"] = "sec_filings"
        match["query_intent_primary"] = "filing_search"
        match["route_candidate_rank"] = match["rank"]
        contexts.append(match)
        seen_doc_ids.add(str(match["doc_id"]))
        if len(contexts) >= output_count:
            break

    manifest = {
        "requested_contexts": output_count,
        "retrieved_contexts": len(contexts),
        "missing_target_count": len(missing_targets),
        "missing_targets": missing_targets[:25],
        "split_counts": {
            "train": sum(1 for row in contexts if row.get("split") == "train"),
            "test": sum(1 for row in contexts if row.get("split") == "test"),
        },
        "unique_doc_ids": len({row["doc_id"] for row in contexts}),
        "unique_tickers": len({row["target_ticker"] for row in contexts}),
        "regime_counts": {
            regime: sum(1 for row in contexts if row.get("regime") == regime)
            for regime in sorted({str(row.get("regime", "")) for row in contexts})
        },
        "strict_leakage_rows": sum(
            1
            for row in contexts
            if parse_datetime(str(row["available_at"])) > parse_datetime(str(row["decision_time"]))
        ),
    }
    return contexts, manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build 300 SEC Dow 30 retrieved contexts through FinIR.")
    parser.add_argument("--documents", default="data/processed_documents/sec_dow30_documents.jsonl")
    parser.add_argument("--metadata", default="data/processed_documents/dow30_ticker_metadata.csv")
    parser.add_argument("--config", default="configs/default.yaml")
    parser.add_argument("--portfolios-dir", default="data/portfolios/sec_dow30_single")
    parser.add_argument("--output", default="data/exports/sec_dow30_2010_2023/retrieved_contexts.jsonl")
    parser.add_argument("--manifest-output", default="data/exports/sec_dow30_2010_2023/manifest.json")
    parser.add_argument("--output-count", type=int, default=300)
    parser.add_argument("--rank-search-k", type=int, default=80)
    args = parser.parse_args(argv)

    contexts, manifest = build_contexts(
        documents_path=Path(args.documents),
        metadata_path=Path(args.metadata),
        config_path=Path(args.config),
        portfolios_dir=Path(args.portfolios_dir),
        output_count=args.output_count,
        rank_search_k=args.rank_search_k,
    )
    if len(contexts) != args.output_count:
        raise RuntimeError(f"Expected {args.output_count} contexts, built {len(contexts)}")
    if manifest["strict_leakage_rows"]:
        raise RuntimeError(f"Leakage rows detected: {manifest['strict_leakage_rows']}")
    write_jsonl(args.output, contexts)
    Path(args.manifest_output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.manifest_output).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
