"""Shared source/type/freshness calibration for evidence-unit retrieval."""

from __future__ import annotations

import re


DEFAULT_EVIDENCE_UNIT_WEIGHTS: dict[str, float] = {
    "fresh_2021_plus": 0.12,
    "recent_2019_2020": 0.03,
    "stale_pre_2016": -0.06,
    "risk_factor": 0.12,
    "earnings_or_guidance": 0.10,
    "mda": 0.06,
    "business_section": -0.08,
    "financial_statements": -0.08,
    "company_ir_block": -0.02,
    "company_guidance": 0.10,
    "company_financial_fact": 0.06,
    "company_risk_fact": 0.08,
    "company_operating_fact": 0.02,
    "generic_company_ir_fact": -0.03,
}


def _text(row: dict[str, object], *fields: str) -> str:
    return " ".join(str(row.get(field, "") or "") for field in fields).lower()


def published_year(row: dict[str, object]) -> int:
    for field in ("published_at", "available_at", "title"):
        match = re.search(r"(\d{4})-\d{2}-\d{2}", str(row.get(field, "") or ""))
        if match:
            return int(match.group(1))
    return 0


def evidence_unit_calibration_features(row: dict[str, object]) -> dict[str, float]:
    claim = str(row.get("evidence_unit_claim_type", "") or "")
    unit_type = str(row.get("evidence_unit_type", "") or "")
    text = _text(row, "title", "body_excerpt", "body", "source_type")
    title = _text(row, "title")
    year = published_year(row)
    is_company_ir = unit_type == "company_ir_fact_block"
    return {
        "fresh_2021_plus": 1.0 if year >= 2021 else 0.0,
        "recent_2019_2020": 1.0 if 2019 <= year < 2021 else 0.0,
        "stale_pre_2016": 1.0 if year and year < 2016 else 0.0,
        "risk_factor": 1.0
        if claim in {"risk_factors", "company_risk_fact"} or "item 1a risk factors" in title
        else 0.0,
        "earnings_or_guidance": 1.0
        if claim in {"filing_exhibit", "company_guidance"} or "earnings release" in text or "guidance" in text
        else 0.0,
        "mda": 1.0 if claim == "mda" else 0.0,
        "business_section": 1.0 if claim == "filing_section" and "business" in text else 0.0,
        "financial_statements": 1.0 if claim == "financial_statements" else 0.0,
        "company_ir_block": 1.0 if is_company_ir else 0.0,
        "company_guidance": 1.0 if claim == "company_guidance" else 0.0,
        "company_financial_fact": 1.0 if claim == "company_financial_fact" else 0.0,
        "company_risk_fact": 1.0 if claim == "company_risk_fact" else 0.0,
        "company_operating_fact": 1.0 if claim == "company_operating_fact" else 0.0,
        "generic_company_ir_fact": 1.0 if claim == "company_ir_fact" else 0.0,
    }


def evidence_unit_calibration_delta(
    row: dict[str, object],
    weights: dict[str, float] | None = None,
) -> float:
    weights = weights or DEFAULT_EVIDENCE_UNIT_WEIGHTS
    features = evidence_unit_calibration_features(row)
    return sum(float(weights.get(name, 0.0)) * value for name, value in features.items())


def active_evidence_unit_calibration_tags(row: dict[str, object]) -> list[str]:
    features = evidence_unit_calibration_features(row)
    return [name for name, value in sorted(features.items()) if value]
