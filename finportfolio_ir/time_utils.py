"""Timezone-aware timestamp helpers for causal retrieval."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Union
from zoneinfo import ZoneInfo


def parse_datetime(value: Union[str, datetime]) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if not text:
            raise ValueError("Timestamp is empty")
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        raise ValueError(f"Timestamp must be timezone-aware: {value}")
    return dt


def parse_decision_datetime(value: str, default_timezone: str = "America/New_York") -> datetime:
    text = str(value).strip()
    if text.endswith("Z") or "+" in text[10:] or "-" in text[10:]:
        return parse_datetime(text)
    return datetime.fromisoformat(text).replace(tzinfo=ZoneInfo(default_timezone))


def to_utc_iso(value: Union[str, datetime]) -> str:
    return parse_datetime(value).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def is_causally_available(
    document_time: Union[str, datetime],
    decision_time: Union[str, datetime],
) -> bool:
    return parse_datetime(document_time) <= parse_datetime(decision_time)
