"""Build a browser/search-ready SQLite FTS index for FinPortfolio IR.

The index is a derived artifact. The processed JSONL documents and extracted
feature CSV remain the source of truth; this file only precomputes lookup tables
so the local UI can search the full corpus quickly.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from finportfolio_ir.io_utils import local_project_path, read_jsonl  # noqa: E402
from web_app import (  # noqa: E402
    FEATURE_RELATIONS_PATH,
    FULL_DOCUMENTS_PATH,
    SIGNAL_FEATURE_COLUMNS,
    SIGNAL_FLAG_COLUMNS,
    TEXT_FEATURES_PATH,
    FinPortfolioWebService,
)

DEFAULT_OUTPUT = ROOT / "data" / "search_index" / "finportfolio_search.sqlite"


def _json_list(value: Any) -> str:
    if isinstance(value, list):
        return json.dumps(value, ensure_ascii=False)
    if value in (None, ""):
        return "[]"
    return json.dumps([value], ensure_ascii=False)


def _join_terms(value: Any) -> str:
    if isinstance(value, list):
        return " ".join(str(item) for item in value if item is not None)
    return str(value or "")


def _source_family(record: dict[str, Any]) -> str:
    source_type = str(record.get("source_type", "") or "").lower()
    source = str(record.get("source", "") or "").lower()
    url = str(record.get("canonical_url") or record.get("url") or "").lower()
    if source_type.startswith("official_macro") or "fred.stlouisfed.org" in url:
        return "official_macro"
    if source_type.startswith("sec_filing") or "sec.gov" in url or "edgar" in source:
        return "sec_edgar"
    if source_type.startswith("company_"):
        return "company_ir"
    if source_type == "sample":
        return "sample"
    return "other"


def _connect(output: Path) -> sqlite3.Connection:
    output.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(output)
    connection.execute("PRAGMA journal_mode=OFF")
    connection.execute("PRAGMA synchronous=OFF")
    connection.execute("PRAGMA temp_store=MEMORY")
    return connection


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        DROP TABLE IF EXISTS manifest;
        DROP TABLE IF EXISTS documents;
        DROP TABLE IF EXISTS document_features;
        DROP TABLE IF EXISTS source_quality;
        DROP TABLE IF EXISTS ticker_coverage;
        DROP TABLE IF EXISTS documents_fts;

        CREATE TABLE manifest (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE documents (
            doc_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            source TEXT NOT NULL,
            source_type TEXT NOT NULL,
            source_family TEXT NOT NULL,
            source_reliability_tier TEXT NOT NULL,
            url TEXT NOT NULL,
            canonical_url TEXT NOT NULL,
            published_at TEXT NOT NULL,
            first_seen_at TEXT NOT NULL,
            available_at TEXT NOT NULL,
            document_split TEXT NOT NULL,
            document_hash TEXT NOT NULL,
            duplicate_cluster_id TEXT NOT NULL,
            matched_tickers_json TEXT NOT NULL,
            matched_holdings_json TEXT NOT NULL,
            event_tags_json TEXT NOT NULL,
            risk_terms_json TEXT NOT NULL,
            source_credibility REAL NOT NULL,
            body_length INTEGER NOT NULL,
            record_json TEXT NOT NULL
        );

        CREATE VIRTUAL TABLE documents_fts USING fts5(
            doc_id UNINDEXED,
            title,
            body,
            source,
            source_type,
            tickers,
            event_tags,
            risk_terms
        );

        CREATE TABLE document_features (
            doc_id TEXT PRIMARY KEY,
            sentiment_proxy REAL NOT NULL DEFAULT 0,
            risk_intensity REAL NOT NULL DEFAULT 0,
            uncertainty_intensity REAL NOT NULL DEFAULT 0,
            opportunity_intensity REAL NOT NULL DEFAULT 0,
            forward_looking_intensity REAL NOT NULL DEFAULT 0,
            portfolio_action_relevance REAL NOT NULL DEFAULT 0,
            final_score REAL NOT NULL DEFAULT 0,
            event_severity_score REAL NOT NULL DEFAULT 0,
            risk_term_score REAL NOT NULL DEFAULT 0,
            macro_regime_relevance_score REAL NOT NULL DEFAULT 0,
            impact_direction_score REAL NOT NULL DEFAULT 0,
            calibrated_signal_score REAL NOT NULL DEFAULT 0,
            historical_usefulness_score REAL NOT NULL DEFAULT 0,
            upside_signal_score REAL NOT NULL DEFAULT 0,
            risk_alert_score REAL NOT NULL DEFAULT 0,
            feature_rows INTEGER NOT NULL DEFAULT 0,
            active_signals_json TEXT NOT NULL DEFAULT '[]',
            signal_earnings_guidance INTEGER NOT NULL DEFAULT 0,
            signal_company_risk INTEGER NOT NULL DEFAULT 0,
            signal_macro_rates INTEGER NOT NULL DEFAULT 0,
            signal_inflation INTEGER NOT NULL DEFAULT 0,
            signal_credit INTEGER NOT NULL DEFAULT 0,
            signal_labor_growth INTEGER NOT NULL DEFAULT 0,
            signal_market_volatility INTEGER NOT NULL DEFAULT 0,
            signal_energy INTEGER NOT NULL DEFAULT 0,
            signal_housing INTEGER NOT NULL DEFAULT 0,
            signal_legal_regulatory INTEGER NOT NULL DEFAULT 0,
            signal_supply_chain INTEGER NOT NULL DEFAULT 0,
            signal_consumer_demand INTEGER NOT NULL DEFAULT 0,
            signal_margin_pressure INTEGER NOT NULL DEFAULT 0,
            signal_capital_return INTEGER NOT NULL DEFAULT 0,
            signal_mna INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE source_quality (
            source_family TEXT NOT NULL,
            source_type TEXT NOT NULL,
            document_count INTEGER NOT NULL,
            feature_doc_count INTEGER NOT NULL,
            avg_source_credibility REAL NOT NULL,
            avg_signal_score REAL NOT NULL,
            PRIMARY KEY (source_family, source_type)
        );

        CREATE TABLE ticker_coverage (
            ticker TEXT PRIMARY KEY,
            document_count INTEGER NOT NULL,
            feature_doc_count INTEGER NOT NULL,
            avg_signal_score REAL NOT NULL
        );

        CREATE INDEX idx_documents_available_at ON documents(available_at);
        CREATE INDEX idx_documents_source_family ON documents(source_family);
        CREATE INDEX idx_documents_source_type ON documents(source_type);
        CREATE INDEX idx_document_features_signal ON document_features(calibrated_signal_score);
        CREATE INDEX idx_document_features_upside ON document_features(upside_signal_score);
        CREATE INDEX idx_document_features_risk ON document_features(risk_alert_score);
        """
    )


def _insert_manifest(
    connection: sqlite3.Connection,
    *,
    documents_path: Path,
    text_features_path: Path,
    feature_relations_path: Path,
    document_count: int,
    feature_doc_count: int,
) -> None:
    documents_stat = documents_path.stat()
    payload = {
        "index_version": "search_index_v1",
        "created_at_epoch": str(time.time()),
        "documents_path": str(documents_path.resolve()),
        "documents_mtime_ns": str(documents_stat.st_mtime_ns),
        "documents_size": str(documents_stat.st_size),
        "text_features_path": str(text_features_path.resolve()) if text_features_path.exists() else "",
        "feature_relations_path": str(feature_relations_path.resolve()) if feature_relations_path.exists() else "",
        "document_count": str(document_count),
        "feature_doc_count": str(feature_doc_count),
    }
    connection.executemany("INSERT INTO manifest(key, value) VALUES (?, ?)", payload.items())


def _document_rows(documents: Iterable[dict[str, Any]]) -> Iterable[tuple[Any, ...]]:
    for record in documents:
        url = str(record.get("url") or "")
        canonical_url = str(record.get("canonical_url") or url)
        body = str(record.get("body") or record.get("body_excerpt") or "")
        yield (
            str(record.get("doc_id", "") or ""),
            str(record.get("title", "") or ""),
            str(record.get("source", "") or ""),
            str(record.get("source_type", "") or ""),
            _source_family(record),
            str(record.get("source_reliability_tier", "") or ""),
            url,
            canonical_url,
            str(record.get("published_at", "") or ""),
            str(record.get("first_seen_at", "") or ""),
            str(record.get("available_at", "") or ""),
            str(record.get("document_split", "") or ""),
            str(record.get("document_hash", "") or ""),
            str(record.get("duplicate_cluster_id", "") or ""),
            _json_list(record.get("matched_tickers", [])),
            _json_list(record.get("matched_holdings", [])),
            _json_list(record.get("event_tags", [])),
            _json_list(record.get("risk_terms", [])),
            float(record.get("source_credibility", 0.0) or 0.0),
            len(body),
            json.dumps(record, ensure_ascii=False, separators=(",", ":")),
        )


def _fts_rows(documents: Iterable[dict[str, Any]]) -> Iterable[tuple[str, str, str, str, str, str, str, str]]:
    for record in documents:
        yield (
            str(record.get("doc_id", "") or ""),
            str(record.get("title", "") or ""),
            str(record.get("body") or record.get("body_excerpt") or ""),
            str(record.get("source", "") or ""),
            str(record.get("source_type", "") or ""),
            _join_terms(record.get("matched_tickers", [])),
            _join_terms(record.get("event_tags", [])),
            _join_terms(record.get("risk_terms", [])),
        )


def _feature_rows(features: dict[str, dict[str, Any]]) -> Iterable[tuple[Any, ...]]:
    for doc_id, feature in features.items():
        yield (
            doc_id,
            *[float(feature.get(column, 0.0) or 0.0) for column in SIGNAL_FEATURE_COLUMNS],
            float(feature.get("impact_direction_score", 0.0) or 0.0),
            float(feature.get("calibrated_signal_score", 0.0) or 0.0),
            float(feature.get("historical_usefulness_score", 0.0) or 0.0),
            float(feature.get("upside_signal_score", 0.0) or 0.0),
            float(feature.get("risk_alert_score", 0.0) or 0.0),
            int(feature.get("feature_rows", 0) or 0),
            json.dumps(feature.get("active_signals", []) or [], ensure_ascii=False),
            *[int(float(feature.get(column, 0.0) or 0.0) > 0) for column in SIGNAL_FLAG_COLUMNS],
        )


def _build_quality_tables(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        INSERT INTO source_quality(
            source_family,
            source_type,
            document_count,
            feature_doc_count,
            avg_source_credibility,
            avg_signal_score
        )
        SELECT
            d.source_family,
            d.source_type,
            COUNT(*) AS document_count,
            SUM(CASE WHEN f.doc_id IS NULL THEN 0 ELSE 1 END) AS feature_doc_count,
            AVG(d.source_credibility) AS avg_source_credibility,
            AVG(COALESCE(f.calibrated_signal_score, 0.0)) AS avg_signal_score
        FROM documents d
        LEFT JOIN document_features f ON f.doc_id = d.doc_id
        GROUP BY d.source_family, d.source_type;
        """
    )


def _build_ticker_coverage(connection: sqlite3.Connection, documents: Iterable[dict[str, Any]]) -> None:
    rows: dict[str, dict[str, float]] = {}
    features = {
        str(row[0]): float(row[1] or 0.0)
        for row in connection.execute("SELECT doc_id, calibrated_signal_score FROM document_features")
    }
    for record in documents:
        doc_id = str(record.get("doc_id", "") or "")
        score = float(features.get(doc_id, 0.0) or 0.0)
        for ticker in record.get("matched_tickers", []) or []:
            ticker = str(ticker).upper()
            if not ticker:
                continue
            bucket = rows.setdefault(ticker, {"document_count": 0.0, "feature_doc_count": 0.0, "signal_sum": 0.0})
            bucket["document_count"] += 1.0
            if doc_id in features:
                bucket["feature_doc_count"] += 1.0
                bucket["signal_sum"] += score
    connection.executemany(
        """
        INSERT INTO ticker_coverage(ticker, document_count, feature_doc_count, avg_signal_score)
        VALUES (?, ?, ?, ?)
        """,
        [
            (
                ticker,
                int(values["document_count"]),
                int(values["feature_doc_count"]),
                values["signal_sum"] / max(1.0, values["feature_doc_count"]),
            )
            for ticker, values in sorted(rows.items())
        ],
    )


def build_search_index(
    *,
    documents_path: Path,
    output_path: Path,
    text_features_path: Path = TEXT_FEATURES_PATH,
    feature_relations_path: Path = FEATURE_RELATIONS_PATH,
) -> dict[str, Any]:
    documents_path = local_project_path(documents_path)
    output_path = local_project_path(output_path)
    text_features_path = local_project_path(text_features_path)
    feature_relations_path = local_project_path(feature_relations_path)
    documents = read_jsonl(documents_path)

    feature_service = FinPortfolioWebService(
        documents_path=documents_path,
        text_features_path=text_features_path,
        feature_relations_path=feature_relations_path,
    )
    features = feature_service.text_features()

    if output_path.exists():
        output_path.unlink()
    connection = _connect(output_path)
    try:
        _create_schema(connection)
        connection.executemany(
            """
            INSERT INTO documents(
                doc_id, title, source, source_type, source_family, source_reliability_tier,
                url, canonical_url, published_at, first_seen_at, available_at, document_split,
                document_hash, duplicate_cluster_id, matched_tickers_json, matched_holdings_json,
                event_tags_json, risk_terms_json, source_credibility, body_length, record_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            _document_rows(documents),
        )
        connection.executemany(
            """
            INSERT INTO documents_fts(
                doc_id, title, body, source, source_type, tickers, event_tags, risk_terms
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            _fts_rows(documents),
        )
        feature_columns = ", ".join(
            [
                "doc_id",
                *SIGNAL_FEATURE_COLUMNS,
                "impact_direction_score",
                "calibrated_signal_score",
                "historical_usefulness_score",
                "upside_signal_score",
                "risk_alert_score",
                "feature_rows",
                "active_signals_json",
                *SIGNAL_FLAG_COLUMNS,
            ]
        )
        placeholders = ", ".join("?" for _ in feature_columns.split(", "))
        connection.executemany(
            f"INSERT INTO document_features({feature_columns}) VALUES ({placeholders})",
            _feature_rows(features),
        )
        _build_quality_tables(connection)
        _build_ticker_coverage(connection, documents)
        _insert_manifest(
            connection,
            documents_path=documents_path,
            text_features_path=text_features_path,
            feature_relations_path=feature_relations_path,
            document_count=len(documents),
            feature_doc_count=len(features),
        )
        connection.commit()
    finally:
        connection.close()

    return {
        "output_path": str(output_path),
        "document_count": len(documents),
        "feature_doc_count": len(features),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build SQLite FTS search index for FinPortfolio IR.")
    parser.add_argument("--documents", default=str(FULL_DOCUMENTS_PATH), help="Processed documents JSONL.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="SQLite output path.")
    parser.add_argument("--text-features", default=str(TEXT_FEATURES_PATH), help="Doc-level text feature CSV.")
    parser.add_argument("--feature-relations", default=str(FEATURE_RELATIONS_PATH), help="Feature-target relation CSV.")
    args = parser.parse_args(argv)

    summary = build_search_index(
        documents_path=Path(args.documents),
        output_path=Path(args.output),
        text_features_path=Path(args.text_features),
        feature_relations_path=Path(args.feature_relations),
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
