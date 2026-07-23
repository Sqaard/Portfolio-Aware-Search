"""Live IR freshness/status helpers.

The live pipeline is intentionally observable: fetch and indexing can succeed
without LLM enrichment, while the pending LLM queue remains visible.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .io_utils import read_jsonl


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def read_json_file(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def latest_by_source(records: list[dict[str, Any]]) -> dict[str, str]:
    latest: dict[str, str] = {}
    for record in records:
        source_type = str(record.get("source_type", "") or "unknown")
        available_at = str(record.get("available_at", "") or "")
        if not available_at:
            continue
        latest[source_type] = max(latest.get(source_type, ""), available_at)
    return dict(sorted(latest.items()))


def latest_by_ticker(records: list[dict[str, Any]], limit: int = 40) -> dict[str, str]:
    latest: dict[str, str] = {}
    for record in records:
        available_at = str(record.get("available_at", "") or "")
        if not available_at:
            continue
        for ticker in record.get("matched_tickers", []) or []:
            key = str(ticker).upper()
            if not key:
                continue
            latest[key] = max(latest.get(key, ""), available_at)
    return dict(sorted(latest.items())[:limit])


def queue_status(queue_rows: list[dict[str, Any]]) -> dict[str, Any]:
    statuses = Counter(str(row.get("status", "pending") or "pending") for row in queue_rows)
    priority_by_status: dict[str, int] = defaultdict(int)
    for row in queue_rows:
        status = str(row.get("status", "pending") or "pending")
        priority_by_status[status] = max(priority_by_status[status], int(row.get("priority", 0) or 0))
    return {
        "count": len(queue_rows),
        "status_counts": dict(statuses),
        "max_priority_by_status": dict(priority_by_status),
        "pending_top": [
            {
                "doc_id": row.get("doc_id", ""),
                "priority": int(row.get("priority", 0) or 0),
                "source_type": row.get("source_type", ""),
                "available_at": row.get("available_at", ""),
                "title": row.get("title", ""),
            }
            for row in sorted(
                [row for row in queue_rows if str(row.get("status", "pending") or "pending") == "pending"],
                key=lambda item: (-int(item.get("priority", 0) or 0), str(item.get("available_at", ""))),
            )[:10]
        ],
    }


def build_live_ir_status(
    *,
    live_dir: Path,
    processed_path: Path | None = None,
    merged_path: Path | None = None,
    queue_path: Path | None = None,
    manifest_path: Path | None = None,
    index_path: Path | None = None,
) -> dict[str, Any]:
    live_dir = Path(live_dir)
    processed_path = processed_path or live_dir / "live_processed_documents.jsonl"
    merged_path = merged_path or live_dir / "merged_documents.jsonl"
    queue_path = queue_path or live_dir / "live_llm_queue.jsonl"
    manifest_path = manifest_path or live_dir / "live_refresh_manifest.json"
    index_path = index_path or live_dir / "finportfolio_search_live.sqlite"

    processed = read_jsonl(processed_path) if processed_path.exists() else []
    merged = read_jsonl(merged_path) if merged_path.exists() else []
    queue = read_jsonl(queue_path) if queue_path.exists() else []
    manifest = read_json_file(manifest_path)
    latest_live = latest_by_source(processed)
    newest_live = max(latest_live.values()) if latest_live else ""

    return {
        "live_ir_enabled": bool(processed_path.exists() or merged_path.exists() or manifest_path.exists()),
        "checked_at": utc_now(),
        "live_dir": str(live_dir),
        "manifest_path": str(manifest_path),
        "manifest": manifest,
        "processed": {
            "path": str(processed_path),
            "exists": processed_path.exists(),
            "document_count": len(processed),
            "latest_available_at": newest_live,
            "latest_by_source_type": latest_live,
            "latest_by_ticker": latest_by_ticker(processed),
        },
        "merged": {
            "path": str(merged_path),
            "exists": merged_path.exists(),
            "document_count": len(merged),
            "latest_available_at": max((str(row.get("available_at", "")) for row in merged), default=""),
        },
        "llm_queue": {
            "path": str(queue_path),
            "exists": queue_path.exists(),
            **queue_status(queue),
        },
        "index": {
            "path": str(index_path),
            "exists": index_path.exists(),
            "last_write_time": datetime.fromtimestamp(index_path.stat().st_mtime, tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
            if index_path.exists()
            else "",
        },
    }
