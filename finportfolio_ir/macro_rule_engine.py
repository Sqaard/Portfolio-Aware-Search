"""Deterministic impact rules for official US macro observations.

Official macro observations are not normal prose documents. A generic LLM or
lexicon extractor tends to overreact to words like "recession risk" in the
retrieval boilerplate and miss the actual economic sign of the value. This
module keeps macro direction, risk, and signal tags in a dedicated rule layer.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any


MACRO_RULE_VERSION = "us_macro_rule_engine_v1"


@dataclass(frozen=True)
class MacroRuleResult:
    series_id: str
    value: float | None
    impact_direction: str
    risk_intensity: float
    uncertainty_intensity: float
    sentiment_proxy: float
    opportunity_intensity: float
    forward_looking_intensity: float
    portfolio_action_relevance: float
    signals: dict[str, int]
    reason: str


def _safe_float(value: Any) -> float | None:
    try:
        if value in ("", None):
            return None
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(parsed) or math.isinf(parsed):
        return None
    return parsed


def _text(row: dict[str, Any]) -> str:
    return " ".join(
        str(part or "")
        for part in [
            row.get("title"),
            row.get("body_excerpt"),
            row.get("body"),
            " ".join(str(item) for item in row.get("event_tags", []) or []),
            " ".join(str(item) for item in row.get("risk_terms", []) or []),
        ]
    )


def parse_macro_series_id(row: dict[str, Any]) -> str:
    explicit = str(row.get("macro_series_id") or "").strip().upper()
    if explicit:
        return explicit
    text = _text(row)
    match = re.search(r"\bSeries\s+([A-Z0-9]+)\s*:", text)
    if match:
        return match.group(1).upper()
    doc_id = str(row.get("doc_id") or "").lower()
    match = re.search(r"official_macro_([a-z0-9]+)_\d{4}-\d{2}-\d{2}", doc_id)
    if match:
        return match.group(1).upper()
    return ""


def parse_macro_value(row: dict[str, Any]) -> float | None:
    explicit = _safe_float(row.get("macro_value"))
    if explicit is not None:
        return explicit
    text = _text(row)
    match = re.search(r"\bValue:\s*([-+]?\d+(?:\.\d+)?)", text)
    if match:
        return _safe_float(match.group(1))
    return None


def _signals(*active: str) -> dict[str, int]:
    names = [
        "signal_earnings_guidance",
        "signal_company_risk",
        "signal_macro_rates",
        "signal_inflation",
        "signal_credit",
        "signal_labor_growth",
        "signal_market_volatility",
        "signal_energy",
        "signal_housing",
        "signal_legal_regulatory",
        "signal_supply_chain",
        "signal_consumer_demand",
        "signal_margin_pressure",
        "signal_capital_return",
        "signal_mna",
    ]
    active_set = set(active)
    return {name: int(name in active_set) for name in names}


def _result(
    *,
    series_id: str,
    value: float | None,
    impact_direction: str,
    risk_intensity: float,
    uncertainty_intensity: float,
    sentiment_proxy: float,
    opportunity_intensity: float,
    portfolio_action_relevance: float,
    signals: dict[str, int],
    reason: str,
) -> MacroRuleResult:
    return MacroRuleResult(
        series_id=series_id,
        value=value,
        impact_direction=impact_direction,
        risk_intensity=round(max(0.0, min(1.0, risk_intensity)), 6),
        uncertainty_intensity=round(max(0.0, min(1.0, uncertainty_intensity)), 6),
        sentiment_proxy=round(max(-1.0, min(1.0, sentiment_proxy)), 6),
        opportunity_intensity=round(max(0.0, min(1.0, opportunity_intensity)), 6),
        forward_looking_intensity=0.0,
        portfolio_action_relevance=round(max(0.0, min(1.0, portfolio_action_relevance)), 6),
        signals=signals,
        reason=reason,
    )


def evaluate_official_macro(row: dict[str, Any]) -> MacroRuleResult | None:
    """Return deterministic macro labels for an official macro observation."""
    source_type = str(row.get("source_type") or "").lower()
    if source_type != "official_macro_release":
        return None

    series_id = parse_macro_series_id(row)
    value = parse_macro_value(row)
    if not series_id or value is None:
        return _result(
            series_id=series_id,
            value=value,
            impact_direction="neutral",
            risk_intensity=0.2,
            uncertainty_intensity=0.3,
            sentiment_proxy=0.0,
            opportunity_intensity=0.0,
            portfolio_action_relevance=0.45,
            signals=_signals("signal_macro_rates"),
            reason="Official macro observation could not be parsed; keep as neutral context.",
        )

    if series_id == "T10Y2Y":
        if value < -0.25:
            return _result(series_id=series_id, value=value, impact_direction="negative", risk_intensity=0.85, uncertainty_intensity=0.35, sentiment_proxy=-0.65, opportunity_intensity=0.0, portfolio_action_relevance=0.85, signals=_signals("signal_macro_rates", "signal_credit"), reason="Inverted 10Y-2Y curve is recession/credit-cycle risk for US equities.")
        if value < 0.25:
            return _result(series_id=series_id, value=value, impact_direction="mixed", risk_intensity=0.55, uncertainty_intensity=0.35, sentiment_proxy=-0.20, opportunity_intensity=0.1, portfolio_action_relevance=0.75, signals=_signals("signal_macro_rates", "signal_credit"), reason="Flat yield curve raises late-cycle and credit-cycle caution.")
        if value >= 1.0:
            return _result(series_id=series_id, value=value, impact_direction="positive", risk_intensity=0.20, uncertainty_intensity=0.20, sentiment_proxy=0.35, opportunity_intensity=0.35, portfolio_action_relevance=0.75, signals=_signals("signal_macro_rates", "signal_credit"), reason="Steep positive yield curve is generally supportive for credit creation and forward growth expectations.")
        return _result(series_id=series_id, value=value, impact_direction="neutral", risk_intensity=0.35, uncertainty_intensity=0.25, sentiment_proxy=0.05, opportunity_intensity=0.15, portfolio_action_relevance=0.65, signals=_signals("signal_macro_rates", "signal_credit"), reason="Moderately positive yield curve is normal macro context.")

    if series_id in {"DGS10", "DGS2", "FEDFUNDS"}:
        if value >= 4.5:
            return _result(series_id=series_id, value=value, impact_direction="negative", risk_intensity=0.75, uncertainty_intensity=0.30, sentiment_proxy=-0.55, opportunity_intensity=0.0, portfolio_action_relevance=0.85, signals=_signals("signal_macro_rates", "signal_credit"), reason="High policy/Treasury rates tighten discount rates, credit, and equity valuation support.")
        if value >= 3.0:
            return _result(series_id=series_id, value=value, impact_direction="mixed", risk_intensity=0.50, uncertainty_intensity=0.25, sentiment_proxy=-0.20, opportunity_intensity=0.10, portfolio_action_relevance=0.75, signals=_signals("signal_macro_rates", "signal_credit"), reason="Moderately high rates are a valuation headwind but not necessarily a stress regime.")
        if value <= 0.5:
            return _result(series_id=series_id, value=value, impact_direction="mixed", risk_intensity=0.35, uncertainty_intensity=0.30, sentiment_proxy=0.10, opportunity_intensity=0.25, portfolio_action_relevance=0.65, signals=_signals("signal_macro_rates", "signal_credit"), reason="Very low rates support valuation but may coincide with weak growth or crisis policy.")
        return _result(series_id=series_id, value=value, impact_direction="neutral", risk_intensity=0.30, uncertainty_intensity=0.20, sentiment_proxy=0.05, opportunity_intensity=0.15, portfolio_action_relevance=0.65, signals=_signals("signal_macro_rates"), reason="Rate level is not extreme; keep as macro context.")

    if series_id == "VIXCLS":
        if value >= 30:
            return _result(series_id=series_id, value=value, impact_direction="negative", risk_intensity=0.85, uncertainty_intensity=0.55, sentiment_proxy=-0.75, opportunity_intensity=0.0, portfolio_action_relevance=0.90, signals=_signals("signal_market_volatility"), reason="High VIX indicates acute equity risk aversion.")
        if value >= 20:
            return _result(series_id=series_id, value=value, impact_direction="mixed", risk_intensity=0.55, uncertainty_intensity=0.35, sentiment_proxy=-0.25, opportunity_intensity=0.05, portfolio_action_relevance=0.75, signals=_signals("signal_market_volatility"), reason="Elevated VIX indicates risk appetite pressure.")
        if value <= 15:
            return _result(series_id=series_id, value=value, impact_direction="positive", risk_intensity=0.20, uncertainty_intensity=0.15, sentiment_proxy=0.35, opportunity_intensity=0.25, portfolio_action_relevance=0.65, signals=_signals("signal_market_volatility"), reason="Low VIX indicates benign risk appetite.")
        return _result(series_id=series_id, value=value, impact_direction="neutral", risk_intensity=0.35, uncertainty_intensity=0.25, sentiment_proxy=0.0, opportunity_intensity=0.1, portfolio_action_relevance=0.65, signals=_signals("signal_market_volatility"), reason="VIX is near a normal range.")

    if series_id == "BAMLH0A0HYM2":
        if value >= 6:
            return _result(series_id=series_id, value=value, impact_direction="negative", risk_intensity=0.85, uncertainty_intensity=0.45, sentiment_proxy=-0.70, opportunity_intensity=0.0, portfolio_action_relevance=0.90, signals=_signals("signal_credit"), reason="Wide high-yield spreads indicate funding stress and default-risk pressure.")
        if value >= 4:
            return _result(series_id=series_id, value=value, impact_direction="mixed", risk_intensity=0.60, uncertainty_intensity=0.35, sentiment_proxy=-0.30, opportunity_intensity=0.05, portfolio_action_relevance=0.80, signals=_signals("signal_credit"), reason="Elevated high-yield spreads are a credit-cycle warning.")
        return _result(series_id=series_id, value=value, impact_direction="positive", risk_intensity=0.25, uncertainty_intensity=0.20, sentiment_proxy=0.30, opportunity_intensity=0.25, portfolio_action_relevance=0.70, signals=_signals("signal_credit"), reason="Contained high-yield spreads indicate lower credit stress.")

    if series_id == "DCOILWTICO":
        if value >= 100:
            return _result(series_id=series_id, value=value, impact_direction="mixed", risk_intensity=0.60, uncertainty_intensity=0.35, sentiment_proxy=-0.15, opportunity_intensity=0.20, portfolio_action_relevance=0.80, signals=_signals("signal_energy", "signal_inflation", "signal_margin_pressure"), reason="Very high oil supports energy cash flow but pressures inflation, margins, and consumers.")
        if value <= 40:
            return _result(series_id=series_id, value=value, impact_direction="mixed", risk_intensity=0.55, uncertainty_intensity=0.35, sentiment_proxy=-0.20, opportunity_intensity=0.10, portfolio_action_relevance=0.75, signals=_signals("signal_energy", "signal_labor_growth"), reason="Very low oil can reflect weak global demand while helping input costs.")
        return _result(series_id=series_id, value=value, impact_direction="neutral", risk_intensity=0.35, uncertainty_intensity=0.25, sentiment_proxy=0.0, opportunity_intensity=0.15, portfolio_action_relevance=0.65, signals=_signals("signal_energy", "signal_inflation"), reason="Oil price is relevant but not extreme.")

    if series_id == "CPIAUCSL":
        return _result(series_id=series_id, value=value, impact_direction="neutral", risk_intensity=0.35, uncertainty_intensity=0.25, sentiment_proxy=0.0, opportunity_intensity=0.0, portfolio_action_relevance=0.70, signals=_signals("signal_inflation", "signal_margin_pressure"), reason="CPI index level alone is inflation evidence; direction needs change/yoy context.")

    if series_id == "UNRATE":
        if value >= 7:
            return _result(series_id=series_id, value=value, impact_direction="negative", risk_intensity=0.80, uncertainty_intensity=0.35, sentiment_proxy=-0.65, opportunity_intensity=0.0, portfolio_action_relevance=0.85, signals=_signals("signal_labor_growth", "signal_consumer_demand"), reason="High unemployment indicates weak labor income and demand risk.")
        if value <= 4:
            return _result(series_id=series_id, value=value, impact_direction="positive", risk_intensity=0.25, uncertainty_intensity=0.20, sentiment_proxy=0.35, opportunity_intensity=0.30, portfolio_action_relevance=0.70, signals=_signals("signal_labor_growth", "signal_consumer_demand", "signal_margin_pressure"), reason="Low unemployment supports demand but can add wage pressure.")
        return _result(series_id=series_id, value=value, impact_direction="neutral", risk_intensity=0.35, uncertainty_intensity=0.25, sentiment_proxy=0.0, opportunity_intensity=0.15, portfolio_action_relevance=0.65, signals=_signals("signal_labor_growth", "signal_consumer_demand"), reason="Unemployment is not at an extreme level.")

    if series_id == "HOUST":
        if value <= 900:
            return _result(series_id=series_id, value=value, impact_direction="negative", risk_intensity=0.65, uncertainty_intensity=0.35, sentiment_proxy=-0.45, opportunity_intensity=0.0, portfolio_action_relevance=0.75, signals=_signals("signal_housing", "signal_labor_growth"), reason="Weak housing starts indicate real-economy and construction demand risk.")
        if value >= 1500:
            return _result(series_id=series_id, value=value, impact_direction="positive", risk_intensity=0.30, uncertainty_intensity=0.25, sentiment_proxy=0.30, opportunity_intensity=0.30, portfolio_action_relevance=0.70, signals=_signals("signal_housing", "signal_labor_growth"), reason="Strong housing starts support housing and cyclical demand.")
        return _result(series_id=series_id, value=value, impact_direction="neutral", risk_intensity=0.35, uncertainty_intensity=0.25, sentiment_proxy=0.0, opportunity_intensity=0.15, portfolio_action_relevance=0.65, signals=_signals("signal_housing"), reason="Housing starts are not at an extreme level.")

    if series_id in {"PAYEMS", "INDPRO"}:
        return _result(series_id=series_id, value=value, impact_direction="neutral", risk_intensity=0.30, uncertainty_intensity=0.25, sentiment_proxy=0.0, opportunity_intensity=0.15, portfolio_action_relevance=0.65, signals=_signals("signal_labor_growth", "signal_consumer_demand"), reason="Level-only growth series needs momentum/change context for directional impact.")

    return _result(
        series_id=series_id,
        value=value,
        impact_direction="neutral",
        risk_intensity=0.30,
        uncertainty_intensity=0.25,
        sentiment_proxy=0.0,
        opportunity_intensity=0.10,
        portfolio_action_relevance=0.60,
        signals=_signals("signal_macro_rates"),
        reason=f"No specialized macro rule for series {series_id}; keep as neutral context.",
    )
