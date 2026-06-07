"""Source registry helpers for reliable financial corpus ingestion.

The registry is intentionally offline-first. It records source quality,
compliance notes, and URL health metadata without making live crawling a
requirement for reproducible tests.
"""

from __future__ import annotations

import argparse
import csv
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, Union

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from finportfolio_ir.io_utils import local_project_path, read_jsonl, write_jsonl


TRACKING_QUERY_PREFIXES = ("utm_",)
TRACKING_QUERY_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid"}


@dataclass(frozen=True)
class SourceRegistryEntry:
    source_registry_id: str
    name: str
    base_url: str
    source_type: str
    source_reliability_tier: str
    robots_policy: str
    content_license_note: str
    source_credibility: float = 0.5
    preferred_for_v1: bool = False
    notes: str = ""

    @property
    def hostname(self) -> str:
        return urllib.parse.urlparse(self.base_url).netloc.lower().removeprefix("www.")


def canonicalize_url(url: str) -> str:
    text = str(url or "").strip()
    if not text:
        return ""
    parsed = urllib.parse.urlparse(text if "://" in text else f"https://{text}")
    scheme = (parsed.scheme or "https").lower()
    netloc = parsed.netloc.lower()
    if (scheme, netloc.endswith(":443")) == ("https", True):
        netloc = netloc[:-4]
    if (scheme, netloc.endswith(":80")) == ("http", True):
        netloc = netloc[:-3]
    path = urllib.parse.quote(urllib.parse.unquote(parsed.path or "/"), safe="/%:@")
    query_items = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    kept_query = [
        (key, value)
        for key, value in query_items
        if key not in TRACKING_QUERY_KEYS and not key.lower().startswith(TRACKING_QUERY_PREFIXES)
    ]
    query = urllib.parse.urlencode(sorted(kept_query), doseq=True)
    return urllib.parse.urlunparse((scheme, netloc, path, "", query, ""))


def _host(value: str) -> str:
    return urllib.parse.urlparse(canonicalize_url(value)).netloc.lower().removeprefix("www.")


def load_source_registry(path: Union[str, Path]) -> dict[str, SourceRegistryEntry]:
    registry: dict[str, SourceRegistryEntry] = {}
    with local_project_path(path).open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            source_id = str(row.get("source_registry_id", "")).strip()
            if not source_id:
                continue
            registry[source_id] = SourceRegistryEntry(
                source_registry_id=source_id,
                name=str(row.get("name", "")).strip() or source_id,
                base_url=canonicalize_url(str(row.get("base_url", "")).strip()),
                source_type=str(row.get("source_type", "")).strip() or "unknown",
                source_reliability_tier=str(row.get("source_reliability_tier", "")).strip() or "unknown",
                robots_policy=str(row.get("robots_policy", "")).strip(),
                content_license_note=str(row.get("content_license_note", "")).strip(),
                source_credibility=float(row.get("source_credibility", 0.5) or 0.5),
                preferred_for_v1=str(row.get("preferred_for_v1", "")).strip().lower() in {"1", "true", "yes", "y"},
                notes=str(row.get("notes", "")).strip(),
            )
    return registry


def match_source_entry(
    record: dict[str, Any],
    registry: dict[str, SourceRegistryEntry],
) -> SourceRegistryEntry | None:
    explicit_id = str(record.get("source_registry_id", "") or "").strip()
    if explicit_id in registry:
        return registry[explicit_id]

    source = str(record.get("source", "") or "").strip()
    if source in registry:
        return registry[source]

    host = _host(str(record.get("url", "") or ""))
    if not host:
        return None
    for entry in registry.values():
        entry_host = entry.hostname
        if host == entry_host or host.endswith(f".{entry_host}"):
            return entry
    return None


def enrich_record_source_metadata(
    record: dict[str, Any],
    registry: dict[str, SourceRegistryEntry],
) -> dict[str, Any]:
    enriched = dict(record)
    canonical_url = canonicalize_url(str(enriched.get("url", "") or ""))
    if canonical_url:
        enriched["canonical_url"] = canonical_url
    entry = match_source_entry(enriched, registry)
    if entry is None:
        enriched.setdefault("source_reliability_tier", "unknown")
        return enriched

    enriched["source_registry_id"] = entry.source_registry_id
    enriched["source_type"] = enriched.get("source_type") or entry.source_type
    enriched["source_reliability_tier"] = entry.source_reliability_tier
    enriched["robots_policy"] = entry.robots_policy
    enriched["content_license_note"] = entry.content_license_note
    if not enriched.get("source_credibility"):
        enriched["source_credibility"] = entry.source_credibility
    return enriched


def enrich_records_source_metadata(
    records: Iterable[dict[str, Any]],
    registry: dict[str, SourceRegistryEntry],
) -> list[dict[str, Any]]:
    return [enrich_record_source_metadata(record, registry) for record in records]


def build_url_health_record(
    url: str,
    *,
    status_code: int | None = None,
    error: str = "",
    checked_at: str | None = None,
) -> dict[str, Any]:
    checked = checked_at or datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    status = "ok" if status_code is not None and 200 <= status_code < 400 else "failed"
    return {
        "url": str(url or ""),
        "canonical_url": canonicalize_url(url),
        "last_url_check_at": checked,
        "fetch_status": status,
        "http_status": status_code,
        "error": error,
    }


def fetch_url_health(url: str, timeout: int = 15) -> dict[str, Any]:
    request = urllib.request.Request(
        canonicalize_url(url),
        method="HEAD",
        headers={"User-Agent": "FinPortfolioIR/0.1 research crawler contact=local"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return build_url_health_record(url, status_code=int(response.status))
    except urllib.error.HTTPError as exc:
        return build_url_health_record(url, status_code=int(exc.code), error=str(exc.reason))
    except (urllib.error.URLError, socket.timeout, ValueError) as exc:
        return build_url_health_record(url, error=str(exc))


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Apply source registry metadata to JSONL documents.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--registry", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)

    registry = load_source_registry(args.registry)
    records = enrich_records_source_metadata(read_jsonl(args.input), registry)
    write_jsonl(args.output, records)
    print(f"Wrote {len(records)} source-enriched documents to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
