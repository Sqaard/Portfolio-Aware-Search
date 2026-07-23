"""Distributed row-building for the production SQLite FTS search index.

This is the Big Data counterpart of ``indexing/build_search_index.py``: the
corpus-scale, per-document work (parsing every JSONL record of the 352 MB
combined corpus, deriving ``source_family``/lengths, serialising the canonical
``record_json``, and preparing the FTS payloads) is expressed as a MapReduce
``map`` and runs on Spark executors (or local worker processes). The driver
then assembles the SQLite artifact single-writer, exactly like any Spark job
with a non-parallel sink.

Field-by-field parity with ``build_search_index._document_rows`` /
``_fts_rows`` is intentional and is asserted by
``tests/test_bigdata_search_index.py`` (table-level equality of the two
builders' outputs). ``source_family`` reuses :mod:`bigdata.jobs.mapping`,
which has its own parity test against the original.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from .mapping import source_family


def _json_list(value: Any) -> str:
    """Parity replica of ``build_search_index._json_list``."""

    if isinstance(value, list):
        return json.dumps(value, ensure_ascii=False)
    if value in (None, ""):
        return "[]"
    return json.dumps([value], ensure_ascii=False)


def _join_terms(value: Any) -> str:
    """Parity replica of ``build_search_index._join_terms``."""

    if isinstance(value, list):
        return " ".join(str(item) for item in value if item is not None)
    return str(value or "")


def index_rows(line: str) -> Optional[tuple]:
    """``JSONL line -> (document_row, fts_row, doc_id, matched_tickers)``.

    Invalid JSON raises ``ValueError`` (the single-machine ``read_jsonl`` is
    equally strict, so both builders fail loudly on a corrupt corpus).
    """

    stripped = line.strip()
    if not stripped:
        return None
    try:
        record = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSONL line in corpus: {exc}") from exc
    if not isinstance(record, dict):
        return None

    url = str(record.get("url") or "")
    canonical_url = str(record.get("canonical_url") or url)
    body = str(record.get("body") or record.get("body_excerpt") or "")
    doc_id = str(record.get("doc_id", "") or "")

    document_row = (
        doc_id,
        str(record.get("title", "") or ""),
        str(record.get("source", "") or ""),
        str(record.get("source_type", "") or ""),
        source_family(record),
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
    fts_row = (
        doc_id,
        str(record.get("title", "") or ""),
        body,
        str(record.get("source", "") or ""),
        str(record.get("source_type", "") or ""),
        _join_terms(record.get("matched_tickers", [])),
        _join_terms(record.get("event_tags", [])),
        _join_terms(record.get("risk_terms", [])),
    )
    tickers = record.get("matched_tickers", []) or []
    if not isinstance(tickers, list):
        tickers = [tickers]
    return (document_row, fts_row, doc_id, [str(t) for t in tickers])


def not_none(value: Any) -> bool:
    return value is not None
