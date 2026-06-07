"""Build deterministic text features from FinIR daily retrieval contexts.

This is the local "teacher" baseline used before a paid/external LLM pass. It
does not try to pretend to be a full LLM extraction. Instead it produces stable
financial signal features, daily PPO-ready aggregates, and a stratified seed set
that can be compared against Mistral/FinGPT outputs later.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from finportfolio_ir.io_utils import read_jsonl, write_jsonl  # noqa: E402
from finportfolio_ir.macro_rule_engine import MACRO_RULE_VERSION, evaluate_official_macro  # noqa: E402


FEATURE_VERSION = "codex_rule_teacher_v2_macro_rules"


LEXICONS: dict[str, tuple[str, ...]] = {
    "positive": (
        "accelerat",
        "beat",
        "benefit",
        "better than expected",
        "buyback",
        "cash return",
        "confidence",
        "expand",
        "favorable",
        "growth",
        "improve",
        "increase",
        "margin expansion",
        "outperform",
        "raise",
        "record",
        "recover",
        "resilient",
        "strong",
        "tailwind",
        "upgrade",
    ),
    "negative": (
        "bankruptcy",
        "challenge",
        "constrain",
        "decline",
        "delay",
        "downgrade",
        "fall",
        "headwind",
        "impairment",
        "investigation",
        "lawsuit",
        "lower",
        "miss",
        "pressure",
        "recession",
        "reduce",
        "slow",
        "soft",
        "uncertain",
        "weak",
    ),
    "uncertainty": (
        "could",
        "estimate",
        "expect",
        "may",
        "might",
        "outlook",
        "possible",
        "risk",
        "uncertain",
        "volatil",
        "would",
    ),
    "forward_looking": (
        "anticipate",
        "expect",
        "forecast",
        "guidance",
        "next quarter",
        "outlook",
        "project",
        "will",
    ),
    "rates": (
        "fed",
        "federal reserve",
        "interest rate",
        "real yield",
        "treasury",
        "yield curve",
    ),
    "inflation": (
        "cpi",
        "consumer price",
        "inflation",
        "price pressure",
        "ppi",
    ),
    "credit": (
        "bank lending",
        "credit",
        "default",
        "funding cost",
        "high yield",
        "loan demand",
        "spread",
    ),
    "labor_growth": (
        "employment",
        "growth",
        "industrial production",
        "jobs",
        "payroll",
        "unemployment",
    ),
    "market_volatility": (
        "risk appetite",
        "selloff",
        "vix",
        "volatil",
    ),
    "energy": (
        "brent",
        "crude",
        "energy",
        "oil",
        "wti",
    ),
    "housing": (
        "construction",
        "home",
        "housing",
        "mortgage",
        "real estate",
    ),
    "legal_regulatory": (
        "antitrust",
        "compliance",
        "investigation",
        "lawsuit",
        "legal",
        "litigation",
        "regulation",
        "regulatory",
        "sanction",
    ),
    "supply_chain": (
        "backlog",
        "component shortage",
        "inventory",
        "logistics",
        "shortage",
        "supply chain",
    ),
    "consumer_demand": (
        "consumer",
        "demand",
        "retail",
        "spending",
        "traffic",
    ),
    "margin_pressure": (
        "cost pressure",
        "gross margin",
        "input cost",
        "margin pressure",
        "operating margin",
        "wage pressure",
    ),
    "capital_return": (
        "buyback",
        "cash return",
        "dividend",
        "repurchase",
        "shareholder return",
    ),
    "mna": (
        "acquisition",
        "divestiture",
        "merger",
        "spin-off",
        "takeover",
    ),
}


SIGNAL_COLUMNS = (
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
)

NUMERIC_FEATURES = (
    "final_score",
    "bm25_score",
    "freshness_score",
    "risk_term_score",
    "event_severity_score",
    "macro_regime_relevance_score",
    "age_days",
    "decay_weight_7d",
    "decay_weight_30d",
    "decay_weight_90d",
    "sentiment_proxy",
    "risk_intensity",
    "uncertainty_intensity",
    "opportunity_intensity",
    "forward_looking_intensity",
    "numerical_density",
    "portfolio_action_relevance",
    *SIGNAL_COLUMNS,
)


def _text(row: dict[str, Any]) -> str:
    parts = [
        str(row.get("title", "")),
        str(row.get("body_excerpt", "")),
        " ".join(str(item) for item in row.get("event_tags", []) or []),
        " ".join(str(item) for item in row.get("risk_terms", []) or []),
        str(row.get("query_intent_primary", "")),
    ]
    return " ".join(parts)


def _count_terms(text_lower: str, terms: Iterable[str]) -> int:
    count = 0
    for term in terms:
        term_lower = term.lower()
        if " " in term_lower:
            count += text_lower.count(term_lower)
        else:
            count += len(re.findall(rf"\b{re.escape(term_lower)}\w*\b", text_lower))
    return count


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in ("", None):
            return default
        parsed = float(value)
        if math.isnan(parsed) or math.isinf(parsed):
            return default
        return parsed
    except (TypeError, ValueError):
        return default


def _score(count: int, denominator: int) -> float:
    return round(min(1.0, count / max(denominator, 1)), 6)


def _direction(sentiment_proxy: float, risk_intensity: float) -> str:
    if sentiment_proxy >= 0.18 and risk_intensity < 0.35:
        return "positive"
    if sentiment_proxy <= -0.18 or risk_intensity >= 0.65:
        return "negative"
    if abs(sentiment_proxy) < 0.08 and risk_intensity < 0.30:
        return "neutral"
    return "mixed"


def extract_doc_features(row: dict[str, Any]) -> dict[str, Any]:
    text = _text(row)
    text_lower = text.lower()
    token_count = max(1, len(re.findall(r"[a-zA-Z][a-zA-Z\-']+", text_lower)))
    numeric_count = len(re.findall(r"\b\d+(?:\.\d+)?%?\b", text_lower))
    event_tags = {str(item).lower() for item in row.get("event_tags", []) or []}
    risk_terms = {str(item).lower() for item in row.get("risk_terms", []) or []}
    intent = str(row.get("query_intent_primary", "")).lower()
    source_type = str(row.get("source_type", "")).lower()

    counts = {name: _count_terms(text_lower, terms) for name, terms in LEXICONS.items()}
    positive = counts["positive"]
    negative = counts["negative"]
    sentiment_proxy = round(max(-1.0, min(1.0, (positive - negative) / max(positive + negative, 3))), 6)
    risk_intensity = _score(
        negative
        + counts["uncertainty"]
        + len(risk_terms)
        + int("risk_factors" in event_tags)
        + int("legal_regulatory" in intent),
        10,
    )
    opportunity_intensity = _score(positive + counts["capital_return"], 8)
    uncertainty_intensity = _score(counts["uncertainty"], 8)
    forward_looking_intensity = _score(counts["forward_looking"] + int("guidance" in intent), 7)
    numerical_density = round(min(1.0, numeric_count / max(token_count / 100.0, 1.0) / 12.0), 6)

    signals = {
        "signal_earnings_guidance": int(
            "earnings_guidance" in intent
            or "earnings_release_candidate" in event_tags
            or "guidance" in event_tags
            or "exhibit_99" in text_lower
        ),
        "signal_company_risk": int("company_risk" in intent or "risk_factors" in event_tags),
        "signal_macro_rates": int(counts["rates"] > 0 or intent == "rates_policy"),
        "signal_inflation": int(counts["inflation"] > 0 or intent == "inflation_pressure"),
        "signal_credit": int(counts["credit"] > 0 or intent == "credit_stress"),
        "signal_labor_growth": int(counts["labor_growth"] > 0 or intent == "macro_growth"),
        "signal_market_volatility": int(counts["market_volatility"] > 0 or intent == "market_volatility"),
        "signal_energy": int(counts["energy"] > 0),
        "signal_housing": int(counts["housing"] > 0),
        "signal_legal_regulatory": int(counts["legal_regulatory"] > 0 or intent == "legal_regulatory"),
        "signal_supply_chain": int(counts["supply_chain"] > 0),
        "signal_consumer_demand": int(counts["consumer_demand"] > 0),
        "signal_margin_pressure": int(counts["margin_pressure"] > 0),
        "signal_capital_return": int(counts["capital_return"] > 0),
        "signal_mna": int(counts["mna"] > 0),
    }
    macro_signal_count = sum(
        signals[name]
        for name in [
            "signal_macro_rates",
            "signal_inflation",
            "signal_credit",
            "signal_labor_growth",
            "signal_market_volatility",
            "signal_energy",
            "signal_housing",
        ]
    )
    company_signal_count = sum(
        signals[name]
        for name in [
            "signal_earnings_guidance",
            "signal_company_risk",
            "signal_legal_regulatory",
            "signal_supply_chain",
            "signal_consumer_demand",
            "signal_margin_pressure",
            "signal_capital_return",
            "signal_mna",
        ]
    )
    portfolio_action_relevance = min(
        1.0,
        0.20
        + 0.12 * macro_signal_count
        + 0.10 * company_signal_count
        + 0.15 * _safe_float(row.get("event_severity_score"))
        + 0.10 * _safe_float(row.get("macro_regime_relevance_score"))
        + 0.10 * int(source_type in {"sec_filing_exhibit", "official_macro_release"}),
    )
    macro_rule = evaluate_official_macro(row)
    macro_rule_reason = ""
    macro_rule_series_id = ""
    macro_rule_value: float | str = ""
    if macro_rule is not None:
        sentiment_proxy = macro_rule.sentiment_proxy
        risk_intensity = macro_rule.risk_intensity
        uncertainty_intensity = macro_rule.uncertainty_intensity
        opportunity_intensity = macro_rule.opportunity_intensity
        forward_looking_intensity = macro_rule.forward_looking_intensity
        portfolio_action_relevance = macro_rule.portfolio_action_relevance
        signals.update(macro_rule.signals)
        macro_rule_reason = macro_rule.reason
        macro_rule_series_id = macro_rule.series_id
        macro_rule_value = "" if macro_rule.value is None else macro_rule.value

    features: dict[str, Any] = {
        "feature_version": FEATURE_VERSION,
        "daily_context_id": row.get("daily_context_id", ""),
        "doc_id": row.get("doc_id", ""),
        "document_hash": row.get("document_hash", ""),
        "duplicate_cluster_id": row.get("duplicate_cluster_id", ""),
        "decision_date": row.get("decision_date", ""),
        "decision_time": row.get("decision_time", ""),
        "available_at": row.get("available_at", ""),
        "split": row.get("split", ""),
        "document_split": row.get("document_split", row.get("split", "")),
        "regime": row.get("regime", ""),
        "retrieval_layer": row.get("retrieval_layer", ""),
        "target_ticker": row.get("target_ticker", ""),
        "tic": row.get("tic", "") or ("" if row.get("target_ticker") == "PORTFOLIO" else row.get("target_ticker", "")),
        "source": row.get("source", ""),
        "source_type": row.get("source_type", ""),
        "source_reliability_tier": row.get("source_reliability_tier", ""),
        "query_intent_primary": row.get("query_intent_primary", ""),
        "age_bucket": row.get("age_bucket", ""),
        "impact_direction": macro_rule.impact_direction if macro_rule is not None else _direction(sentiment_proxy, risk_intensity),
        "macro_rule_version": MACRO_RULE_VERSION if macro_rule is not None else "",
        "macro_rule_series_id": macro_rule_series_id,
        "macro_rule_value": macro_rule_value,
        "macro_rule_reason": macro_rule_reason,
        "positive_term_count": positive,
        "negative_term_count": negative,
        "numeric_token_count": numeric_count,
        "token_count_proxy": token_count,
        "sentiment_proxy": sentiment_proxy,
        "risk_intensity": risk_intensity,
        "uncertainty_intensity": uncertainty_intensity,
        "opportunity_intensity": opportunity_intensity,
        "forward_looking_intensity": forward_looking_intensity,
        "numerical_density": numerical_density,
        "portfolio_action_relevance": round(portfolio_action_relevance, 6),
    }
    for column in [
        "final_score",
        "bm25_score",
        "freshness_score",
        "risk_term_score",
        "event_severity_score",
        "macro_regime_relevance_score",
        "age_days",
        "decay_weight_7d",
        "decay_weight_30d",
        "decay_weight_90d",
    ]:
        features[column] = _safe_float(row.get(column))
    features.update(signals)
    return features


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _weighted_mean(rows: list[dict[str, Any]], column: str) -> float:
    numerator = 0.0
    denominator = 0.0
    for row in rows:
        weight = max(_safe_float(row.get("final_score")), 0.05) * max(_safe_float(row.get("decay_weight_30d")), 0.01)
        numerator += _safe_float(row.get(column)) * weight
        denominator += weight
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 6)


def _max_value(rows: list[dict[str, Any]], column: str) -> float:
    return round(max((_safe_float(row.get(column)) for row in rows), default=0.0), 6)


def aggregate_features(doc_features: list[dict[str, Any]], *, layer: str) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in doc_features:
        if row.get("retrieval_layer") != layer:
            continue
        key = (str(row.get("decision_date", "")), str(row.get("tic", "")) if layer == "stock" else "PORTFOLIO")
        if not key[0]:
            continue
        groups[key].append(row)

    output: list[dict[str, Any]] = []
    for (decision_date, ticker), rows in sorted(groups.items()):
        prefix = "stock" if layer == "stock" else "portfolio"
        aggregated: dict[str, Any] = {
            "date": decision_date,
            "decision_date": decision_date,
            "tic": ticker if layer == "stock" else "",
            "retrieval_layer": layer,
            f"{prefix}_text_doc_count": len(rows),
            f"{prefix}_text_unique_doc_count": len({row.get("doc_id", "") for row in rows}),
            f"{prefix}_text_avg_age_days": _weighted_mean(rows, "age_days"),
            f"{prefix}_text_avg_final_score": _weighted_mean(rows, "final_score"),
            f"{prefix}_text_max_event_severity": _max_value(rows, "event_severity_score"),
            f"{prefix}_text_avg_risk_intensity": _weighted_mean(rows, "risk_intensity"),
            f"{prefix}_text_avg_uncertainty": _weighted_mean(rows, "uncertainty_intensity"),
            f"{prefix}_text_avg_sentiment_proxy": _weighted_mean(rows, "sentiment_proxy"),
            f"{prefix}_text_avg_action_relevance": _weighted_mean(rows, "portfolio_action_relevance"),
        }
        for signal in SIGNAL_COLUMNS:
            aggregated[f"{prefix}_{signal}_count"] = int(sum(_safe_float(row.get(signal)) for row in rows))
            aggregated[f"{prefix}_{signal}_flag"] = int(any(_safe_float(row.get(signal)) > 0 for row in rows))
        output.append(aggregated)
    return output


def _rationale(row: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    macro_reason = str(row.get("macro_rule_reason", ""))
    if macro_reason:
        reasons.append(macro_reason)
    intent = str(row.get("query_intent_primary", ""))
    if intent:
        reasons.append(f"Primary retrieval intent is {intent}.")
    active_signals = [name.replace("signal_", "") for name in SIGNAL_COLUMNS if _safe_float(row.get(name)) > 0]
    if active_signals:
        reasons.append("Active signal families: " + ", ".join(active_signals[:5]) + ".")
    if _safe_float(row.get("risk_intensity")) >= 0.5:
        reasons.append("Risk intensity is elevated from risk/uncertainty vocabulary or risk tags.")
    if _safe_float(row.get("opportunity_intensity")) >= 0.3:
        reasons.append("Positive/opportunity vocabulary is present.")
    if str(row.get("source_reliability_tier", "")).lower() == "official":
        reasons.append("Source reliability tier is official.")
    return reasons or ["No strong specialized signal; keep as low-intensity contextual evidence."]


def build_teacher_seed(doc_rows: list[dict[str, Any]], contexts_by_id: dict[str, dict[str, Any]], *, size: int) -> list[dict[str, Any]]:
    if size <= 0:
        return []
    sorted_rows = sorted(
        doc_rows,
        key=lambda row: (
            str(row.get("query_intent_primary", "")),
            str(row.get("retrieval_layer", "")),
            -_safe_float(row.get("portfolio_action_relevance")),
            -_safe_float(row.get("final_score")),
        ),
    )
    selected: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str, str]] = set()
    selected_contexts: set[str] = set()

    def add_row(row: dict[str, Any]) -> None:
        context_id = str(row.get("daily_context_id", ""))
        context = contexts_by_id.get(context_id, {})
        selected_contexts.add(context_id)
        selected.append(
            {
                "teacher_id": f"{FEATURE_VERSION}:{len(selected) + 1:04d}",
                "feature_version": FEATURE_VERSION,
                "daily_context_id": row.get("daily_context_id", ""),
                "doc_id": row.get("doc_id", ""),
                "decision_date": row.get("decision_date", ""),
                "decision_time": row.get("decision_time", ""),
                "available_at": row.get("available_at", ""),
                "split": row.get("split", ""),
                "document_split": row.get("document_split", row.get("split", "")),
                "regime": row.get("regime", ""),
                "retrieval_layer": row.get("retrieval_layer", ""),
                "target_ticker": row.get("target_ticker", ""),
                "source_type": row.get("source_type", ""),
                "query_intent_primary": row.get("query_intent_primary", ""),
                "title": context.get("title", ""),
                "excerpt": str(context.get("body_excerpt", ""))[:1600],
                "labels": {
                    "impact_direction": row.get("impact_direction", ""),
                    "risk_intensity": row.get("risk_intensity", 0.0),
                    "uncertainty_intensity": row.get("uncertainty_intensity", 0.0),
                    "sentiment_proxy": row.get("sentiment_proxy", 0.0),
                    "portfolio_action_relevance": row.get("portfolio_action_relevance", 0.0),
                    "active_signals": [name for name in SIGNAL_COLUMNS if _safe_float(row.get(name)) > 0],
                },
                "teacher_confidence": "high" if _safe_float(row.get("portfolio_action_relevance")) >= 0.65 else "medium",
                "teacher_rationale": _rationale(row),
                "comparison_use": "seed row for Mistral-vs-Codex feature extraction QA",
            }
        )

    for row in sorted_rows:
        key = (
            str(row.get("query_intent_primary", "")),
            str(row.get("retrieval_layer", "")),
            str(row.get("target_ticker", "")),
        )
        if key in seen_keys:
            continue
        seen_keys.add(key)
        add_row(row)
        if len(selected) >= size:
            break
    for row in sorted_rows:
        if len(selected) >= size:
            break
        context_id = str(row.get("daily_context_id", ""))
        if context_id in selected_contexts:
            continue
        add_row(row)
    return selected


def build_text_feature_baseline(
    *,
    contexts_path: Path,
    output_dir: Path,
    teacher_size: int,
) -> dict[str, Any]:
    contexts = read_jsonl(contexts_path)
    contexts_by_id = {str(row.get("daily_context_id", "")): row for row in contexts}
    doc_features = [extract_doc_features(row) for row in contexts]
    stock_features = aggregate_features(doc_features, layer="stock")
    portfolio_features = aggregate_features(doc_features, layer="portfolio")
    teacher_seed = build_teacher_seed(doc_features, contexts_by_id, size=teacher_size)

    doc_fieldnames = [
        "feature_version",
        "daily_context_id",
        "doc_id",
        "document_hash",
        "duplicate_cluster_id",
        "decision_date",
        "decision_time",
        "available_at",
        "split",
        "document_split",
        "regime",
        "retrieval_layer",
        "target_ticker",
        "tic",
        "source",
        "source_type",
        "source_reliability_tier",
        "query_intent_primary",
        "age_bucket",
        "impact_direction",
        "macro_rule_version",
        "macro_rule_series_id",
        "macro_rule_value",
        "macro_rule_reason",
        "positive_term_count",
        "negative_term_count",
        "numeric_token_count",
        "token_count_proxy",
        *NUMERIC_FEATURES,
    ]
    doc_fieldnames = list(dict.fromkeys(doc_fieldnames))

    stock_fieldnames = sorted({key for row in stock_features for key in row})
    portfolio_fieldnames = sorted({key for row in portfolio_features for key in row})
    _write_csv(output_dir / "doc_text_features_codex_rule.csv", doc_features, doc_fieldnames)
    _write_csv(output_dir / "daily_stock_text_features_codex_rule.csv", stock_features, stock_fieldnames)
    _write_csv(output_dir / "daily_portfolio_text_features_codex_rule.csv", portfolio_features, portfolio_fieldnames)
    write_jsonl(output_dir / "codex_teacher_seed.jsonl", teacher_seed)

    diagnostics = {
        "feature_version": FEATURE_VERSION,
        "input_contexts": str(contexts_path),
        "context_rows": len(contexts),
        "doc_feature_rows": len(doc_features),
        "daily_stock_rows": len(stock_features),
        "daily_portfolio_rows": len(portfolio_features),
        "teacher_seed_rows": len(teacher_seed),
        "retrieval_layer_counts": dict(Counter(str(row.get("retrieval_layer", "")) for row in doc_features)),
        "source_type_counts": dict(Counter(str(row.get("source_type", "")) for row in doc_features)),
        "query_intent_counts": dict(Counter(str(row.get("query_intent_primary", "")) for row in doc_features)),
        "impact_direction_counts": dict(Counter(str(row.get("impact_direction", "")) for row in doc_features)),
        "unique_decision_dates": len({str(row.get("decision_date", "")) for row in doc_features}),
        "unique_stock_tickers": len({str(row.get("tic", "")) for row in doc_features if row.get("tic")}),
        "outputs": {
            "doc_text_features": str(output_dir / "doc_text_features_codex_rule.csv"),
            "daily_stock_text_features": str(output_dir / "daily_stock_text_features_codex_rule.csv"),
            "daily_portfolio_text_features": str(output_dir / "daily_portfolio_text_features_codex_rule.csv"),
            "teacher_seed": str(output_dir / "codex_teacher_seed.jsonl"),
        },
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "text_feature_diagnostics.json").write_text(
        json.dumps(diagnostics, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return diagnostics


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build deterministic Codex-rule text features from FinIR contexts.")
    parser.add_argument("--contexts", required=True)
    parser.add_argument("--output-dir", default="data/exports/text_features_codex_rule")
    parser.add_argument("--teacher-size", type=int, default=200)
    args = parser.parse_args(argv)

    diagnostics = build_text_feature_baseline(
        contexts_path=Path(args.contexts),
        output_dir=Path(args.output_dir),
        teacher_size=args.teacher_size,
    )
    print(json.dumps(diagnostics, ensure_ascii=False, indent=2))
    return 0 if diagnostics["context_rows"] == diagnostics["doc_feature_rows"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
