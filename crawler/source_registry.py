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
REQUIRED_REGISTRY_COLUMNS = {
    "source_registry_id",
    "name",
    "base_url",
    "source_type",
    "source_reliability_tier",
    "robots_policy",
    "content_license_note",
    "source_credibility",
    "preferred_for_v1",
    "notes",
}
RICH_SCORE_COLUMNS = {
    "authority_score",
    "timeliness_score",
    "legal_liability_score",
    "numeric_density_score",
    "promotion_risk_score",
}


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
    authority_score: float = 0.5
    timeliness_score: float = 0.5
    legal_liability_score: float = 0.5
    numeric_density_score: float = 0.5
    promotion_risk_score: float = 0.5
    fetch_method: str = ""
    update_frequency: str = ""
    point_in_time_policy: str = ""
    preferred_endpoints: str = ""
    documentation_url: str = ""
    coverage_scope: str = ""

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


def _float_or_default(row: dict[str, Any], key: str, default: float = 0.5) -> float:
    value = str(row.get(key, "") or "").strip()
    if not value:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _boolish(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


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
                source_credibility=_float_or_default(row, "source_credibility"),
                preferred_for_v1=_boolish(row.get("preferred_for_v1")),
                notes=str(row.get("notes", "")).strip(),
                authority_score=_float_or_default(row, "authority_score"),
                timeliness_score=_float_or_default(row, "timeliness_score"),
                legal_liability_score=_float_or_default(row, "legal_liability_score"),
                numeric_density_score=_float_or_default(row, "numeric_density_score"),
                promotion_risk_score=_float_or_default(row, "promotion_risk_score"),
                fetch_method=str(row.get("fetch_method", "")).strip(),
                update_frequency=str(row.get("update_frequency", "")).strip(),
                point_in_time_policy=str(row.get("point_in_time_policy", "")).strip(),
                preferred_endpoints=str(row.get("preferred_endpoints", "")).strip(),
                documentation_url=str(row.get("documentation_url", "")).strip(),
                coverage_scope=str(row.get("coverage_scope", "")).strip(),
            )
    return registry


def validate_source_registry(path: Union[str, Path]) -> list[str]:
    resolved = local_project_path(path)
    errors: list[str] = []
    with resolved.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = set(reader.fieldnames or [])
        missing = sorted(REQUIRED_REGISTRY_COLUMNS - fieldnames)
        if missing:
            errors.append(f"missing required columns: {', '.join(missing)}")
        seen_ids: set[str] = set()
        for line_number, row in enumerate(reader, start=2):
            source_id = str(row.get("source_registry_id", "") or "").strip()
            if not source_id:
                errors.append(f"line {line_number}: empty source_registry_id")
                continue
            if source_id in seen_ids:
                errors.append(f"line {line_number}: duplicate source_registry_id {source_id}")
            seen_ids.add(source_id)

            base_url = canonicalize_url(str(row.get("base_url", "") or ""))
            parsed = urllib.parse.urlparse(base_url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                errors.append(f"line {line_number}: invalid base_url for {source_id}: {row.get('base_url', '')}")

            preferred = str(row.get("preferred_for_v1", "") or "").strip().lower()
            if preferred and preferred not in {"1", "true", "yes", "y", "0", "false", "no", "n"}:
                errors.append(f"line {line_number}: preferred_for_v1 must be boolean-like for {source_id}")

            for score_column in {"source_credibility", *RICH_SCORE_COLUMNS}:
                raw_value = str(row.get(score_column, "") or "").strip()
                if not raw_value:
                    continue
                try:
                    score = float(raw_value)
                except ValueError:
                    errors.append(f"line {line_number}: {score_column} is not numeric for {source_id}")
                    continue
                if not 0.0 <= score <= 1.0:
                    errors.append(f"line {line_number}: {score_column} must be in [0, 1] for {source_id}")
    return errors


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
    enriched.setdefault("source_authority_score", entry.authority_score)
    enriched.setdefault("source_timeliness_score", entry.timeliness_score)
    enriched.setdefault("source_legal_liability_score", entry.legal_liability_score)
    enriched.setdefault("source_numeric_density_score", entry.numeric_density_score)
    enriched.setdefault("source_promotion_risk_score", entry.promotion_risk_score)
    enriched.setdefault("source_fetch_method", entry.fetch_method)
    enriched.setdefault("source_update_frequency", entry.update_frequency)
    enriched.setdefault("source_point_in_time_policy", entry.point_in_time_policy)
    enriched.setdefault("source_documentation_url", entry.documentation_url)
    enriched.setdefault("source_coverage_scope", entry.coverage_scope)
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
    parser.add_argument("--registry", required=True)
    parser.add_argument("--input")
    parser.add_argument("--output")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args(argv)

    if args.validate_only:
        errors = validate_source_registry(args.registry)
        if errors:
            for error in errors:
                print(error)
            return 1
        print(f"Registry OK: {args.registry}")
        return 0

    if not args.input or not args.output:
        parser.error("--input and --output are required unless --validate-only is used")

    registry = load_source_registry(args.registry)
    records = enrich_records_source_metadata(read_jsonl(args.input), registry)
    write_jsonl(args.output, records)
    print(f"Wrote {len(records)} source-enriched documents to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
