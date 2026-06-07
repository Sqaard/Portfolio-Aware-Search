"""Text normalization and scoring helpers."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from typing import Any


TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.$'-]*|\d+(?:\.\d+)?")


def tokenize(text: str) -> list[str]:
    return [token.lower().strip(".$'") for token in TOKEN_RE.findall(text or "") if token.strip(".$'")]


def normalize_scores(scores: Mapping[str, float]) -> dict[str, float]:
    if not scores:
        return {}
    values = list(scores.values())
    min_value = min(values)
    max_value = max(values)
    if max_value <= min_value:
        return {key: 1.0 if value > 0 else 0.0 for key, value in scores.items()}
    return {key: (value - min_value) / (max_value - min_value) for key, value in scores.items()}


def stable_document_hash(record: Mapping[str, Any]) -> str:
    parts = [
        str(record.get("source", "")),
        str(record.get("doc_id", "")),
        str(record.get("published_at", "")),
        str(record.get("available_at", "")),
        str(record.get("title", "")),
        str(record.get("body", record.get("body_excerpt", ""))),
    ]
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def stable_content_hash(record: Mapping[str, Any]) -> str:
    """Hash normalized content for duplicate clustering."""

    parts = [
        str(record.get("title", "")).lower(),
        " ".join(str(record.get("body", record.get("body_excerpt", ""))).lower().split()),
    ]
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def excerpt(text: str, max_chars: int) -> str:
    cleaned = " ".join((text or "").split())
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[: max(0, max_chars - 3)].rstrip() + "..."
