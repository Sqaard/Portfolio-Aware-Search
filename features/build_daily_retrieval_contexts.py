"""Build PPO-aligned daily FinPortfolio IR retrieval contexts."""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from features.build_sec_dow30_300_contexts import canonical_utc_second, regime_for_date, split_for_decision  # noqa: E402
from features.build_sec_section_contexts import _copy_section_metadata, _record_from_ranked_item  # noqa: E402
from finportfolio_ir.io_utils import read_jsonl, write_jsonl  # noqa: E402
from finportfolio_ir.schema import FinancialDocument, load_documents  # noqa: E402
from finportfolio_ir.time_utils import parse_datetime  # noqa: E402
from indexing.build_sparse_index import BM25Index  # noqa: E402
from indexing.entity_linking import enrich_document_entities, load_ticker_metadata  # noqa: E402
from retrieval.hybrid_ranker import rank_documents  # noqa: E402
from retrieval.portfolio_query_builder import build_portfolio_query  # noqa: E402
from retrieval.retrieve_for_portfolio import _load_config, _ranker_config  # noqa: E402


DATE_COLUMNS = ("date", "Date", "DATE", "datadate", "timestamp")
TICKER_COLUMNS = ("tic", "ticker", "Ticker", "symbol", "Symbol")
DEFAULT_MACRO_QUERY_TERMS = [
    "Federal Reserve",
    "rates",
    "real yields",
    "Treasury",
    "yield curve",
    "inflation",
    "CPI",
    "jobs",
    "payrolls",
    "unemployment",
    "credit spreads",
    "VIX",
    "market volatility",
    "oil",
    "housing",
    "consumer demand",
    "recession risk",
    "risk appetite",
    "sector rotation",
]


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _first_present(columns: set[str], candidates: tuple[str, ...]) -> str | None:
    for candidate in candidates:
        if candidate in columns:
            return candidate
    return None


def load_base_panel(path: Path) -> tuple[list[date], dict[str, list[str]], dict[str, Any]]:
    rows = _read_csv_rows(path)
    if not rows:
        raise ValueError(f"Base panel is empty: {path}")
    columns = set(rows[0])
    date_col = _first_present(columns, DATE_COLUMNS)
    ticker_col = _first_present(columns, TICKER_COLUMNS)
    if not date_col:
        raise ValueError(f"Base panel has no supported date column: {path}")

    by_date: dict[date, set[str]] = defaultdict(set)
    for row in rows:
        raw_date = str(row.get(date_col, "")).strip()[:10]
        if not raw_date:
            continue
        current_date = date.fromisoformat(raw_date)
        ticker = str(row.get(ticker_col, "")).upper().strip() if ticker_col else ""
        if ticker:
            by_date[current_date].add(ticker)
        else:
            by_date[current_date]

    trading_dates = sorted(by_date)
    active_by_date = {day.isoformat(): sorted(tickers) for day, tickers in by_date.items()}
    metadata = {
        "path": str(path),
        "row_count": len(rows),
        "date_column": date_col,
        "ticker_column": ticker_col or "",
        "date_min": trading_dates[0].isoformat() if trading_dates else "",
        "date_max": trading_dates[-1].isoformat() if trading_dates else "",
        "trading_date_count": len(trading_dates),
    }
    return trading_dates, active_by_date, metadata


def _decision_datetime(decision_date: date, policy: str, timezone_name: str) -> datetime:
    if policy == "market_open":
        local_time = time(9, 30)
    elif policy == "pre_open":
        local_time = time(9, 25)
    elif policy == "market_close":
        local_time = time(16, 0)
    else:
        local_time = time.fromisoformat(policy)
    return datetime.combine(decision_date, local_time, tzinfo=ZoneInfo(timezone_name))


def _load_document_records(paths_text: str, metadata_path: Path) -> list[dict[str, Any]]:
    metadata = load_ticker_metadata(metadata_path)
    records: list[dict[str, Any]] = []
    for item in paths_text.split(","):
        path_text = item.strip()
        if not path_text:
            continue
        for record in read_jsonl(path_text):
            if not record.get("tickers_detected"):
                record = enrich_document_entities(record, metadata)
            records.append(record)
    return records


def _age_bucket(age_days: float) -> str:
    if age_days <= 1:
        return "0_1d"
    if age_days <= 7:
        return "2_7d"
    if age_days <= 30:
        return "8_30d"
    if age_days <= 90:
        return "31_90d"
    return "over_90d"


def _decay(age_days: float, window: int) -> float:
    return math.exp(-max(age_days, 0.0) / max(window, 1))


def _risk_term_score(document: FinancialDocument) -> float:
    return min(1.0, len(document.risk_terms) / 6.0)


def _event_severity_score(document: FinancialDocument) -> float:
    tags = {tag.lower() for tag in document.event_tags}
    source_type = document.source_type.lower()
    if "risk_factors" in tags or "credit_stress" in tags:
        return 0.90
    if "earnings_release_candidate" in tags or "guidance" in tags:
        return 0.80
    if "official_macro" in tags or "inflation_pressure" in tags or "rates_policy" in tags:
        return 0.75
    if source_type == "sec_filing_exhibit":
        return 0.70
    if "current_report" in tags:
        return 0.65
    return 0.40


def _macro_regime_relevance_score(document: FinancialDocument) -> float:
    tags = {tag.lower() for tag in document.event_tags + document.risk_terms + document.sector_tags}
    if document.source_type.startswith("official_macro") or "market" in {ticker.lower() for ticker in document.tickers_detected}:
        return 1.0
    if tags.intersection({"inflation", "rates_policy", "credit_stress", "market_volatility", "macro_growth", "yield_curve"}):
        return 0.75
    return 0.20


def is_portfolio_level_candidate(document: FinancialDocument) -> bool:
    return (
        document.source_type.startswith("official_macro")
        or "MARKET" in document.tickers_detected
    )


def infer_query_intent(document: FinancialDocument, retrieval_layer: str) -> str:
    text = " ".join(document.event_tags + document.risk_terms + [document.event_type, document.source_type]).lower()
    if "inflation" in text or "consumer_prices" in text:
        return "inflation_pressure"
    if "rates" in text or "yield" in text or "monetary_policy" in text:
        return "rates_policy"
    if "credit" in text or "spread" in text:
        return "credit_stress"
    if "volatility" in text or "risk_appetite" in text:
        return "market_volatility"
    if "labor" in text or "growth" in text or "housing" in text:
        return "macro_growth"
    if "risk_factors" in text:
        return "company_risk"
    if "earnings" in text or "guidance" in text or "exhibit_99" in text:
        return "earnings_guidance"
    if "legal" in text or "regulation" in text or "litigation" in text:
        return "legal_regulatory"
    if retrieval_layer == "portfolio":
        return "macro_growth"
    return "company_risk"


def _enrich_daily_record(
    record: dict[str, Any],
    *,
    target: str,
    retrieval_layer: str,
    decision_dt: datetime,
    source_document: FinancialDocument,
) -> dict[str, Any]:
    age_days = max((decision_dt - parse_datetime(str(record["available_at"]))).total_seconds() / 86400.0, 0.0)
    intent = infer_query_intent(source_document, retrieval_layer)
    component_scores = {
        "bm25_score": record.get("sparse_score", 0.0),
        "entity_match_score": record.get("entity_score", 0.0),
        "portfolio_weight_score": record.get("portfolio_exposure_score", 0.0),
        "ticker_specificity_score": 1.0 if target in source_document.matched_holdings else 0.0,
        "source_reliability_score": record.get("source_credibility_score", 0.0),
        "freshness_score": record.get("recency_score", 0.0),
        "event_severity_score": round(_event_severity_score(source_document), 6),
        "risk_term_score": round(_risk_term_score(source_document), 6),
        "macro_regime_relevance_score": round(_macro_regime_relevance_score(source_document), 6),
        "diversity_penalty": 0.0,
        "duplicate_penalty": 0.0,
        "final_score": record.get("final_score", 0.0),
    }
    record.update(
        {
            "daily_context_id": f"{record['decision_date']}:{retrieval_layer}:{target}:{record['rank']}:{record['doc_id']}",
            "retrieval_layer": retrieval_layer,
            "target_ticker": target,
            "tic": "" if target == "PORTFOLIO" else target,
            "query_intent_primary": intent,
            "source_route": "daily_ppo_aligned",
            "age_days": round(age_days, 6),
            "age_bucket": _age_bucket(age_days),
            "decay_weight_7d": round(_decay(age_days, 7), 6),
            "decay_weight_30d": round(_decay(age_days, 30), 6),
            "decay_weight_90d": round(_decay(age_days, 90), 6),
            "component_scores": component_scores,
            "bm25_score": component_scores["bm25_score"],
            "risk_term_score": component_scores["risk_term_score"],
            "macro_regime_relevance_score": component_scores["macro_regime_relevance_score"],
            "event_severity_score": component_scores["event_severity_score"],
            "freshness_score": component_scores["freshness_score"],
            "document_split": split_for_decision(str(record["decision_time"])),
            "regime": regime_for_date(str(record["decision_time"])[:10]),
        }
    )
    return record


def _record_from_ranked(
    *,
    ranked_item: dict[str, Any],
    query_id: str,
    method: str,
    portfolio_id: str,
    holdings: dict[str, float],
    decision_dt: datetime,
    retrieval_query_lex: str,
    retrieval_query_sem: str,
    body_excerpt_chars: int,
    source_record: dict[str, Any] | None = None,
) -> dict[str, Any]:
    decision_time_utc = decision_dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    record = _record_from_ranked_item(
        item=ranked_item,
        query_id=query_id,
        method=method,
        portfolio_id=portfolio_id,
        holdings=holdings,
        decision_time_utc=decision_time_utc,
        decision_date=decision_dt.astimezone(timezone.utc).date().isoformat(),
        retrieval_query_lex=retrieval_query_lex,
        retrieval_query_sem=retrieval_query_sem,
        body_excerpt_chars=body_excerpt_chars,
    )
    document = ranked_item["document"]
    if source_record:
        _copy_section_metadata(record, source_record)
    for timestamp_column in ["decision_time", "decision_datetime", "retrieval_cutoff", "published_at", "available_at", "first_seen_at", "ingested_at", "last_url_check_at"]:
        if record.get(timestamp_column):
            record[timestamp_column] = canonical_utc_second(str(record[timestamp_column]))
    record["split"] = split_for_decision(str(record["decision_time"]))
    return record


def build_daily_contexts(
    *,
    base_panel: Path,
    documents: str,
    metadata_path: Path,
    config_path: Path,
    output: Path,
    manifest_output: Path,
    decision_time_policy: str,
    portfolio_top_k: int,
    ticker_top_k: int,
    ticker_date_stride: int,
    lookback_days: int,
    max_contexts_total: int,
    start_date: str,
    end_date: str,
    tickers_filter: set[str],
    method: str,
) -> dict[str, Any]:
    config = _load_config(config_path)
    retrieval_config = config.get("retrieval", {}) or {}
    timezone_name = str(retrieval_config.get("decision_timezone", "America/New_York"))
    body_excerpt_chars = int(retrieval_config.get("body_excerpt_chars", 1200))
    ranker_config = _ranker_config(config, method=method)
    metadata = load_ticker_metadata(metadata_path)

    trading_dates, active_by_date, base_meta = load_base_panel(base_panel)
    if start_date:
        start = date.fromisoformat(start_date)
        trading_dates = [day for day in trading_dates if day >= start]
    if end_date:
        end = date.fromisoformat(end_date)
        trading_dates = [day for day in trading_dates if day <= end]

    document_records = _load_document_records(documents, metadata_path)
    raw_by_doc = {str(record.get("doc_id")): record for record in document_records}
    loaded_documents = load_documents(document_records)
    available_dt_by_doc = {document.doc_id: parse_datetime(document.available_at) for document in loaded_documents}
    sparse_index = BM25Index.from_documents(loaded_documents)
    sparse_score_cache: dict[str, dict[str, float]] = {}
    contexts: list[dict[str, Any]] = []
    leakage_rows = 0
    missing_active_ticker_dates = 0

    def sparse_scores_for(cache_key: str, query_text: str) -> dict[str, float]:
        if cache_key not in sparse_score_cache:
            sparse_score_cache[cache_key] = sparse_index.score_query(query_text)
        return sparse_score_cache[cache_key]

    ticker_date_stride = max(1, ticker_date_stride)
    for date_index, day in enumerate(trading_dates):
        decision_dt = _decision_datetime(day, decision_time_policy, timezone_name)
        window_start = decision_dt - timedelta(days=lookback_days)
        candidate_documents = [
            document
            for document in loaded_documents
            if window_start <= available_dt_by_doc[document.doc_id] <= decision_dt
        ]
        if not candidate_documents:
            continue

        active_tickers = active_by_date.get(day.isoformat(), [])
        active_tickers = [ticker for ticker in active_tickers if ticker in metadata and ticker != "MARKET"]
        if tickers_filter:
            active_tickers = [ticker for ticker in active_tickers if ticker in tickers_filter]
        if not active_tickers:
            missing_active_ticker_dates += 1
            continue

        portfolio_candidate_documents = [
            document for document in candidate_documents if is_portfolio_level_candidate(document)
        ]
        if not portfolio_candidate_documents:
            portfolio_candidate_documents = candidate_documents

        portfolio_holdings = {ticker: 1.0 for ticker in active_tickers}
        portfolio_query = build_portfolio_query(
            "dow30_daily_portfolio",
            portfolio_holdings,
            metadata,
            risk_keywords=[str(item) for item in config.get("event_keywords", []) or []] + DEFAULT_MACRO_QUERY_TERMS,
        )
        portfolio_query_text = portfolio_query.query_text + " " + " ".join(DEFAULT_MACRO_QUERY_TERMS)
        ranked = []
        if portfolio_top_k > 0:
            sparse_scores = sparse_scores_for("PORTFOLIO", portfolio_query_text)
            ranked = rank_documents(
                documents=portfolio_candidate_documents,
                query=portfolio_query,
                decision_datetime=decision_dt,
                sparse_scores=sparse_scores,
                config=ranker_config,
                top_k=portfolio_top_k,
            )
        for ranked_item in ranked:
            document = ranked_item["document"]
            assert isinstance(document, FinancialDocument)
            record = _record_from_ranked(
                ranked_item=ranked_item,
                query_id=f"daily_{day.isoformat()}_portfolio",
                method=method,
                portfolio_id="dow30_daily_portfolio",
                holdings=portfolio_holdings,
                decision_dt=decision_dt,
                retrieval_query_lex=" ".join(active_tickers),
                retrieval_query_sem=portfolio_query_text,
                body_excerpt_chars=body_excerpt_chars,
                source_record=raw_by_doc.get(document.doc_id),
            )
            record = _enrich_daily_record(
                record,
                target="PORTFOLIO",
                retrieval_layer="portfolio",
                decision_dt=decision_dt,
                source_document=document,
            )
            contexts.append(record)

        if max_contexts_total > 0 and len(contexts) >= max_contexts_total:
            contexts = contexts[:max_contexts_total]
            break

        if date_index % ticker_date_stride != 0:
            continue

        for ticker in active_tickers:
            ticker_candidate_documents = [
                document
                for document in candidate_documents
                if ticker in document.matched_holdings or ticker in document.tickers_detected
            ]
            if not ticker_candidate_documents:
                continue
            ticker_holdings = {ticker: 1.0}
            ticker_query = build_portfolio_query(
                f"dow30_daily_{ticker}",
                ticker_holdings,
                metadata,
                risk_keywords=[str(item) for item in config.get("event_keywords", []) or []],
            )
            if ticker_top_k <= 0:
                continue
            sparse_scores = sparse_scores_for(ticker, ticker_query.query_text)
            ranked = rank_documents(
                documents=ticker_candidate_documents,
                query=ticker_query,
                decision_datetime=decision_dt,
                sparse_scores=sparse_scores,
                config=ranker_config,
                top_k=ticker_top_k,
            )
            for ranked_item in ranked:
                document = ranked_item["document"]
                assert isinstance(document, FinancialDocument)
                record = _record_from_ranked(
                    ranked_item=ranked_item,
                    query_id=f"daily_{day.isoformat()}_{ticker}",
                    method=method,
                    portfolio_id=f"dow30_daily_{ticker}",
                    holdings=ticker_holdings,
                    decision_dt=decision_dt,
                    retrieval_query_lex=ticker,
                    retrieval_query_sem=ticker_query.query_text,
                    body_excerpt_chars=body_excerpt_chars,
                    source_record=raw_by_doc.get(document.doc_id),
                )
                record = _enrich_daily_record(
                    record,
                    target=ticker,
                    retrieval_layer="stock",
                    decision_dt=decision_dt,
                    source_document=document,
                )
                contexts.append(record)

        if max_contexts_total > 0 and len(contexts) >= max_contexts_total:
            contexts = contexts[:max_contexts_total]
            break

    for row in contexts:
        if parse_datetime(str(row["available_at"])) > parse_datetime(str(row["decision_time"])):
            leakage_rows += 1
    write_jsonl(output, contexts)

    manifest = {
        "output": str(output),
        "document_inputs": [item.strip() for item in documents.split(",") if item.strip()],
        "document_count": len(loaded_documents),
        "base_panel": base_meta,
        "base_panel_date_range": {"start": base_meta["date_min"], "end": base_meta["date_max"]},
        "base_panel_trading_calendar": {
            "count": len(trading_dates),
            "first": trading_dates[0].isoformat() if trading_dates else "",
            "last": trading_dates[-1].isoformat() if trading_dates else "",
        },
        "decision_time_policy": decision_time_policy,
        "decision_timezone": timezone_name,
        "active_universe_by_date": {
            "date_count": len(active_by_date),
            "min_count": min((len(v) for v in active_by_date.values()), default=0),
            "max_count": max((len(v) for v in active_by_date.values()), default=0),
        },
        "parameters": {
            "portfolio_top_k_per_day": portfolio_top_k,
            "ticker_top_k_per_day": ticker_top_k,
            "ticker_date_stride": ticker_date_stride,
            "lookback_days": lookback_days,
            "max_contexts_total": max_contexts_total,
            "method": method,
        },
        "row_count": len(contexts),
        "retrieval_layer_counts": dict(Counter(str(row.get("retrieval_layer", "")) for row in contexts)),
        "query_intent_counts": dict(Counter(str(row.get("query_intent_primary", "")) for row in contexts)),
        "source_type_counts": dict(Counter(str(row.get("source_type", "")) for row in contexts)),
        "split_counts": dict(Counter(str(row.get("split", "")) for row in contexts)),
        "document_split_counts": dict(Counter(str(row.get("document_split", "")) for row in contexts)),
        "age_bucket_counts": dict(Counter(str(row.get("age_bucket", "")) for row in contexts)),
        "unique_doc_ids": len({str(row.get("doc_id", "")) for row in contexts}),
        "unique_decision_dates": len({str(row.get("decision_date", "")) for row in contexts}),
        "unique_tickers": len({str(row.get("target_ticker", "")) for row in contexts if row.get("target_ticker") != "PORTFOLIO"}),
        "sparse_query_cache_entries": len(sparse_score_cache),
        "strict_leakage_rows": leakage_rows,
        "missing_active_ticker_dates": missing_active_ticker_dates,
    }
    manifest_output.parent.mkdir(parents=True, exist_ok=True)
    manifest_output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build PPO-aligned daily retrieval contexts.")
    parser.add_argument("--base-panel", required=True)
    parser.add_argument("--documents", required=True, help="Comma-separated processed document JSONL paths.")
    parser.add_argument("--metadata", default="data/processed_documents/dow30_ticker_metadata.csv")
    parser.add_argument("--config", default="configs/default.yaml")
    parser.add_argument("--decision-time-policy", default="market_open")
    parser.add_argument("--portfolio-top-k", type=int, default=10)
    parser.add_argument("--ticker-top-k", type=int, default=3)
    parser.add_argument(
        "--ticker-date-stride",
        type=int,
        default=1,
        help="Run stock-level retrieval every Nth trading date while keeping portfolio retrieval daily.",
    )
    parser.add_argument("--lookback-days", type=int, default=90)
    parser.add_argument("--max-contexts-total", type=int, default=0)
    parser.add_argument("--start-date", default="")
    parser.add_argument("--end-date", default="")
    parser.add_argument("--tickers", default="")
    parser.add_argument("--method", default="full_hybrid_diversified")
    parser.add_argument("--output", default="data/exports/daily_retrieval/retrieved_contexts_daily.jsonl")
    parser.add_argument("--manifest-output", default="data/exports/daily_retrieval/manifest_daily.json")
    args = parser.parse_args(argv)

    manifest = build_daily_contexts(
        base_panel=Path(args.base_panel),
        documents=args.documents,
        metadata_path=Path(args.metadata),
        config_path=Path(args.config),
        output=Path(args.output),
        manifest_output=Path(args.manifest_output),
        decision_time_policy=args.decision_time_policy,
        portfolio_top_k=args.portfolio_top_k,
        ticker_top_k=args.ticker_top_k,
        ticker_date_stride=args.ticker_date_stride,
        lookback_days=args.lookback_days,
        max_contexts_total=args.max_contexts_total,
        start_date=args.start_date,
        end_date=args.end_date,
        tickers_filter={item.strip().upper() for item in args.tickers.split(",") if item.strip()},
        method=args.method,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0 if manifest["strict_leakage_rows"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
