"""Guardrails for enabling evidence-unit retrieval in live search.

Evidence units are useful when the query asks for a specific filing section,
macro observation, legal item, or earnings/guidance evidence. They are less
safe as a default for broad company overview queries, where splitting pages can
over-amplify small snippets.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from finportfolio_ir.query_intent import QueryIntent, classify_query_intent
from retrieval.evidence_unit_promotion import EvidenceUnitPromotionStatus, load_evidence_unit_promotion_status


HIGH_VALUE_FIELDS = {
    "risk_factors",
    "earnings",
    "revenue",
    "capital_return",
    "credit",
    "rates",
    "inflation",
    "labor",
    "housing",
    "energy",
    "legal_regulatory",
}

SEC_UNIT_FIELDS = {"risk_factors", "earnings", "revenue", "legal_regulatory", "credit"}
MACRO_UNIT_FIELDS = {"rates", "inflation", "labor", "housing", "energy", "credit"}
COMPANY_IR_UNIT_FIELDS = {"earnings", "revenue", "capital_return", "energy"}


@dataclass(frozen=True)
class EvidenceUnitGateDecision:
    enabled: bool
    mode: str
    reason_tags: list[str]
    preferred_unit_types: list[str]
    min_label_status: str = "human_or_reviewed_heldout"
    promotion_status: str = "not_applicable"
    promotion_reason: str = ""
    promotion_artifact: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evidence_unit_gate_for_intent(
    intent: QueryIntent,
    promotion_status: EvidenceUnitPromotionStatus | None = None,
) -> EvidenceUnitGateDecision:
    promotion_status = promotion_status or load_evidence_unit_promotion_status()
    fields = set(intent.field_labels)
    routes = set(intent.source_routes)
    reason_tags: list[str] = []
    preferred_unit_types: list[str] = []

    if intent.external_or_user_source:
        return EvidenceUnitGateDecision(
            enabled=False,
            mode="document",
            reason_tags=["external_or_user_source_requires_document_context"],
            preferred_unit_types=[],
            promotion_status=promotion_status.status_label,
            promotion_reason=promotion_status.reason,
            promotion_artifact=promotion_status.artifact_path,
        )

    if "sec_filings" in routes and fields.intersection(SEC_UNIT_FIELDS):
        reason_tags.append("specific_sec_section_intent")
        if "risk_factors" in fields:
            preferred_unit_types.append("sec_section:risk_factors")
        if "earnings" in fields or "revenue" in fields:
            preferred_unit_types.append("sec_exhibit:earnings_or_guidance")
            preferred_unit_types.append("sec_section:mda")
        if "legal_regulatory" in fields:
            preferred_unit_types.append("sec_section:legal_proceedings")
        if "credit" in fields:
            preferred_unit_types.append("sec_section:risk_factors")

    if "official_macro" in routes and fields.intersection(MACRO_UNIT_FIELDS):
        reason_tags.append("specific_macro_observation_intent")
        preferred_unit_types.append("macro_observation")

    if "company_ir" in routes and fields.intersection(COMPANY_IR_UNIT_FIELDS):
        reason_tags.append("specific_company_ir_fact_intent")
        preferred_unit_types.append("company_ir_fact_block")

    if intent.primary_intent in {"filing_fact_lookup", "structured_numeric_lookup"}:
        reason_tags.append("structured_fact_lookup")
        preferred_unit_types.append("sec_section:financial_statements")

    enabled = bool(reason_tags and fields.intersection(HIGH_VALUE_FIELDS))
    if not enabled:
        return EvidenceUnitGateDecision(
            enabled=False,
            mode="document",
            reason_tags=["broad_or_low_specificity_query"],
            preferred_unit_types=[],
            promotion_status=promotion_status.status_label,
            promotion_reason=promotion_status.reason,
            promotion_artifact=promotion_status.artifact_path,
        )

    if not promotion_status.accepted:
        return EvidenceUnitGateDecision(
            enabled=False,
            mode="document",
            reason_tags=sorted(set(reason_tags + ["promotion_gate_not_accepted"])),
            preferred_unit_types=[],
            min_label_status=promotion_status.decision,
            promotion_status=promotion_status.status_label,
            promotion_reason=promotion_status.reason,
            promotion_artifact=promotion_status.artifact_path,
        )

    return EvidenceUnitGateDecision(
        enabled=True,
        mode="evidence_unit_calibrated_source_type_v1",
        reason_tags=sorted(set(reason_tags)),
        preferred_unit_types=sorted(set(preferred_unit_types)),
        min_label_status="mixed_qrels_promotion_gate_accepted",
        promotion_status=promotion_status.status_label,
        promotion_reason=promotion_status.reason,
        promotion_artifact=promotion_status.artifact_path,
    )


def evidence_unit_gate_for_query(query: str) -> EvidenceUnitGateDecision:
    return evidence_unit_gate_for_intent(classify_query_intent(query))
