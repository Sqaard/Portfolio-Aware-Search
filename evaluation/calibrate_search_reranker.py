"""Calibrate an interpretable reranker from search relevance labels.

The web search stack is still deliberately rule based. This script adds the
next evaluation layer: learn small, inspectable weights from a judged search
pool and verify them with query-level cross-validation before considering a
live ranking change.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional, Union


BASE_FEATURE_NAMES = [
    "base_score",
    "signal_strength",
    "rank_prior",
    "expected_ticker_match",
    "wrong_company",
    "source_scope_match",
    "sec_scope_match",
    "macro_scope_match",
    "company_ir_scope_match",
    "field_exact",
    "field_bad",
]

SECTION_FEATURE_NAMES = [
    "risk_factor_section_match",
    "legal_section_match",
    "mda_section_match",
    "earnings_release_match",
    "results_item_match",
    "item_901_wrapper",
    "financial_statement_section",
    "risk_query_earnings_release_bad",
    "nonfinancial_press_bad",
    "energy_theme_match",
]

FEATURE_NAMES = BASE_FEATURE_NAMES + SECTION_FEATURE_NAMES

INITIAL_WEIGHTS = {
    "base_score": 1.0,
    "signal_strength": 0.25,
    "rank_prior": 0.10,
    "expected_ticker_match": 0.75,
    "wrong_company": -1.00,
    "source_scope_match": 0.50,
    "sec_scope_match": 0.25,
    "macro_scope_match": 0.25,
    "company_ir_scope_match": 0.25,
    "field_exact": 0.75,
    "field_bad": -1.00,
    "risk_factor_section_match": 0.50,
    "legal_section_match": 0.25,
    "mda_section_match": 0.25,
    "earnings_release_match": 0.50,
    "results_item_match": 0.50,
    "item_901_wrapper": -0.25,
    "financial_statement_section": 0.00,
    "risk_query_earnings_release_bad": -0.50,
    "nonfinancial_press_bad": -0.50,
    "energy_theme_match": 0.50,
}

GRID_VALUES = [-4.0, -2.0, -1.0, -0.5, 0.0, 0.5, 1.0, 2.0, 4.0]
FINANCIAL_CREDIT_TICKERS = {"AXP", "GS", "JPM", "TRV", "V"}


def load_csv(path: Union[str, Path]) -> list[dict[str, str]]:
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def load_qrels(path: Union[str, Path]) -> dict[str, dict[str, int]]:
    qrels: dict[str, dict[str, int]] = defaultdict(dict)
    for row in load_csv(path):
        qrels[row["query_id"]][row["doc_id"]] = int(row["relevance"])
    return qrels


def load_queries(path: Union[str, Path]) -> dict[str, dict[str, str]]:
    rows = load_csv(path)
    return {row["query_id"]: row for row in rows}


def _split_list(value: str) -> set[str]:
    return {item.strip().upper() for item in str(value or "").split("|") if item.strip()}


def _has_any(text: str, phrases: tuple[str, ...]) -> bool:
    return any(phrase in text for phrase in phrases)


def _document_text(row: dict[str, str]) -> str:
    return " ".join(
        [
            row.get("title", ""),
            row.get("source_type", ""),
            row.get("event_tags", ""),
            row.get("risk_terms", ""),
            row.get("excerpt", ""),
        ]
    ).lower().replace("_", " ").replace("’", "'").replace("`", "'")


def query_field_profile(query: str) -> set[str]:
    normalized = " ".join(str(query or "").lower().split())
    profile: set[str] = set()
    if _has_any(normalized, ("earnings", "guidance", "eps", "results of operations")):
        profile.add("earnings_guidance")
    if _has_any(normalized, ("risk", "risk factors", "litigation", "lawsuit", "legal", "regulatory", "supply chain")):
        profile.add("company_risk")
    if _has_any(normalized, ("litigation", "lawsuit", "legal proceedings", "regulatory")):
        profile.add("legal_regulatory")
    if "supply chain" in normalized:
        profile.add("supply_chain")
    if _has_any(normalized, ("energy", "oil", "wti", "crude", "commodity demand")):
        profile.add("energy")
    if _has_any(normalized, ("consumer demand", "consumer spending", "spending", "payments demand", "card spending")):
        profile.add("consumer_demand")
    if _has_any(normalized, ("bank", "banks", "banking", "credit cycle", "credit risk")):
        profile.add("bank_credit")
    if _has_any(normalized, ("margin", "margins", "profitability", "cost pressure")):
        profile.add("margin_pressure")
    return profile


def field_match_features(query: str, row: dict[str, str]) -> tuple[float, float]:
    profile = query_field_profile(query)
    if not profile:
        return 0.0, 0.0

    text = _document_text(row)
    source_type = row.get("source_type", "").lower()
    matched_tickers = _split_list(row.get("matched_tickers", ""))
    exact = 0.0
    bad = 0.0

    if "earnings_guidance" in profile:
        if _has_any(text, ("earnings guidance", "earnings release", "results of operations", "item 2.02")):
            exact += 1.0
        elif source_type.startswith("company_") and "press release" in text:
            bad += 1.0

    if "company_risk" in profile:
        if _has_any(
            text,
            (
                "risk factors",
                "company risk",
                "market risk",
                "legal regulatory",
                "legal proceedings",
                "mda",
                "management s discussion",
            ),
        ):
            exact += 1.0
        if _has_any(text, ("earnings guidance", "earnings release")) and not _has_any(text, ("risk", "legal", "supply chain")):
            bad += 1.0

    if "legal_regulatory" in profile and _has_any(text, ("legal regulatory", "legal proceedings", "litigation", "lawsuit", "regulatory")):
        exact += 1.0
    if "supply_chain" in profile and _has_any(text, ("supply chain", "supplier", "production", "inventory", "company risk", "mda")):
        exact += 1.0
    if "energy" in profile and _has_any(text, ("energy", "oil", "commodity", "market risk", "risk factors")):
        exact += 1.0
    if "consumer_demand" in profile and _has_any(text, ("consumer demand", "consumer spending", "card member spending", "sales", "revenue")):
        exact += 1.0
    if "bank_credit" in profile:
        if matched_tickers.intersection(FINANCIAL_CREDIT_TICKERS):
            exact += 1.0
        elif matched_tickers and "MARKET" not in matched_tickers:
            bad += 1.0
        if _has_any(text, ("credit", "loan", "deposit", "financial statements", "mda", "company risk")):
            exact += 1.0
    if "margin_pressure" in profile and _has_any(text, ("margin pressure", "operating income", "gross margin", "cost", "mda")):
        exact += 1.0

    return exact, bad


def section_intent_features(query: str, row: dict[str, str]) -> dict[str, float]:
    """Document-form features for ranking within the same ticker/source scope.

    The earlier field_exact/field_bad features catch broad topical matches. These
    features are intentionally narrower: they let qrels teach whether an Item 1A,
    MD&A, earnings exhibit, or wrapper section is the best evidence type for a
    particular intent.
    """

    profile = query_field_profile(query)
    text = _document_text(row)
    title = row.get("title", "").lower().replace("’", "'")
    source_type = row.get("source_type", "").lower()
    is_company_press = source_type.startswith("company_")

    is_risk_factor = _has_any(text, ("item 1a", "risk factors"))
    is_legal = _has_any(text, ("legal proceedings", "legal regulatory", "litigation", "lawsuit", "regulatory"))
    is_mda = _has_any(
        text,
        (
            "mda",
            "management's discussion",
            "management s discussion",
            "management discussion",
            "item 7 management",
            "item 2 management",
        ),
    )
    is_earnings_release = _has_any(text, ("earnings release", "investor material"))
    is_results_item = _has_any(text, ("item 2.02", "results of operations", "financial condition"))
    is_item_901 = _has_any(text, ("item 9.01", "financial statements and exhibits"))
    is_financial_statement = _has_any(text, ("financial statements", "consolidated financial statements", "statement of income"))
    has_energy_theme = _has_any(text, ("energy", "oil", "crude", "commodity", "wti"))
    is_nonfinancial_press = is_company_press and _has_any(
        title,
        (
            "health records",
            "news app",
            "environmental",
            "impact accelerator",
            "privacy",
            "education",
        ),
    )

    wants_risk = bool(profile.intersection({"company_risk", "legal_regulatory", "supply_chain"}))
    wants_earnings = "earnings_guidance" in profile
    wants_energy = "energy" in profile
    wants_margin = "margin_pressure" in profile

    return {
        "risk_factor_section_match": 1.0 if wants_risk and is_risk_factor else 0.0,
        "legal_section_match": 1.0 if wants_risk and is_legal else 0.0,
        "mda_section_match": 1.0 if (wants_risk or wants_energy or wants_margin) and is_mda else 0.0,
        "earnings_release_match": 1.0 if (wants_earnings or wants_margin or wants_energy) and is_earnings_release else 0.0,
        "results_item_match": 1.0 if (wants_earnings or wants_margin) and is_results_item else 0.0,
        "item_901_wrapper": 1.0 if (wants_earnings or wants_margin or wants_risk) and is_item_901 else 0.0,
        "financial_statement_section": 1.0 if (wants_risk or wants_margin) and is_financial_statement else 0.0,
        "risk_query_earnings_release_bad": 1.0
        if wants_risk and is_earnings_release and not (is_risk_factor or is_legal or is_mda)
        else 0.0,
        "nonfinancial_press_bad": 1.0 if wants_earnings and is_nonfinancial_press else 0.0,
        "energy_theme_match": 1.0 if wants_energy and has_energy_theme else 0.0,
    }


def row_features(row: dict[str, str], query_row: dict[str, str]) -> dict[str, float]:
    source_type = row.get("source_type", "").lower()
    source_scope = query_row.get("source_scope", "")
    expected_ticker = query_row.get("expected_ticker", "").upper()
    matched_tickers = _split_list(row.get("matched_tickers", ""))
    score = float(row.get("score") or 0.0)
    rank = max(1.0, float(row.get("rank") or 1.0))
    signal_strength = float(row.get("signal_strength") or 0.0)
    folder_key = row.get("folder_key", "")

    sec_match = source_scope == "sec_filings" and (source_type.startswith("sec_") or folder_key == "sec_filings")
    macro_match = source_scope == "macro" and source_type.startswith("official_macro")
    company_ir_match = source_scope == "company_ir" and (source_type.startswith("company_") or folder_key == "company_ir")
    field_exact, field_bad = field_match_features(query_row.get("query", ""), row)
    section_features = section_intent_features(query_row.get("query", ""), row)

    features = {
        "base_score": score,
        "signal_strength": signal_strength,
        "rank_prior": 1.0 / rank,
        "expected_ticker_match": 1.0 if expected_ticker and expected_ticker != "MARKET" and expected_ticker in matched_tickers else 0.0,
        "wrong_company": 1.0
        if expected_ticker
        and expected_ticker != "MARKET"
        and matched_tickers
        and expected_ticker not in matched_tickers
        and "MARKET" not in matched_tickers
        else 0.0,
        "source_scope_match": 1.0 if sec_match or macro_match or company_ir_match else 0.0,
        "sec_scope_match": 1.0 if sec_match else 0.0,
        "macro_scope_match": 1.0 if macro_match else 0.0,
        "company_ir_scope_match": 1.0 if company_ir_match else 0.0,
        "field_exact": field_exact,
        "field_bad": field_bad,
    }
    features.update(section_features)
    return features


def normalize_features(items: list[dict[str, Any]]) -> None:
    by_query: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        by_query[item["query_id"]].append(item)

    for query_items in by_query.values():
        max_abs_score = max((abs(item["features"]["base_score"]) for item in query_items), default=0.0) or 1.0
        max_signal = max((abs(item["features"]["signal_strength"]) for item in query_items), default=0.0) or 1.0
        for item in query_items:
            item["features"]["base_score"] = item["features"]["base_score"] / max_abs_score
            item["features"]["signal_strength"] = item["features"]["signal_strength"] / max_signal


def build_items(
    pool_rows: list[dict[str, str]],
    query_rows: dict[str, dict[str, str]],
    qrels: dict[str, dict[str, int]],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for row in pool_rows:
        query_id = row["query_id"]
        if query_id not in query_rows:
            raise ValueError(f"Missing query metadata for query_id={query_id!r}")
        relevance = qrels.get(query_id, {}).get(row["doc_id"])
        items.append(
            {
                "query_id": query_id,
                "doc_id": row["doc_id"],
                "original_rank": int(row.get("rank") or 0),
                "original_score": float(row.get("score") or 0.0),
                "relevance": relevance,
                "pool_row": dict(row),
                "features": row_features(row, query_rows[query_id]),
            }
        )
    normalize_features(items)
    return items


def score_item(item: dict[str, Any], weights: dict[str, float]) -> float:
    features = item["features"]
    return sum(float(features.get(name, 0.0)) * float(weights.get(name, 0.0)) for name in FEATURE_NAMES)


def ranked_items(items: list[dict[str, Any]], weights: dict[str, float]) -> list[dict[str, Any]]:
    return sorted(
        items,
        key=lambda item: (
            score_item(item, weights),
            -int(item.get("original_rank", 0) or 0),
            str(item.get("doc_id", "")),
        ),
        reverse=True,
    )


def dcg(relevances: list[int], k: int) -> float:
    return sum((2**rel - 1) / math.log2(index + 2) for index, rel in enumerate(relevances[:k]))


def ndcg_at_k(relevances: list[int], ideal_relevances: list[int], k: int) -> float:
    ideal = dcg(sorted(ideal_relevances, reverse=True), k)
    return dcg(relevances, k) / ideal if ideal > 0 else 0.0


def precision_at_k(relevances: list[int], k: int) -> float:
    return sum(1 for value in relevances[:k] if value > 0) / k if k else 0.0


def reciprocal_rank(relevances: list[int]) -> float:
    for index, relevance in enumerate(relevances, start=1):
        if relevance > 0:
            return 1.0 / index
    return 0.0


def query_metrics(
    items_by_query: dict[str, list[dict[str, Any]]],
    qrels: dict[str, dict[str, int]],
    weights: dict[str, float],
    query_ids: list[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for query_id in query_ids:
        ranked = ranked_items(items_by_query.get(query_id, []), weights)
        relevances = [int(qrels.get(query_id, {}).get(item["doc_id"], 0)) for item in ranked]
        ideal = list(qrels.get(query_id, {}).values())
        rows.append(
            {
                "query_id": query_id,
                "precision_at_10": precision_at_k(relevances, 10),
                "ndcg_at_10": ndcg_at_k(relevances, ideal, 10),
                "mrr": reciprocal_rank(relevances),
            }
        )
    return rows


def mean_metric(rows: list[dict[str, Any]], metric: str) -> float:
    values = [float(row[metric]) for row in rows]
    return sum(values) / len(values) if values else 0.0


def objective(
    items_by_query: dict[str, list[dict[str, Any]]],
    qrels: dict[str, dict[str, int]],
    weights: dict[str, float],
    query_ids: list[str],
    *,
    feature_names: Optional[list[str]] = None,
    precision_weight: float = 0.0,
    regularization_strength: float = 0.0,
) -> float:
    rows = query_metrics(items_by_query, qrels, weights, query_ids)
    relevance_score = mean_metric(rows, "ndcg_at_10") + precision_weight * mean_metric(rows, "precision_at_10")
    if regularization_strength <= 0:
        return relevance_score
    active_features = feature_names or BASE_FEATURE_NAMES
    penalty = sum((float(weights.get(name, 0.0)) - float(INITIAL_WEIGHTS.get(name, 0.0))) ** 2 for name in active_features)
    return relevance_score - regularization_strength * penalty / len(active_features)


def fit_weights(
    items_by_query: dict[str, list[dict[str, Any]]],
    qrels: dict[str, dict[str, int]],
    query_ids: list[str],
    *,
    max_passes: int,
    feature_names: Optional[list[str]] = None,
    precision_weight: float = 0.0,
    regularization_strength: float = 0.0,
) -> dict[str, float]:
    active_features = feature_names or BASE_FEATURE_NAMES
    weights = {name: INITIAL_WEIGHTS[name] for name in active_features}
    for _ in range(max_passes):
        changed = False
        for feature_name in active_features:
            best_value = weights[feature_name]
            best_score = objective(
                items_by_query,
                qrels,
                weights,
                query_ids,
                feature_names=active_features,
                precision_weight=precision_weight,
                regularization_strength=regularization_strength,
            )
            for candidate in GRID_VALUES:
                trial = dict(weights)
                trial[feature_name] = candidate
                trial_score = objective(
                    items_by_query,
                    qrels,
                    trial,
                    query_ids,
                    feature_names=active_features,
                    precision_weight=precision_weight,
                    regularization_strength=regularization_strength,
                )
                if trial_score > best_score + 1e-12:
                    best_score = trial_score
                    best_value = candidate
            if best_value != weights[feature_name]:
                weights[feature_name] = best_value
                changed = True
        if not changed:
            break
    return weights


def fold_query_ids(query_ids: list[str], folds: int) -> list[list[str]]:
    folds = max(2, min(folds, len(query_ids)))
    buckets = [[] for _ in range(folds)]
    for index, query_id in enumerate(sorted(query_ids)):
        buckets[index % folds].append(query_id)
    return [bucket for bucket in buckets if bucket]


def cross_validate(
    items_by_query: dict[str, list[dict[str, Any]]],
    qrels: dict[str, dict[str, int]],
    *,
    folds: int,
    max_passes: int,
    feature_names: Optional[list[str]] = None,
    precision_weight: float = 0.0,
    regularization_strength: float = 0.0,
) -> list[dict[str, Any]]:
    query_ids = sorted(items_by_query)
    buckets = fold_query_ids(query_ids, folds)
    rows: list[dict[str, Any]] = []
    for fold_index, test_ids in enumerate(buckets, start=1):
        test_set = set(test_ids)
        train_ids = [query_id for query_id in query_ids if query_id not in test_set]
        weights = fit_weights(
            items_by_query,
            qrels,
            train_ids,
            max_passes=max_passes,
            feature_names=feature_names,
            precision_weight=precision_weight,
            regularization_strength=regularization_strength,
        )
        train_rows = query_metrics(items_by_query, qrels, weights, train_ids)
        test_rows = query_metrics(items_by_query, qrels, weights, test_ids)
        rows.append(
            {
                "fold": fold_index,
                "train_query_count": len(train_ids),
                "test_query_count": len(test_ids),
                "train_ndcg_at_10": mean_metric(train_rows, "ndcg_at_10"),
                "test_precision_at_10": mean_metric(test_rows, "precision_at_10"),
            "test_ndcg_at_10": mean_metric(test_rows, "ndcg_at_10"),
            "test_mrr": mean_metric(test_rows, "mrr"),
            "precision_weight": precision_weight,
            "regularization_strength": regularization_strength,
            "test_query_ids": "|".join(test_ids),
            "weights_json": json.dumps(weights, sort_keys=True),
        }
        )
    return rows


def write_csv(path: Union[str, Path], rows: list[dict[str, Any]], fieldnames: Optional[list[str]] = None) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    if not fieldnames:
        fieldnames = list(rows[0].keys()) if rows else ["empty"]
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_run(
    path: Union[str, Path],
    items_by_query: dict[str, list[dict[str, Any]]],
    weights: dict[str, float],
    *,
    method: str,
) -> None:
    rows: list[dict[str, Any]] = []
    for query_id in sorted(items_by_query):
        for rank, item in enumerate(ranked_items(items_by_query[query_id], weights), start=1):
            rows.append(
                {
                    "query_id": query_id,
                    "doc_id": item["doc_id"],
                    "rank": rank,
                    "score": round(score_item(item, weights), 9),
                    "method": method,
                }
            )
    write_csv(path, rows, ["query_id", "doc_id", "rank", "score", "method"])


def write_ranked_pool(
    path: Union[str, Path],
    items_by_query: dict[str, list[dict[str, Any]]],
    weights: dict[str, float],
) -> None:
    rows: list[dict[str, Any]] = []
    fieldnames: list[str] = []
    for query_id in sorted(items_by_query):
        for rank, item in enumerate(ranked_items(items_by_query[query_id], weights), start=1):
            row = dict(item["pool_row"])
            row["rank"] = str(rank)
            row["score"] = str(round(score_item(item, weights), 9))
            rows.append(row)
            for field in row:
                if field not in fieldnames:
                    fieldnames.append(field)
    write_csv(path, rows, fieldnames)


def write_weights(
    path: Union[str, Path],
    weights: dict[str, float],
    *,
    args: argparse.Namespace,
    cv_rows: list[dict[str, Any]],
    final_summary: dict[str, Any],
    feature_names: list[str],
) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "method": args.method,
        "feature_names": feature_names,
        "available_feature_names": FEATURE_NAMES,
        "weights": weights,
        "inputs": {
            "pool": args.pool,
            "queries": args.queries,
            "qrels": args.qrels,
        },
        "training": {
            "folds": args.folds,
            "max_passes": args.max_passes,
            "objective": "mean_ndcg_at_10_by_query + precision_weight * mean_precision_at_10 - regularization",
            "precision_weight": args.precision_weight,
            "regularization_strength": args.regularization_strength,
            "include_section_features": args.include_section_features,
            "label_warning": "assistant-reviewed qrels are for development; replace with independent human labels before final claims",
        },
        "cross_validation": {
            "mean_test_precision_at_10": mean_metric(cv_rows, "test_precision_at_10"),
            "mean_test_ndcg_at_10": mean_metric(cv_rows, "test_ndcg_at_10"),
            "mean_test_mrr": mean_metric(cv_rows, "test_mrr"),
        },
        "final_in_sample_summary": final_summary,
    }
    output.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Calibrate interpretable web-search reranker weights.")
    parser.add_argument("--pool", required=True, help="Search quality pool CSV.")
    parser.add_argument("--queries", required=True, help="Search quality query metadata CSV.")
    parser.add_argument("--qrels", required=True, help="Judged qrels CSV.")
    parser.add_argument("--run-output", required=True, help="Output reranked run CSV.")
    parser.add_argument("--pool-output", default="", help="Optional reranked pool CSV with original metadata.")
    parser.add_argument("--weights-output", required=True, help="Output learned weights JSON.")
    parser.add_argument("--cv-output", required=True, help="Output query-fold cross-validation CSV.")
    parser.add_argument("--summary-output", required=True, help="Output calibration summary CSV.")
    parser.add_argument("--method", default="web_search_calibrated_v5", help="Method label for the reranked run.")
    parser.add_argument("--folds", type=int, default=5, help="Number of query-level CV folds.")
    parser.add_argument("--max-passes", type=int, default=4, help="Coordinate-ascent passes.")
    parser.add_argument("--precision-weight", type=float, default=0.0, help="Weight for mean Precision@10 in the training objective.")
    parser.add_argument(
        "--regularization-strength",
        type=float,
        default=0.0,
        help="L2 penalty against initial interpretable weights, scaled by feature count.",
    )
    parser.add_argument(
        "--include-section-features",
        action="store_true",
        help="Enable finer document-section intent features. Keep off for production unless CV and coverage improve.",
    )
    args = parser.parse_args(argv)

    qrels = load_qrels(args.qrels)
    query_rows = load_queries(args.queries)
    items = build_items(load_csv(args.pool), query_rows, qrels)
    items_by_query: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        items_by_query[item["query_id"]].append(item)

    query_ids = sorted(items_by_query)
    feature_names = FEATURE_NAMES if args.include_section_features else BASE_FEATURE_NAMES
    cv_rows = cross_validate(
        items_by_query,
        qrels,
        folds=args.folds,
        max_passes=args.max_passes,
        feature_names=feature_names,
        precision_weight=args.precision_weight,
        regularization_strength=args.regularization_strength,
    )
    final_weights = fit_weights(
        items_by_query,
        qrels,
        query_ids,
        max_passes=args.max_passes,
        feature_names=feature_names,
        precision_weight=args.precision_weight,
        regularization_strength=args.regularization_strength,
    )
    final_rows = query_metrics(items_by_query, qrels, final_weights, query_ids)
    final_summary = {
        "query_count": len(query_ids),
        "precision_at_10": mean_metric(final_rows, "precision_at_10"),
        "ndcg_at_10": mean_metric(final_rows, "ndcg_at_10"),
        "mrr": mean_metric(final_rows, "mrr"),
    }

    write_csv(args.cv_output, cv_rows)
    write_run(args.run_output, items_by_query, final_weights, method=args.method)
    if args.pool_output:
        write_ranked_pool(args.pool_output, items_by_query, final_weights)
    write_csv(args.summary_output, [{"method": args.method, **final_summary}], ["method", "query_count", "precision_at_10", "ndcg_at_10", "mrr"])
    write_weights(args.weights_output, final_weights, args=args, cv_rows=cv_rows, final_summary=final_summary, feature_names=feature_names)

    print(f"query_count={len(query_ids)}")
    print(f"cv_mean_ndcg_at_10={mean_metric(cv_rows, 'test_ndcg_at_10'):.6f}")
    print(f"final_ndcg_at_10={final_summary['ndcg_at_10']:.6f}")
    print(f"run_output={args.run_output}")
    print(f"weights_output={args.weights_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
