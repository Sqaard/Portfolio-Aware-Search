"""Data objects for normalized financial documents and retrieval results."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .text_utils import excerpt, stable_content_hash, stable_document_hash
from .time_utils import parse_datetime, to_utc_iso


def _float_or_default(record: dict[str, Any], key: str, default: float) -> float:
    value = record.get(key, default)
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@dataclass
class FinancialDocument:
    doc_id: str
    title: str
    body: str
    source: str
    source_type: str
    url: str
    published_at: str
    first_seen_at: str
    available_at: str
    ingested_at: str
    source_registry_id: str = ""
    canonical_url: str = ""
    source_reliability_tier: str = "unknown"
    robots_policy: str = ""
    last_url_check_at: str = ""
    fetch_status: str = ""
    content_license_note: str = ""
    version_id: str = "v1"
    is_revision: bool = False
    revision_of: str = ""
    duplicate_cluster_id: str = ""
    tickers_detected: list[str] = field(default_factory=list)
    matched_tickers: list[str] = field(default_factory=list)
    matched_holdings: list[str] = field(default_factory=list)
    company_names_detected: list[str] = field(default_factory=list)
    sectors_detected: list[str] = field(default_factory=list)
    sector_tags: list[str] = field(default_factory=list)
    event_tags: list[str] = field(default_factory=list)
    risk_terms: list[str] = field(default_factory=list)
    sentiment_score: float = 0.0
    uncertainty_score: float = 0.0
    source_credibility: float = 0.5
    source_authority_score: float = 0.5
    source_timeliness_score: float = 0.5
    source_legal_liability_score: float = 0.5
    source_numeric_density_score: float = 0.5
    source_promotion_risk_score: float = 0.5
    source_fetch_method: str = ""
    source_update_frequency: str = ""
    source_point_in_time_policy: str = ""
    source_documentation_url: str = ""
    source_coverage_scope: str = ""
    evidence_unit_id: str = ""
    parent_doc_id: str = ""
    evidence_unit_type: str = ""
    evidence_unit_index: int = 0
    evidence_unit_claim_type: str = ""
    event_type: str = ""
    language: str = "en"
    document_hash: str = ""

    @classmethod
    def from_dict(cls, record: dict[str, Any]) -> "FinancialDocument":
        published_at = record.get("published_at", "")
        if not published_at:
            raise ValueError(f"Document {record.get('doc_id', '<missing>')} has no published_at")
        first_seen_at = record.get("first_seen_at") or record.get("available_at") or published_at
        available_at = record.get("available_at") or published_at
        ingested_at = record.get("ingested_at") or available_at
        parse_datetime(str(published_at))
        parse_datetime(str(first_seen_at))
        parse_datetime(str(available_at))
        parse_datetime(str(ingested_at))
        last_url_check_at = str(record.get("last_url_check_at", "") or "")
        if last_url_check_at:
            parse_datetime(last_url_check_at)
        tickers = [str(ticker).upper() for ticker in record.get("tickers_detected", [])]
        matched_tickers = [str(ticker).upper() for ticker in record.get("matched_tickers", tickers)]
        matched_holdings = [
            str(ticker).upper()
            for ticker in record.get("matched_holdings", matched_tickers)
            if str(ticker).upper() != "MARKET"
        ]
        doc = cls(
            doc_id=str(record["doc_id"]),
            title=str(record.get("title", "")),
            body=str(record.get("body", "")),
            source=str(record.get("source", "")),
            source_type=str(record.get("source_type", "sample" if str(record.get("source", "")).startswith("sample") else "unknown")),
            url=str(record.get("url", "")),
            source_registry_id=str(record.get("source_registry_id", record.get("source", "")) or ""),
            canonical_url=str(record.get("canonical_url", record.get("url", "")) or ""),
            source_reliability_tier=str(record.get("source_reliability_tier", "unknown") or "unknown"),
            robots_policy=str(record.get("robots_policy", "")),
            last_url_check_at=to_utc_iso(last_url_check_at) if last_url_check_at else "",
            fetch_status=str(record.get("fetch_status", "")),
            content_license_note=str(record.get("content_license_note", "")),
            published_at=to_utc_iso(str(published_at)),
            first_seen_at=to_utc_iso(str(first_seen_at)),
            available_at=to_utc_iso(str(available_at)),
            ingested_at=to_utc_iso(str(ingested_at)),
            version_id=str(record.get("version_id", "v1")),
            is_revision=bool(record.get("is_revision", False)),
            revision_of=str(record.get("revision_of", "")),
            duplicate_cluster_id=str(record.get("duplicate_cluster_id", "")),
            tickers_detected=tickers,
            matched_tickers=matched_tickers,
            matched_holdings=matched_holdings,
            company_names_detected=[str(name) for name in record.get("company_names_detected", [])],
            sectors_detected=[str(sector) for sector in record.get("sectors_detected", [])],
            sector_tags=[str(sector) for sector in record.get("sector_tags", record.get("sectors_detected", []))],
            event_tags=[str(tag) for tag in record.get("event_tags", [record.get("event_type", "")] if record.get("event_type", "") else [])],
            risk_terms=[str(term) for term in record.get("risk_terms", [])],
            sentiment_score=_float_or_default(record, "sentiment_score", 0.0),
            uncertainty_score=_float_or_default(record, "uncertainty_score", 0.0),
            source_credibility=_float_or_default(record, "source_credibility", 0.5),
            source_authority_score=_float_or_default(record, "source_authority_score", 0.5),
            source_timeliness_score=_float_or_default(record, "source_timeliness_score", 0.5),
            source_legal_liability_score=_float_or_default(record, "source_legal_liability_score", 0.5),
            source_numeric_density_score=_float_or_default(record, "source_numeric_density_score", 0.5),
            source_promotion_risk_score=_float_or_default(record, "source_promotion_risk_score", 0.5),
            source_fetch_method=str(record.get("source_fetch_method", "")),
            source_update_frequency=str(record.get("source_update_frequency", "")),
            source_point_in_time_policy=str(record.get("source_point_in_time_policy", "")),
            source_documentation_url=str(record.get("source_documentation_url", "")),
            source_coverage_scope=str(record.get("source_coverage_scope", "")),
            evidence_unit_id=str(record.get("evidence_unit_id", record.get("doc_id", "")) or ""),
            parent_doc_id=str(record.get("parent_doc_id", "")),
            evidence_unit_type=str(record.get("evidence_unit_type", "")),
            evidence_unit_index=int(record.get("evidence_unit_index", 0) or 0),
            evidence_unit_claim_type=str(record.get("evidence_unit_claim_type", "")),
            event_type=str(record.get("event_type", "")),
            language=str(record.get("language", "en")),
            document_hash=str(record.get("document_hash", "")),
        )
        if not doc.duplicate_cluster_id:
            doc.duplicate_cluster_id = stable_content_hash(doc.to_dict(include_hash=False))
        if not doc.document_hash:
            doc.document_hash = stable_document_hash(doc.to_dict(include_hash=False))
        return doc

    def to_dict(self, include_hash: bool = True) -> dict[str, Any]:
        record = {
            "doc_id": self.doc_id,
            "title": self.title,
            "body": self.body,
            "source": self.source,
            "source_type": self.source_type,
            "url": self.url,
            "source_registry_id": self.source_registry_id,
            "canonical_url": self.canonical_url,
            "source_reliability_tier": self.source_reliability_tier,
            "robots_policy": self.robots_policy,
            "last_url_check_at": self.last_url_check_at,
            "fetch_status": self.fetch_status,
            "content_license_note": self.content_license_note,
            "published_at": self.published_at,
            "first_seen_at": self.first_seen_at,
            "available_at": self.available_at,
            "ingested_at": self.ingested_at,
            "version_id": self.version_id,
            "is_revision": self.is_revision,
            "revision_of": self.revision_of,
            "duplicate_cluster_id": self.duplicate_cluster_id,
            "tickers_detected": self.tickers_detected,
            "matched_tickers": self.matched_tickers,
            "matched_holdings": self.matched_holdings,
            "company_names_detected": self.company_names_detected,
            "sectors_detected": self.sectors_detected,
            "sector_tags": self.sector_tags,
            "event_tags": self.event_tags,
            "risk_terms": self.risk_terms,
            "sentiment_score": self.sentiment_score,
            "uncertainty_score": self.uncertainty_score,
            "source_credibility": self.source_credibility,
            "source_authority_score": self.source_authority_score,
            "source_timeliness_score": self.source_timeliness_score,
            "source_legal_liability_score": self.source_legal_liability_score,
            "source_numeric_density_score": self.source_numeric_density_score,
            "source_promotion_risk_score": self.source_promotion_risk_score,
            "source_fetch_method": self.source_fetch_method,
            "source_update_frequency": self.source_update_frequency,
            "source_point_in_time_policy": self.source_point_in_time_policy,
            "source_documentation_url": self.source_documentation_url,
            "source_coverage_scope": self.source_coverage_scope,
            "evidence_unit_id": self.evidence_unit_id,
            "parent_doc_id": self.parent_doc_id,
            "evidence_unit_type": self.evidence_unit_type,
            "evidence_unit_index": self.evidence_unit_index,
            "evidence_unit_claim_type": self.evidence_unit_claim_type,
            "event_type": self.event_type,
            "language": self.language,
        }
        if include_hash:
            record["document_hash"] = self.document_hash
        return record

    def text_for_indexing(self) -> str:
        entity_text = " ".join(self.tickers_detected + self.company_names_detected + self.sectors_detected)
        return f"{self.title} {self.body} {entity_text} {self.event_type}"

    def body_excerpt(self, max_chars: int = 1200) -> str:
        return excerpt(self.body, max_chars)


def load_documents(records: list[dict[str, Any]]) -> list[FinancialDocument]:
    documents: list[FinancialDocument] = []
    for record in records:
        try:
            documents.append(FinancialDocument.from_dict(record))
        except (KeyError, ValueError):
            # Missing or invalid timestamps are unsafe for causal retrieval.
            continue
    return documents
