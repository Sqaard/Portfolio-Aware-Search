"""Merge historical and live FinPortfolio IR documents.

Historical backfills remain immutable.  Fresh official documents are appended to
``data/live_ir/live_processed_documents.jsonl`` by the live fetcher, then this
script creates a deduplicated merged JSONL that can be indexed by
``indexing/build_search_index.py``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from finportfolio_ir.io_utils import read_jsonl, write_jsonl  # noqa: E402


DEFAULT_HISTORICAL = ROOT / "data" / "processed_documents" / "sec_macro_company_ir_ppo_2010_2023_documents.jsonl"
DEFAULT_LIVE = ROOT / "data" / "live_ir" / "live_processed_documents.jsonl"
DEFAULT_OUTPUT = ROOT / "data" / "live_ir" / "merged_documents.jsonl"


def document_sort_key(record: dict[str, Any]) -> tuple[str, str]:
    return str(record.get("available_at", "") or ""), str(record.get("doc_id", "") or "")


def merge_documents(paths: Iterable[Path]) -> list[dict[str, Any]]:
    by_doc_id: dict[str, dict[str, Any]] = {}
    hash_owner: dict[str, str] = {}
    anonymous: list[dict[str, Any]] = []

    for path in paths:
        if not path.exists():
            continue
        for record in read_jsonl(path):
            doc_id = str(record.get("doc_id", "") or "")
            document_hash = str(record.get("document_hash", "") or "")
            if document_hash and document_hash in hash_owner and hash_owner[document_hash] != doc_id:
                continue
            if not doc_id:
                anonymous.append(record)
                continue
            by_doc_id[doc_id] = record
            if document_hash:
                hash_owner[document_hash] = doc_id

    merged = [*by_doc_id.values(), *anonymous]
    merged.sort(key=document_sort_key)
    return merged


def build_live_documents(*, historical_path: Path, live_path: Path, output_path: Path) -> dict[str, Any]:
    historical_count = len(read_jsonl(historical_path)) if historical_path.exists() else 0
    live_count = len(read_jsonl(live_path)) if live_path.exists() else 0
    merged = merge_documents([historical_path, live_path])
    write_jsonl(output_path, merged)
    return {
        "status": "completed",
        "historical_path": str(historical_path),
        "live_path": str(live_path),
        "output_path": str(output_path),
        "historical_count": historical_count,
        "live_count": live_count,
        "merged_count": len(merged),
        "deduplicated_count": historical_count + live_count - len(merged),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Merge historical and live documents for live search indexing.")
    parser.add_argument("--historical", default=str(DEFAULT_HISTORICAL))
    parser.add_argument("--live", default=str(DEFAULT_LIVE))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args(argv)
    summary = build_live_documents(
        historical_path=Path(args.historical),
        live_path=Path(args.live),
        output_path=Path(args.output),
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
