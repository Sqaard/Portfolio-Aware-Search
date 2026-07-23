"""Shared document mapping: JSONL line -> validated, causal-safe document.

Parsing reuses the existing single-machine contract exactly:

* ``FinancialDocument.from_dict`` performs the same field coercion, timestamp
  validation, and hashing as the pure-Python pipeline.
* Records that ``schema.load_documents`` would drop (missing/invalid causal
  timestamps) are dropped here too, so the distributed corpus is identical to
  the one the reference code would build.

``source_family`` is a faithful, dependency-light replica of
``indexing.build_search_index._source_family`` (importing that module would pull
in the whole web app), and is covered by a parity test.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from finportfolio_ir.schema import FinancialDocument  # noqa: E402
from finportfolio_ir.text_utils import tokenize  # noqa: E402


def parse_json_line(line: str) -> Optional[dict]:
    """Parse one JSONL line to a dict, or ``None`` if blank / not valid JSON."""

    if not line:
        return None
    line = line.strip()
    if not line:
        return None
    try:
        record = json.loads(line)
    except (ValueError, TypeError):
        return None
    return record if isinstance(record, dict) else None


def build_document(record: dict) -> Optional[FinancialDocument]:
    """Build a :class:`FinancialDocument`, or ``None`` if it is causally unsafe.

    Mirrors ``finportfolio_ir.schema.load_documents``: a missing or invalid
    ``published_at`` / timestamp makes the record unsafe for point-in-time
    retrieval and is excluded.
    """

    try:
        return FinancialDocument.from_dict(record)
    except (KeyError, ValueError):
        return None


def source_family(record: dict) -> str:
    """Coarse source family (parity with ``build_search_index._source_family``)."""

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


def indexing_tokens(doc: FinancialDocument) -> list:
    """Tokens used for indexing -- identical field composition to ``BM25Index``."""

    return tokenize(doc.text_for_indexing())


def document_year(doc: FinancialDocument) -> str:
    """Calendar year of the point-in-time ``available_at`` timestamp (UTC ISO)."""

    available_at = doc.available_at or ""
    return available_at[:4] if len(available_at) >= 4 and available_at[:4].isdigit() else "unknown"


def as_list(value: Any) -> list:
    """Coerce a possibly-scalar field to a list (defensive for messy records)."""

    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return [value]
