"""Minimal RSS collector for future extension.

The project does not rely on live crawling for the v1 demo. This script can
collect basic RSS item fields when a user provides feed URLs, but sample data
is the default reproducible path.
"""

from __future__ import annotations

import argparse
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from finportfolio_ir.io_utils import write_jsonl
from finportfolio_ir.text_utils import stable_document_hash
from crawler.source_registry import canonicalize_url


def _item_text(item: ET.Element, name: str) -> str:
    found = item.find(name)
    return found.text.strip() if found is not None and found.text else ""


def _parse_pubdate(value: str) -> str:
    if not value:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    parsed = parsedate_to_datetime(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def collect_feed(url: str, source: str) -> list[dict[str, object]]:
    with urllib.request.urlopen(url, timeout=20) as response:
        payload = response.read()
    root = ET.fromstring(payload)
    records: list[dict[str, object]] = []
    for index, item in enumerate(root.findall(".//item"), start=1):
        title = _item_text(item, "title")
        body = _item_text(item, "description")
        link = _item_text(item, "link")
        published_at = _parse_pubdate(_item_text(item, "pubDate"))
        record = {
            "doc_id": f"{source}_{index:06d}",
            "title": title,
            "body": body,
            "source": source,
            "source_type": "rss",
            "url": link,
            "canonical_url": canonicalize_url(link),
            "published_at": published_at,
            "first_seen_at": published_at,
            "available_at": published_at,
            "ingested_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "fetch_status": "ok",
            "language": "en",
        }
        record["document_hash"] = stable_document_hash(record)
        records.append(record)
    return records


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Collect raw documents from RSS feeds.")
    parser.add_argument("--feed", action="append", required=True, help="RSS feed URL. Can be repeated.")
    parser.add_argument("--source", default="rss")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)

    records: list[dict[str, object]] = []
    for feed in args.feed:
        records.extend(collect_feed(feed, args.source))
    write_jsonl(args.output, records)
    print(f"Wrote {len(records)} RSS records to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
