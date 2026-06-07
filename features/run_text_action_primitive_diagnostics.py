"""Diagnose text features against teacher actions and behavior primitives.

This script is a pre-PPO screen. It does not train an RL policy. It joins the
merge-ready text panel with prior base-macro teacher outputs and asks whether
text features explain action/behavior regimes beyond a small base-macro
baseline.
"""

from __future__ import annotations

import argparse
import csv
import json
import warnings
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from pandas.errors import PerformanceWarning
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score,
    balanced_accuracy_score,
    f1_score,
    roc_auc_score,
)
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler


warnings.filterwarnings("ignore", category=PerformanceWarning)
warnings.filterwarnings("ignore", message="y_pred contains classes not in y_true")

TRAIN_END = "2021-10-01"
TEST_END = "2023-03-01"

BASE_MACRO_FEATURES = [
    "daily_return",
    "atr_rel",
    "macd",
    "rsi_30",
    "cci_30",
    "dx_30",
    "volume_ratio",
    "obv_pct_change",
    "turbulence",
    "10Y_Yield",
    "VIX",
    "SP500_Trend",
]

PROVENANCE_LIKE_COLUMNS = {
    "portfolio_text_has_evidence",
    "portfolio_text_doc_count",
    "portfolio_text_unique_doc_count",
    "stock_text_has_evidence",
    "stock_text_doc_count",
    "stock_text_unique_doc_count",
}

RETRIEVAL_PROXY_COLUMNS = {
    "portfolio_text_avg_age_days",
    "portfolio_text_avg_final_score",
    "stock_text_avg_age_days",
    "stock_text_avg_final_score",
}

PPO_TICKERS = [
    "AAPL",
    "AMGN",
    "AMZN",
    "AXP",
    "BA",
    "CAT",
    "CRM",
    "CSCO",
    "CVX",
    "DIS",
    "GS",
    "HD",
    "HON",
    "IBM",
    "INTC",
    "JNJ",
    "JPM",
    "KO",
    "MCD",
    "MMM",
    "MRK",
    "MSFT",
    "NKE",
    "PG",
    "TRV",
    "UNH",
    "V",
    "VZ",
    "WMT",
]


def _json_default(value: Any) -> Any:
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    return str(value)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=_json_default) + "\n", encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        keys: list[str] = []
        for row in rows:
            for key in row:
                if key not in keys:
                    keys.append(key)
        fieldnames = keys
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _write_txt(path: Path, values: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(values) + ("\n" if values else ""), encoding="utf-8")


def text_feature_columns(df: pd.DataFrame) -> list[str]:
    return sorted(
        [
            column
            for column in df.columns
            if column.startswith(("stock_text_", "portfolio_text_", "stock_signal_", "portfolio_signal_"))
        ]
    )


def _is_provenance_like(feature: str) -> bool:
    source = source_feature(feature)
    return source in PROVENANCE_LIKE_COLUMNS or source.endswith("_has_evidence")


def _is_recommendation_candidate(feature: str) -> bool:
    source = source_feature(feature)
    if _is_provenance_like(source):
        return False
    if source in RETRIEVAL_PROXY_COLUMNS:
        return False
    return True


def source_feature(feature: str) -> str:
    for prefix in ("stock_mean__", "stock_max__"):
        if feature.startswith(prefix):
            return feature.removeprefix(prefix)
    return feature


def _safe_auc(y_true: pd.Series, score: pd.Series) -> float | None:
    mask = y_true.notna() & score.notna()
    if mask.sum() < 30 or y_true.loc[mask].nunique() < 2:
        return None
    try:
        return float(roc_auc_score(y_true.loc[mask].astype(int), score.loc[mask]))
    except ValueError:
        return None


def _safe_ap(y_true: pd.Series, score: pd.Series) -> float | None:
    mask = y_true.notna() & score.notna()
    if mask.sum() < 30 or y_true.loc[mask].nunique() < 2:
        return None
    return float(average_precision_score(y_true.loc[mask].astype(int), score.loc[mask]))


def split_masks(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    dates = pd.to_datetime(df["date"])
    train = dates < pd.Timestamp(TRAIN_END)
    test = (dates >= pd.Timestamp(TRAIN_END)) & (dates < pd.Timestamp(TEST_END))
    return train, test


def _numeric_nonconstant_features(df: pd.DataFrame, features: list[str], mask: pd.Series) -> list[str]:
    selected: list[str] = []
    for feature in features:
        if feature not in df.columns or not pd.api.types.is_numeric_dtype(df[feature]):
            continue
        series = df.loc[mask, feature]
        if series.notna().sum() < 30:
            continue
        if series.nunique(dropna=True) <= 1:
            continue
        selected.append(feature)
    return selected


def date_level_features(panel: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, list[str]]]:
    panel = panel.copy()
    panel["date"] = pd.to_datetime(panel["date"])
    text_features = text_feature_columns(panel)
    stock_text = [feature for feature in text_features if feature.startswith("stock_")]
    portfolio_text = [feature for feature in text_features if feature.startswith("portfolio_")]

    agg: dict[str, tuple[str, str]] = {}
    for feature in BASE_MACRO_FEATURES:
        if feature in {"10Y_Yield", "VIX", "SP500_Trend", "turbulence"}:
            agg[f"base_mean__{feature}"] = (feature, "first")
        else:
            agg[f"base_mean__{feature}"] = (feature, "mean")
    for feature in portfolio_text:
        agg[feature] = (feature, "first")
    for feature in stock_text:
        agg[f"stock_mean__{feature}"] = (feature, "mean")
        agg[f"stock_max__{feature}"] = (feature, "max")

    daily = panel.groupby("date", as_index=False).agg(**agg).sort_values("date").reset_index(drop=True)
    groups = {
        "base_date": [column for column in daily.columns if column.startswith("base_mean__")],
        "portfolio_text": portfolio_text,
        "stock_text_aggregate": [
            column for column in daily.columns if column.startswith(("stock_mean__", "stock_max__"))
        ],
    }
    groups["all_text_date"] = groups["portfolio_text"] + groups["stock_text_aggregate"]
    groups["base_date_plus_all_text"] = groups["base_date"] + groups["all_text_date"]
    return daily, groups


def load_stock_action_frame(panel: pd.DataFrame, actions_path: Path) -> pd.DataFrame:
    actions = pd.read_csv(actions_path)
    actions["date"] = pd.to_datetime(actions["date"])
    tickers = [ticker for ticker in PPO_TICKERS if ticker in actions.columns]
    id_columns = [
        "run_key",
        "feature_set",
        "feature_family",
        "fold_id",
        "seed",
        "split_name",
        "action_row_id",
        "action_step",
        "date",
    ]
    id_columns = [column for column in id_columns if column in actions.columns]
    long_actions = actions.melt(
        id_vars=id_columns,
        value_vars=tickers,
        var_name="tic",
        value_name="teacher_action",
    )
    long_actions = long_actions.sort_values(["run_key", "tic", "date"]).reset_index(drop=True)
    long_actions["prev_teacher_action"] = long_actions.groupby(["run_key", "tic"])["teacher_action"].shift(1)
    long_actions["stock_action_active_flag"] = (long_actions["teacher_action"].abs() > 1e-9).astype(int)
    long_actions["stock_action_full_buy_flag"] = (long_actions["teacher_action"] >= 100).astype(int)
    long_actions["stock_action_sell_flag"] = (long_actions["teacher_action"] < 0).astype(int)
    long_actions["stock_action_changed_flag"] = (
        long_actions["prev_teacher_action"].notna()
        & ((long_actions["teacher_action"] - long_actions["prev_teacher_action"]).abs() > 1e-9)
    ).astype(int)

    features = ["date", "tic"] + BASE_MACRO_FEATURES + text_feature_columns(panel)
    merged = long_actions.merge(panel[features], on=["date", "tic"], how="left", validate="many_to_one")
    return merged


def load_action_code_frame(date_features: pd.DataFrame, codes_path: Path) -> pd.DataFrame:
    codes = pd.read_csv(codes_path)
    codes["date"] = pd.to_datetime(codes["date"])
    codes = codes.sort_values(["run_key", "date"]).reset_index(drop=True)
    codes["prev_simple_action_code"] = codes.groupby("run_key")["simple_action_code"].shift(1)
    codes["action_nonflat_flag"] = (codes["simple_action_code"] != "flat__flat__flat").astype(int)
    codes["action_code_changed_flag"] = (
        codes["prev_simple_action_code"].notna()
        & (codes["simple_action_code"] != codes["prev_simple_action_code"])
    ).astype(int)
    codes["action_l1_high_flag"] = (
        codes["action_l1"] >= codes.loc[pd.to_datetime(codes["date"]) < pd.Timestamp(TRAIN_END), "action_l1"].quantile(0.75)
    ).astype(int)
    return codes.merge(date_features, on="date", how="left", validate="many_to_one")


def load_behavior_frame(date_features: pd.DataFrame, behavior_path: Path) -> pd.DataFrame:
    behavior = pd.read_csv(behavior_path)
    behavior["date"] = pd.to_datetime(behavior["date"])
    behavior = behavior.sort_values(["run_key", "date"]).reset_index(drop=True)
    behavior["prev_primitive_id"] = behavior.groupby("run_key")["primitive_id"].shift(1)
    behavior["primitive_changed_flag"] = (
        behavior["prev_primitive_id"].notna() & (behavior["primitive_id"] != behavior["prev_primitive_id"])
    ).astype(int)
    behavior["primitive_00_flag"] = (behavior["primitive_id"] == "primitive_00").astype(int)
    behavior["primitive_04_flag"] = (behavior["primitive_id"] == "primitive_04").astype(int)
    behavior["primitive_05_flag"] = (behavior["primitive_id"] == "primitive_05").astype(int)
    behavior["bad_primitive_flag"] = behavior["primitive_id"].isin(["primitive_03", "primitive_04", "primitive_05"]).astype(int)
    if "turnover_wmean" in behavior.columns:
        threshold = behavior.loc[
            pd.to_datetime(behavior["date"]) < pd.Timestamp(TRAIN_END), "turnover_wmean"
        ].quantile(0.75)
        behavior["high_turnover_flag"] = (behavior["turnover_wmean"] >= threshold).astype(int)
    return behavior.merge(date_features, on="date", how="left", validate="many_to_one")


def _fit_logistic(
    x_train: pd.DataFrame,
    y_train: pd.Series,
    x_test: pd.DataFrame,
    target_kind: str,
) -> tuple[np.ndarray, np.ndarray | None, Any]:
    if target_kind == "binary":
        model = make_pipeline(
            SimpleImputer(strategy="median"),
            StandardScaler(),
            LogisticRegression(max_iter=1000, class_weight="balanced", solver="lbfgs"),
        )
    else:
        model = make_pipeline(
            SimpleImputer(strategy="median"),
            StandardScaler(),
            LogisticRegression(max_iter=1500, class_weight="balanced", solver="lbfgs"),
        )
    model.fit(x_train, y_train)
    pred = model.predict(x_test)
    proba = model.predict_proba(x_test) if hasattr(model, "predict_proba") else None
    return pred, proba, model


def evaluate_models(
    df: pd.DataFrame,
    dataset_name: str,
    targets: dict[str, str],
    feature_sets: dict[str, list[str]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    metric_rows: list[dict[str, Any]] = []
    coef_rows: list[dict[str, Any]] = []
    train_mask, test_mask = split_masks(df)

    for target, target_kind in targets.items():
        target_mask = df[target].notna()
        train = train_mask & target_mask
        test = test_mask & target_mask
        if int(train.sum()) < 100 or int(test.sum()) < 30:
            continue
        y_train = df.loc[train, target]
        y_test = df.loc[test, target]
        if y_train.nunique(dropna=True) < 2 or y_test.nunique(dropna=True) < 2:
            continue

        majority_label = y_train.value_counts().idxmax()
        majority_pred = pd.Series(majority_label, index=y_test.index)
        metric_rows.append(
            {
                "dataset": dataset_name,
                "variant": "majority_baseline",
                "target": target,
                "target_kind": target_kind,
                "feature_count": 0,
                "train_rows": int(train.sum()),
                "test_rows": int(test.sum()),
                "test_positive_rate": float(y_test.mean()) if target_kind == "binary" else None,
                "balanced_accuracy": float(balanced_accuracy_score(y_test, majority_pred)),
                "macro_f1": float(f1_score(y_test, majority_pred, average="macro", zero_division=0)),
                "roc_auc": None,
                "average_precision": None,
            }
        )

        previous_column = {
            "simple_action_code": "prev_simple_action_code",
            "primitive_id": "prev_primitive_id",
            "action_code_changed_flag": "prev_simple_action_code",
            "primitive_changed_flag": "prev_primitive_id",
        }.get(target)
        if previous_column and previous_column in df.columns:
            prev_test = df.loc[test, previous_column]
            if target_kind == "binary" and target.endswith("_changed_flag"):
                prev_pred = pd.Series(0, index=y_test.index)
            else:
                prev_pred = prev_test.fillna(majority_label)
            if prev_pred.nunique(dropna=True) > 0:
                metric_rows.append(
                    {
                        "dataset": dataset_name,
                        "variant": "persistence_baseline",
                        "target": target,
                        "target_kind": target_kind,
                        "feature_count": 0,
                        "train_rows": int(train.sum()),
                        "test_rows": int(test.sum()),
                        "test_positive_rate": float(y_test.mean()) if target_kind == "binary" else None,
                        "balanced_accuracy": float(balanced_accuracy_score(y_test, prev_pred)),
                        "macro_f1": float(f1_score(y_test, prev_pred, average="macro", zero_division=0)),
                        "roc_auc": None,
                        "average_precision": None,
                    }
                )

        for variant, requested_features in feature_sets.items():
            features = _numeric_nonconstant_features(df, requested_features, train)
            if not features:
                continue
            x_train = df.loc[train, features]
            x_test = df.loc[test, features]
            pred, proba, model = _fit_logistic(x_train, y_train, x_test, target_kind)
            row = {
                "dataset": dataset_name,
                "variant": variant,
                "target": target,
                "target_kind": target_kind,
                "feature_count": len(features),
                "train_rows": int(train.sum()),
                "test_rows": int(test.sum()),
                "test_positive_rate": float(y_test.mean()) if target_kind == "binary" else None,
                "balanced_accuracy": float(balanced_accuracy_score(y_test, pred)),
                "macro_f1": float(f1_score(y_test, pred, average="macro", zero_division=0)),
                "roc_auc": None,
                "average_precision": None,
            }
            if target_kind == "binary" and proba is not None:
                row["roc_auc"] = float(roc_auc_score(y_test.astype(int), proba[:, 1]))
                row["average_precision"] = float(average_precision_score(y_test.astype(int), proba[:, 1]))
            metric_rows.append(row)

            estimator = model.named_steps["logisticregression"]
            coefs = estimator.coef_
            if coefs.ndim == 2 and coefs.shape[0] > 1:
                importance = np.abs(coefs).mean(axis=0)
            else:
                importance = np.abs(coefs.reshape(-1))
            for feature, value in zip(features, importance):
                if source_feature(feature) in text_feature_columns(df) or feature.startswith(("stock_mean__", "stock_max__")):
                    coef_rows.append(
                        {
                            "dataset": dataset_name,
                            "variant": variant,
                            "target": target,
                            "feature": feature,
                            "source_feature": source_feature(feature),
                            "is_provenance_like": int(_is_provenance_like(feature)),
                            "mean_abs_standardized_coefficient": float(value),
                        }
                    )
    return metric_rows, coef_rows


def model_delta_rows(metric_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    metrics = pd.DataFrame(metric_rows)
    rows: list[dict[str, Any]] = []
    if metrics.empty:
        return rows
    for (dataset, target), group in metrics.groupby(["dataset", "target"]):
        base_candidates = ["base_macro", "base_date"]
        base = pd.DataFrame()
        for candidate in base_candidates:
            base = group[group["variant"] == candidate]
            if not base.empty:
                break
        if base.empty:
            continue
        base_row = base.iloc[0]
        for _, row in group.iterrows():
            rows.append(
                {
                    "dataset": dataset,
                    "variant": row["variant"],
                    "target": target,
                    "delta_balanced_accuracy_vs_base": float(row["balanced_accuracy"] - base_row["balanced_accuracy"]),
                    "delta_macro_f1_vs_base": float(row["macro_f1"] - base_row["macro_f1"]),
                    "delta_roc_auc_vs_base": (
                        None
                        if pd.isna(row.get("roc_auc")) or pd.isna(base_row.get("roc_auc"))
                        else float(row["roc_auc"] - base_row["roc_auc"])
                    ),
                    "delta_average_precision_vs_base": (
                        None
                        if pd.isna(row.get("average_precision")) or pd.isna(base_row.get("average_precision"))
                        else float(row["average_precision"] - base_row["average_precision"])
                    ),
                }
            )
    return rows


def univariate_text_edges(
    df: pd.DataFrame,
    dataset_name: str,
    targets: list[str],
    features: list[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    train_mask, test_mask = split_masks(df)
    for target in targets:
        if target not in df.columns:
            continue
        y = df[target]
        if y.dropna().nunique() < 2:
            continue
        for feature in features:
            if feature not in df.columns or not pd.api.types.is_numeric_dtype(df[feature]):
                continue
            if df.loc[train_mask, feature].nunique(dropna=True) <= 1:
                continue
            train_auc = _safe_auc(y.loc[train_mask], df.loc[train_mask, feature])
            test_auc = _safe_auc(y.loc[test_mask], df.loc[test_mask, feature])
            train_ap = _safe_ap(y.loc[train_mask], df.loc[train_mask, feature])
            test_ap = _safe_ap(y.loc[test_mask], df.loc[test_mask, feature])
            if train_auc is None:
                continue
            rows.append(
                {
                    "dataset": dataset_name,
                    "target": target,
                    "feature": feature,
                    "source_feature": source_feature(feature),
                    "is_provenance_like": int(_is_provenance_like(feature)),
                    "train_positive_rate": float(y.loc[train_mask].mean()),
                    "test_positive_rate": float(y.loc[test_mask].mean()),
                    "train_auc": train_auc,
                    "test_auc": test_auc,
                    "train_auc_edge": float(abs(train_auc - 0.5)),
                    "test_auc_edge": None if test_auc is None else float(abs(test_auc - 0.5)),
                    "train_average_precision": train_ap,
                    "test_average_precision": test_ap,
                }
            )
    return rows


def target_summary_rows(df: pd.DataFrame, dataset_name: str, targets: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    train_mask, test_mask = split_masks(df)
    for split_name, mask in [("train", train_mask), ("test", test_mask)]:
        for target in targets:
            if target not in df.columns:
                continue
            series = df.loc[mask, target]
            row: dict[str, Any] = {
                "dataset": dataset_name,
                "split": split_name,
                "target": target,
                "rows": int(series.notna().sum()),
                "unique_values": int(series.nunique(dropna=True)),
            }
            if pd.api.types.is_numeric_dtype(series):
                row["mean"] = float(series.mean())
                row["std"] = float(series.std(ddof=0))
            else:
                row["top_value"] = series.value_counts(dropna=False).index[0] if len(series) else None
                row["top_share"] = float(series.value_counts(normalize=True, dropna=False).iloc[0]) if len(series) else None
            rows.append(row)
    return rows


def select_recommended_features(
    univariate_rows: list[dict[str, Any]],
    coef_rows: list[dict[str, Any]],
    limit: int = 14,
) -> list[str]:
    scores: dict[str, dict[str, float]] = {}
    for row in univariate_rows:
        if row.get("is_provenance_like"):
            continue
        feature = str(row["source_feature"])
        if not _is_recommendation_candidate(feature):
            continue
        train_rate = row.get("train_positive_rate")
        test_rate = row.get("test_positive_rate")
        if train_rate is not None and test_rate is not None and abs(float(train_rate) - float(test_rate)) > 0.25:
            continue
        entry = scores.setdefault(feature, {"train_auc_edge": 0.0, "test_auc_edge": 0.0, "coef": 0.0, "hits": 0.0})
        entry["train_auc_edge"] = max(entry["train_auc_edge"], float(row.get("train_auc_edge") or 0.0))
        entry["test_auc_edge"] = max(entry["test_auc_edge"], float(row.get("test_auc_edge") or 0.0))
        entry["hits"] += 1.0
    for row in coef_rows:
        if row.get("is_provenance_like"):
            continue
        feature = str(row["source_feature"])
        if not _is_recommendation_candidate(feature):
            continue
        entry = scores.setdefault(feature, {"train_auc_edge": 0.0, "test_auc_edge": 0.0, "coef": 0.0, "hits": 0.0})
        entry["coef"] = max(entry["coef"], float(row.get("mean_abs_standardized_coefficient") or 0.0))

    ranked: list[tuple[str, float]] = []
    for feature, score in scores.items():
        total = score["train_auc_edge"] + 0.5 * score["test_auc_edge"] + 0.02 * min(score["coef"], 10.0)
        if score["train_auc_edge"] < 0.015 and score["coef"] < 0.05:
            continue
        ranked.append((feature, total))
    ranked.sort(key=lambda item: item[1], reverse=True)

    selected: list[str] = []
    selected_roots: set[str] = set()
    for feature, _score in ranked:
        root = feature.removesuffix("_flag").removesuffix("_count")
        if root in selected_roots:
            continue
        selected.append(feature)
        selected_roots.add(root)
        if len(selected) >= limit:
            break
    return selected


def recommended_feature_rationale_rows(
    recommended_features: list[str],
    univariate_rows: list[dict[str, Any]],
    coef_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    univariate = pd.DataFrame(univariate_rows)
    coefs = pd.DataFrame(coef_rows)
    for feature in recommended_features:
        row: dict[str, Any] = {
            "feature": feature,
            "feature_family": "portfolio" if feature.startswith("portfolio_") else "stock",
            "is_count_or_flag": int(feature.endswith("_count") or feature.endswith("_flag")),
        }
        if not univariate.empty:
            feature_edges = univariate[univariate["source_feature"] == feature].copy()
            if not feature_edges.empty:
                feature_edges = feature_edges[
                    (feature_edges["train_positive_rate"] - feature_edges["test_positive_rate"]).abs() <= 0.25
                ].copy()
            if not feature_edges.empty:
                feature_edges["score"] = feature_edges["train_auc_edge"].fillna(0.0) + feature_edges[
                    "test_auc_edge"
                ].fillna(0.0)
                best_edge = feature_edges.sort_values("score", ascending=False).iloc[0]
                row.update(
                    {
                        "best_univariate_dataset": best_edge["dataset"],
                        "best_univariate_target": best_edge["target"],
                        "best_train_auc": best_edge["train_auc"],
                        "best_test_auc": best_edge["test_auc"],
                        "best_train_auc_edge": best_edge["train_auc_edge"],
                        "best_test_auc_edge": best_edge["test_auc_edge"],
                        "best_target_train_positive_rate": best_edge["train_positive_rate"],
                        "best_target_test_positive_rate": best_edge["test_positive_rate"],
                    }
                )
        if not coefs.empty:
            feature_coefs = coefs[coefs["source_feature"] == feature].copy()
            if not feature_coefs.empty:
                best_coef = feature_coefs.sort_values("mean_abs_standardized_coefficient", ascending=False).iloc[0]
                row.update(
                    {
                        "best_coef_dataset": best_coef["dataset"],
                        "best_coef_variant": best_coef["variant"],
                        "best_coef_target": best_coef["target"],
                        "max_abs_standardized_coefficient": best_coef["mean_abs_standardized_coefficient"],
                    }
                )
        rows.append(row)
    return rows


def _markdown_table(rows: list[dict[str, Any]], columns: list[str], limit: int = 20) -> str:
    def fmt(value: Any) -> str:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return ""
        if isinstance(value, float):
            return f"{value:.4f}"
        return str(value)

    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join(["---"] * len(columns)) + " |",
    ]
    for row in rows[:limit]:
        lines.append("| " + " | ".join(fmt(row.get(column)) for column in columns) + " |")
    return "\n".join(lines)


def write_findings(
    path: Path,
    summary: dict[str, Any],
    metric_rows: list[dict[str, Any]],
    delta_rows: list[dict[str, Any]],
    recommended_features: list[str],
    rationale_rows: list[dict[str, Any]],
) -> None:
    metrics = pd.DataFrame(metric_rows)
    deltas = pd.DataFrame(delta_rows)
    top_metric_rows: list[dict[str, Any]] = []
    if not metrics.empty:
        keep_variants = {
            "base_macro",
            "base_date",
            "base_macro_plus_all_text",
            "base_date_plus_all_text",
            "all_text_date",
            "stock_text_only",
            "portfolio_text",
            "stock_text_aggregate",
            "persistence_baseline",
        }
        selected = metrics[metrics["variant"].isin(keep_variants)].copy()
        selected = selected.sort_values(["dataset", "target", "variant"])
        top_metric_rows = selected.to_dict("records")

    top_delta_rows: list[dict[str, Any]] = []
    if not deltas.empty:
        selected_delta = deltas[deltas["variant"].str.contains("text", na=False)].copy()
        selected_delta["sort_key"] = selected_delta["delta_roc_auc_vs_base"].fillna(
            selected_delta["delta_balanced_accuracy_vs_base"]
        )
        selected_delta = selected_delta.sort_values("sort_key", ascending=False)
        top_delta_rows = selected_delta.drop(columns=["sort_key"]).to_dict("records")

    content = [
        "# Action And Primitive Text Diagnostics",
        "",
        f"- stock action rows: `{summary['stock_action_rows']}`",
        f"- action-code rows: `{summary['action_code_rows']}`",
        f"- behavior primitive rows: `{summary['behavior_rows']}`",
        f"- split: train `< {TRAIN_END}`, test `{TRAIN_END}` to `< {TEST_END}`",
        f"- recommended text features: `{len(recommended_features)}`",
        "",
        "## Recommended Features",
        "",
        "\n".join(f"- `{feature}`" for feature in recommended_features),
        "",
        "## Recommended Feature Rationale",
        "",
        _markdown_table(
            rationale_rows,
            [
                "feature",
                "best_univariate_target",
                "best_train_auc_edge",
                "best_test_auc_edge",
                "best_coef_target",
                "max_abs_standardized_coefficient",
            ],
            limit=30,
        ),
        "",
        "## Metric Snapshot",
        "",
        _markdown_table(
            top_metric_rows,
            [
                "dataset",
                "target",
                "variant",
                "balanced_accuracy",
                "macro_f1",
                "roc_auc",
                "average_precision",
            ],
            limit=80,
        ),
        "",
        "## Best Text Deltas Vs Base",
        "",
        _markdown_table(
            top_delta_rows,
            [
                "dataset",
                "target",
                "variant",
                "delta_balanced_accuracy_vs_base",
                "delta_macro_f1_vs_base",
                "delta_roc_auc_vs_base",
                "delta_average_precision_vs_base",
            ],
            limit=40,
        ),
        "",
        "Interpretation: this is a cheap screening pass, not a PPO result. A text",
        "feature is useful here if it explains action/behavior regimes or improves",
        "simple held-out classification over base macro features.",
        "",
    ]
    path.write_text("\n".join(content), encoding="utf-8")


def run_diagnostics(
    panel_path: Path,
    actions_path: Path,
    codes_path: Path,
    behavior_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    panel = pd.read_csv(panel_path)
    panel["date"] = pd.to_datetime(panel["date"])
    text_features = text_feature_columns(panel)
    portfolio_text = [feature for feature in text_features if feature.startswith("portfolio_")]
    stock_text = [feature for feature in text_features if feature.startswith("stock_")]

    date_features, date_groups = date_level_features(panel)
    stock_frame = load_stock_action_frame(panel, actions_path)
    action_code_frame = load_action_code_frame(date_features, codes_path)
    behavior_frame = load_behavior_frame(date_features, behavior_path)

    stock_feature_sets = {
        "base_macro": BASE_MACRO_FEATURES,
        "portfolio_text_only": portfolio_text,
        "stock_text_only": stock_text,
        "base_macro_plus_portfolio_text": BASE_MACRO_FEATURES + portfolio_text,
        "base_macro_plus_stock_text": BASE_MACRO_FEATURES + stock_text,
        "base_macro_plus_all_text": BASE_MACRO_FEATURES + text_features,
    }
    date_feature_sets = {
        "base_date": date_groups["base_date"],
        "portfolio_text": date_groups["portfolio_text"],
        "stock_text_aggregate": date_groups["stock_text_aggregate"],
        "all_text_date": date_groups["all_text_date"],
        "base_date_plus_all_text": date_groups["base_date_plus_all_text"],
    }

    stock_targets = {
        "stock_action_active_flag": "binary",
        "stock_action_full_buy_flag": "binary",
        "stock_action_sell_flag": "binary",
        "stock_action_changed_flag": "binary",
    }
    action_code_targets = {
        "action_nonflat_flag": "binary",
        "action_code_changed_flag": "binary",
        "action_l1_high_flag": "binary",
        "direction_code": "multiclass",
        "simple_action_code": "multiclass",
    }
    behavior_targets = {
        "primitive_00_flag": "binary",
        "primitive_04_flag": "binary",
        "primitive_05_flag": "binary",
        "bad_primitive_flag": "binary",
        "primitive_changed_flag": "binary",
        "primitive_id": "multiclass",
    }

    metric_rows: list[dict[str, Any]] = []
    coef_rows: list[dict[str, Any]] = []
    for frame, dataset_name, targets, feature_sets in [
        (stock_frame, "stock_teacher_actions", stock_targets, stock_feature_sets),
        (action_code_frame, "portfolio_action_codes", action_code_targets, date_feature_sets),
        (behavior_frame, "behavior_primitives", behavior_targets, date_feature_sets),
    ]:
        metrics, coefs = evaluate_models(frame, dataset_name, targets, feature_sets)
        metric_rows.extend(metrics)
        coef_rows.extend(coefs)

    univariate_rows: list[dict[str, Any]] = []
    univariate_rows.extend(
        univariate_text_edges(stock_frame, "stock_teacher_actions", list(stock_targets), text_features)
    )
    univariate_rows.extend(
        univariate_text_edges(action_code_frame, "portfolio_action_codes", list(action_code_targets)[:3], date_groups["all_text_date"])
    )
    univariate_rows.extend(
        univariate_text_edges(behavior_frame, "behavior_primitives", list(behavior_targets)[:5], date_groups["all_text_date"])
    )

    delta_rows = model_delta_rows(metric_rows)
    recommended_features = select_recommended_features(univariate_rows, coef_rows)
    lean_plus_action_features = [
        "daily_return",
        "atr_rel",
        "macd",
        "rsi_30",
        "cci_30",
        "dx_30",
        "volume_ratio",
        "obv_pct_change",
        "turbulence",
        "10Y_Yield",
        "VIX",
        "SP500_Trend",
        *recommended_features,
    ]

    target_rows: list[dict[str, Any]] = []
    target_rows.extend(target_summary_rows(stock_frame, "stock_teacher_actions", list(stock_targets)))
    target_rows.extend(target_summary_rows(action_code_frame, "portfolio_action_codes", list(action_code_targets)))
    target_rows.extend(target_summary_rows(behavior_frame, "behavior_primitives", list(behavior_targets)))

    _write_csv(output_dir / "action_primitive_target_summary.csv", target_rows)
    _write_csv(output_dir / "action_primitive_model_metrics.csv", metric_rows)
    _write_csv(output_dir / "action_primitive_model_deltas_vs_base.csv", delta_rows)
    _write_csv(output_dir / "action_primitive_text_coefficients.csv", coef_rows)
    _write_csv(output_dir / "action_primitive_univariate_text_edges.csv", univariate_rows)
    _write_txt(output_dir / "recommended_text_features_action_primitive_v1.txt", recommended_features)
    rationale_rows = recommended_feature_rationale_rows(recommended_features, univariate_rows, coef_rows)
    _write_csv(output_dir / "recommended_text_feature_rationale.csv", rationale_rows)
    _write_json(
        output_dir / "feature_set_base_macro_plus_text_action_primitive_v1.json",
        {
            "name": "base_macro_plus_text_action_primitive_v1",
            "dataset_path": str(panel_path),
            "index_columns": ["date", "tic"],
            "feature_columns": lean_plus_action_features,
            "base_feature_columns": BASE_MACRO_FEATURES,
            "text_feature_columns": recommended_features,
            "feature_count": len(lean_plus_action_features),
            "selection_rule": (
                "Text features screened against teacher stock actions, latent action codes, "
                "and behavior primitives using train-before-2021-10-01 / OOS-after split."
            ),
        },
    )
    _write_txt(output_dir / "feature_set_base_macro_plus_text_action_primitive_v1.txt", lean_plus_action_features)

    summary = {
        "panel_path": str(panel_path),
        "actions_path": str(actions_path),
        "codes_path": str(codes_path),
        "behavior_path": str(behavior_path),
        "output_dir": str(output_dir),
        "train_end": TRAIN_END,
        "test_end": TEST_END,
        "panel_rows": int(len(panel)),
        "text_feature_count": len(text_features),
        "stock_action_rows": int(len(stock_frame)),
        "action_code_rows": int(len(action_code_frame)),
        "behavior_rows": int(len(behavior_frame)),
        "stock_action_merge_missing_rate": float(stock_frame[BASE_MACRO_FEATURES[0]].isna().mean()),
        "action_code_date_merge_missing_rate": float(action_code_frame[date_groups["base_date"][0]].isna().mean()),
        "behavior_date_merge_missing_rate": float(behavior_frame[date_groups["base_date"][0]].isna().mean()),
        "recommended_text_features": recommended_features,
        "feature_count_base_plus_action_primitive_text": len(lean_plus_action_features),
        "outputs": [
            "action_primitive_target_summary.csv",
            "action_primitive_model_metrics.csv",
            "action_primitive_model_deltas_vs_base.csv",
            "action_primitive_text_coefficients.csv",
            "action_primitive_univariate_text_edges.csv",
            "recommended_text_features_action_primitive_v1.txt",
            "recommended_text_feature_rationale.csv",
            "feature_set_base_macro_plus_text_action_primitive_v1.json",
            "feature_set_base_macro_plus_text_action_primitive_v1.txt",
            "action_primitive_diagnostics_findings.md",
        ],
    }
    write_findings(
        output_dir / "action_primitive_diagnostics_findings.md",
        summary,
        metric_rows,
        delta_rows,
        recommended_features,
        rationale_rows,
    )
    _write_json(output_dir / "action_primitive_diagnostics_summary.json", summary)
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--panel",
        type=Path,
        default=Path("data") / "exports" / "daily_retrieval_ppo_full_dis_legacy" / "rl_panel_codex_rule_text_features.csv",
    )
    parser.add_argument(
        "--actions",
        type=Path,
        default=Path("..")
        / "Supportive_project_FinGPT_as_feature_engine"
        / "latent_actions_previous_experiments"
        / "Latent Actions"
        / "research_outputs_phase2_base_macro_teacher"
        / "walk_forward_test_actions.csv",
    )
    parser.add_argument(
        "--codes",
        type=Path,
        default=Path("..")
        / "Supportive_project_FinGPT_as_feature_engine"
        / "latent_actions_previous_experiments"
        / "Latent Actions"
        / "research_outputs_phase2_teacher_action_audit"
        / "latent_action_teacher_simple_codes.csv",
    )
    parser.add_argument(
        "--behavior",
        type=Path,
        default=Path("..")
        / "Supportive_project_FinGPT_as_feature_engine"
        / "latent_actions_previous_experiments"
        / "Behavior Interpretability Audit"
        / "research_outputs_behavior_interpretability_base_macro"
        / "behavior_primitive_assignments.csv",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data")
        / "exports"
        / "daily_retrieval_ppo_full_dis_legacy"
        / "ppo_ablation_package"
        / "pre_ppo_diagnostics"
        / "action_primitive_text_diagnostics",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = run_diagnostics(args.panel, args.actions, args.codes, args.behavior, args.output_dir)
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=_json_default))


if __name__ == "__main__":
    main()
