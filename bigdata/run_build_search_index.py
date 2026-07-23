"""CLI: build the production SQLite FTS search index via the Big Data layer.

The corpus-scale per-document work is a distributed MapReduce ``map`` (Spark
executors or local worker processes); the driver assembles the SQLite file —
schema, tables, manifest — **identically** to ``indexing/build_search_index.py``,
so the resulting index is drop-in for ``web_app.py``. Parity is asserted by
``tests/test_bigdata_search_index.py``.

Examples::

    # local engine, sample corpus, throwaway output
    python -m bigdata.run_build_search_index --corpus sample --engine local \
        --output data/exports/bigdata/search_index/sample.sqlite

    # the real thing on the Spark cluster (from the master container):
    python3 -m bigdata.run_build_search_index --corpus all_ppo --engine spark \
        --master spark://spark-master:7077 --native-read \
        --output data/exports/bigdata/search_index/finportfolio_search_spark.sqlite
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bigdata.cli_common import (  # noqa: E402
    Timer,
    add_common_args,
    build_engine,
    load_dataset,
    run_context,
    write_json,
)
from bigdata.jobs import search_index  # noqa: E402

DEFAULT_OUTPUT = ROOT / "data" / "exports" / "bigdata" / "search_index" / "finportfolio_search.sqlite"

# --- SQL below is a verbatim copy of indexing/build_search_index.py ---------
# (duplicated on purpose so Spark workers/driver never import web_app's heavy
# module tree; drift is guarded by the table-parity test)

_SCHEMA_SQL = """
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

_DOCUMENT_INSERT = """
INSERT INTO documents(
    doc_id, title, source, source_type, source_family, source_reliability_tier,
    url, canonical_url, published_at, first_seen_at, available_at, document_split,
    document_hash, duplicate_cluster_id, matched_tickers_json, matched_holdings_json,
    event_tags_json, risk_terms_json, source_credibility, body_length, record_json
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_FTS_INSERT = """
INSERT INTO documents_fts(
    doc_id, title, body, source, source_type, tickers, event_tags, risk_terms
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
"""

_QUALITY_SQL = """
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


def _feature_rows(features: dict, signal_feature_columns: list, signal_flag_columns: list):
    """Verbatim logic of ``build_search_index._feature_rows``."""

    for doc_id, feature in features.items():
        yield (
            doc_id,
            *[float(feature.get(column, 0.0) or 0.0) for column in signal_feature_columns],
            float(feature.get("impact_direction_score", 0.0) or 0.0),
            float(feature.get("calibrated_signal_score", 0.0) or 0.0),
            float(feature.get("historical_usefulness_score", 0.0) or 0.0),
            float(feature.get("upside_signal_score", 0.0) or 0.0),
            float(feature.get("risk_alert_score", 0.0) or 0.0),
            int(feature.get("feature_rows", 0) or 0),
            json.dumps(feature.get("active_signals", []) or [], ensure_ascii=False),
            *[int(float(feature.get(column, 0.0) or 0.0) > 0) for column in signal_flag_columns],
        )


def _build_ticker_coverage(connection: sqlite3.Connection, doc_tickers: list) -> None:
    """Verbatim logic of ``build_search_index._build_ticker_coverage``.

    ``doc_tickers`` is ``[(doc_id, [tickers...]), ...]`` in corpus file order,
    so per-ticker float accumulation order matches the original exactly.
    """

    rows: dict = {}
    features = {
        str(row[0]): float(row[1] or 0.0)
        for row in connection.execute("SELECT doc_id, calibrated_signal_score FROM document_features")
    }
    for doc_id, tickers in doc_tickers:
        score = float(features.get(doc_id, 0.0) or 0.0)
        for ticker in tickers or []:
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


def build_index_sqlite(
    *,
    rows: list,
    output_path: Path,
    documents_path: Path,
    text_features_path: Path,
    feature_relations_path: Path,
    engine_name: str,
) -> dict:
    """Assemble the SQLite artifact from collected distributed rows (driver-side)."""

    # Lazy import: only the driver needs web_app (for the exact same
    # text-features aggregation the original builder uses).
    from web_app import SIGNAL_FEATURE_COLUMNS, SIGNAL_FLAG_COLUMNS, FinPortfolioWebService

    feature_service = FinPortfolioWebService(
        documents_path=documents_path,
        text_features_path=text_features_path,
        feature_relations_path=feature_relations_path,
    )
    features = feature_service.text_features()

    # Build into a local temp file first: SQLite over container bind mounts /
    # OneDrive can misbehave, and this also makes the final move atomic-ish.
    tmp_dir = Path(tempfile.mkdtemp(prefix="finportfolio_index_"))
    tmp_db = tmp_dir / "index.sqlite"
    connection = sqlite3.connect(tmp_db)
    connection.execute("PRAGMA journal_mode=OFF")
    connection.execute("PRAGMA synchronous=OFF")
    connection.execute("PRAGMA temp_store=MEMORY")
    try:
        connection.executescript(_SCHEMA_SQL)
        connection.executemany(_DOCUMENT_INSERT, (r[0] for r in rows))
        connection.executemany(_FTS_INSERT, (r[1] for r in rows))

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
            _feature_rows(features, SIGNAL_FEATURE_COLUMNS, SIGNAL_FLAG_COLUMNS),
        )
        connection.executescript(_QUALITY_SQL)
        _build_ticker_coverage(connection, [(r[2], r[3]) for r in rows])

        documents_stat = documents_path.stat()
        manifest = {
            "index_version": "search_index_v1",
            "created_at_epoch": str(time.time()),
            "documents_path": str(documents_path.resolve()),
            "documents_mtime_ns": str(documents_stat.st_mtime_ns),
            "documents_size": str(documents_stat.st_size),
            "text_features_path": str(text_features_path.resolve()) if text_features_path.exists() else "",
            "feature_relations_path": str(feature_relations_path.resolve()) if feature_relations_path.exists() else "",
            "document_count": str(len(rows)),
            "feature_doc_count": str(len(features)),
            "built_by": "bigdata.run_build_search_index",
            "builder_engine": engine_name,
        }
        connection.executemany("INSERT INTO manifest(key, value) VALUES (?, ?)", manifest.items())
        connection.commit()
    finally:
        connection.close()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_path.unlink()
    shutil.move(str(tmp_db), str(output_path))
    shutil.rmtree(tmp_dir, ignore_errors=True)
    return {
        "output_path": str(output_path),
        "document_count": len(rows),
        "feature_doc_count": len(features),
    }


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the SQLite FTS search index via the Big Data layer.")
    add_common_args(parser)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="SQLite output path.")
    parser.add_argument("--text-features", default=None, help="Doc-level text feature CSV (default: web_app's).")
    parser.add_argument("--feature-relations", default=None, help="Feature-target relation CSV (default: web_app's).")
    args = parser.parse_args(argv)

    from web_app import FEATURE_RELATIONS_PATH, TEXT_FEATURES_PATH  # lazy: driver only

    text_features_path = Path(args.text_features) if args.text_features else TEXT_FEATURES_PATH
    feature_relations_path = Path(args.feature_relations) if args.feature_relations else FEATURE_RELATIONS_PATH
    output_path = Path(args.output)
    context = run_context(args, "build_search_index")

    engine = build_engine(args)
    context["engine_used"] = engine.name
    print(f"[bigdata] search index build | engine={engine.name} corpus={args.corpus}", flush=True)
    try:
        dataset, corpus_path = load_dataset(engine, args)
        with Timer() as timer:
            rows = dataset.map(search_index.index_rows).filter(search_index.not_none).collect()
        context["map_seconds"] = round(timer.seconds, 3)
        with Timer() as timer:
            summary = build_index_sqlite(
                rows=rows,
                output_path=output_path,
                documents_path=corpus_path,
                text_features_path=text_features_path,
                feature_relations_path=feature_relations_path,
                engine_name=engine.name,
            )
        context["sqlite_seconds"] = round(timer.seconds, 3)
        context["corpus_path"] = str(corpus_path)
    finally:
        engine.stop()

    summary["run"] = context
    write_json(output_path.with_suffix(".manifest.json"), summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
