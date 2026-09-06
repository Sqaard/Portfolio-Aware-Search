"""Distributed corpus analytics -- the 'processing & analysis' deliverable.

A single MapReduce pass turns the whole corpus into the aggregates that make the
collected data useful: how evidence is distributed across source families, SEC
vs macro vs company-IR, reliability tiers, calendar years, tickers, event types,
and risk themes, plus token-length and source-credibility summaries and the
point-in-time coverage window.

    map:     JSONL line -> compact metadata dict            [parse + tokenise]
    flatMap: metadata    -> many (metric_key, value) pairs   [emit counters]
    reduce:  (key, v)*    -> (key, total)                     [sum]

Everything is additive so it collapses in one ``reduceByKey`` (min/max of the
coverage window are two extra reduce actions on the cached base).
"""

from __future__ import annotations

from typing import Any, Iterator, Optional

from ..engine.base import Dataset
from .mapping import (
    build_document,
    document_year,
    indexing_tokens,
    parse_json_line,
    source_family,
)

_SEP = "\t"


def analytics_doc(line: str) -> Optional[dict]:
    """``JSONL line -> compact metadata dict`` (or ``None`` if dropped)."""

    record = parse_json_line(line)
    if record is None:
        return None
    document = build_document(record)
    if document is None:
        return None
    tickers = [str(t).upper() for t in (document.matched_tickers or []) if str(t).strip()]
    events = [str(e) for e in (document.event_tags or []) if str(e).strip()]
    risks = [str(r) for r in (document.risk_terms or []) if str(r).strip()]
    return {
        "family": source_family(record),
        "source_type": document.source_type or "unknown",
        "tier": document.source_reliability_tier or "unknown",
        "year": document_year(document),
        "lang": document.language or "unknown",
        "tickers": tickers,
        "events": events,
        "risks": risks,
        "length": len(indexing_tokens(document)),
        "credibility": float(document.source_credibility or 0.0),
        "available_at": document.available_at or "",
    }


def not_none(value: Any) -> bool:
    return value is not None


def emit_metrics(meta: dict) -> Iterator[tuple]:
    """Fan one document's metadata into many additive ``(key, value)`` metrics."""

    yield ("docs" + _SEP + "total", 1)
    yield ("family" + _SEP + meta["family"], 1)
    yield ("type" + _SEP + meta["source_type"], 1)
    yield ("tier" + _SEP + meta["tier"], 1)
    yield ("year" + _SEP + meta["year"], 1)
    yield ("lang" + _SEP + meta["lang"], 1)
    # dict.fromkeys de-duplicates while preserving order: a tag repeated inside
    # one document must contribute ONE document to that tag's count, exactly as
    # document frequency does in the inverted index. 37% of the macro corpus
    # carries a repeated tag (e.g. event_tags = [..., "energy", "energy", ...]),
    # so without this the report counts documents twice.
    for ticker in dict.fromkeys(meta["tickers"]):
        yield ("ticker" + _SEP + ticker, 1)
    for event in dict.fromkeys(meta["events"]):
        yield ("event" + _SEP + event, 1)
    for risk in dict.fromkeys(meta["risks"]):
        yield ("risk" + _SEP + risk, 1)
    yield ("tokens" + _SEP + "total", meta["length"])
    yield ("doclen" + _SEP + "count", 1)
    # Credibility sum per family (paired with family counts to form an average).
    yield ("credsum" + _SEP + meta["family"], meta["credibility"])


def add_numbers(a, b):
    return a + b


def get_available_at(meta: dict) -> str:
    return meta["available_at"]


def _nonempty_min(a: str, b: str) -> str:
    if not a:
        return b
    if not b:
        return a
    return a if a <= b else b


def _nonempty_max(a: str, b: str) -> str:
    if not a:
        return b
    if not b:
        return a
    return a if a >= b else b


def raw_metrics(dataset: Dataset) -> tuple:
    """Return the pre-finalized ``(metrics, min_available, max_available, total)``.

    ``metrics`` is the flat, additive ``"dimension\\tvalue" -> total`` map straight
    out of the shuffle. Keeping it un-shaped is what lets the streaming updater
    merge micro-batches losslessly (see :func:`merge_metrics`).
    """

    base = dataset.map(analytics_doc).filter(not_none).cache()
    total_docs = base.count()
    if total_docs == 0:
        return {}, "", "", 0
    metrics = base.flat_map(emit_metrics).reduce_by_key(add_numbers).collect_as_map()
    dates = base.map(get_available_at).filter(bool)
    try:
        min_available = dates.reduce(_nonempty_min)
        max_available = dates.reduce(_nonempty_max)
    except ValueError:  # no dated documents
        min_available = max_available = ""
    return metrics, min_available, max_available, total_docs


def merge_metrics(left: dict, right: dict) -> dict:
    """Additively merge two flat metric maps (for incremental streaming state)."""

    out = dict(left)
    for key, value in right.items():
        out[key] = out.get(key, 0) + value
    return out


def finalize_report(metrics: dict, min_available: str, max_available: str, *, top_n: int = 25) -> dict:
    """Shape a flat metric map into the structured analytics report."""

    if not metrics:
        return {"total_documents": 0}

    buckets = _bucketize(metrics)
    family_counts = buckets.get("family", {})
    cred_sums = buckets.get("credsum", {})
    doclen_count = buckets.get("doclen", {}).get("count", 0) or 1
    tokens_total = buckets.get("tokens", {}).get("total", 0)

    avg_cred_by_family = {
        family: round(cred_sums.get(family, 0.0) / count, 4)
        for family, count in family_counts.items()
        if count
    }

    return {
        "total_documents": buckets.get("docs", {}).get("total", 0),
        "vocabulary_note": "term-level stats are produced by the inverted-index job",
        "by_source_family": _sorted_dict(family_counts),
        "by_source_type": _sorted_dict(buckets.get("type", {})),
        "by_reliability_tier": _sorted_dict(buckets.get("tier", {})),
        "by_year": dict(sorted(buckets.get("year", {}).items())),
        "by_language": _sorted_dict(buckets.get("lang", {})),
        "top_tickers": _top(buckets.get("ticker", {}), top_n),
        "top_event_tags": _top(buckets.get("event", {}), top_n),
        "top_risk_terms": _top(buckets.get("risk", {}), top_n),
        "total_tokens": tokens_total,
        "avg_document_length_tokens": round(tokens_total / doclen_count, 2),
        "avg_source_credibility_by_family": avg_cred_by_family,
        "point_in_time": {
            "min_available_at": min_available,
            "max_available_at": max_available,
        },
    }


def compute_analytics(dataset: Dataset, *, top_n: int = 25) -> dict:
    """Compute the full corpus-analytics report from a dataset of JSONL lines."""

    metrics, min_available, max_available, _ = raw_metrics(dataset)
    return finalize_report(metrics, min_available, max_available, top_n=top_n)


# -- report shaping helpers ------------------------------------------------
def _bucketize(metrics: dict) -> dict:
    """Split flat ``"dimension\\tvalue" -> total`` metrics into nested dicts."""

    out: dict = {}
    for key, value in metrics.items():
        dimension, _, name = key.partition(_SEP)
        out.setdefault(dimension, {})[name] = value
    return out


def _sorted_dict(counts: dict) -> dict:
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def _top(counts: dict, n: int) -> list:
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:n]
    return [{"name": name, "count": count} for name, count in ranked]
