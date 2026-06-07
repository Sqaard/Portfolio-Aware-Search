"""Normalize raw financial document JSONL into the project schema."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional, Union

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from finportfolio_ir.io_utils import read_jsonl, write_jsonl
from finportfolio_ir.schema import FinancialDocument
from crawler.source_registry import enrich_record_source_metadata, load_source_registry
from indexing.entity_linking import enrich_document_entities, load_ticker_metadata


def normalize_records(
    records: list[dict[str, object]],
    metadata_path: Union[str, Path],
    source_registry_path: Union[str, Path, None] = None,
) -> list[dict[str, object]]:
    metadata = load_ticker_metadata(metadata_path)
    source_registry = load_source_registry(source_registry_path) if source_registry_path else {}
    normalized: list[dict[str, object]] = []
    seen_hashes: set[str] = set()

    for record in records:
        enriched = enrich_document_entities(record, metadata)
        if source_registry:
            enriched = enrich_record_source_metadata(enriched, source_registry)
        try:
            document = FinancialDocument.from_dict(enriched)
        except (KeyError, ValueError):
            continue
        if document.document_hash in seen_hashes:
            continue
        seen_hashes.add(document.document_hash)
        normalized.append(document.to_dict())
    return normalized


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Normalize raw JSONL documents.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--metadata", required=True)
    parser.add_argument("--source-registry", default="", help="Optional source registry CSV.")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)

    records = normalize_records(read_jsonl(args.input), args.metadata, args.source_registry or None)
    write_jsonl(args.output, records)
    print(f"Wrote {len(records)} normalized documents to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
