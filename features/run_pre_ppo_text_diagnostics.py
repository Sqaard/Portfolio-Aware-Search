"""Run pre-PPO diagnostics for text features.

This script does not train PPO. It builds causal forward targets from the merged
panel, evaluates simple train/OOS models, and writes a lean text-feature shortlist
for a later controlled PPO ablation.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.metrics import (
    average_precision_score,
    balanced_accuracy_score,
    mean_absolute_error,
    r2_score,
    roc_auc_score,
)
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler


TRAIN_END = "2021-10-01"
TEST_START = "2021-10-01"
TEST_END = "2023-03-01"
FORWARD_HORIZON = 20
SHORT_RETURN_HORIZON = 5
LEAN_TEXT_FEATURE_LIMIT = 12
LEAN_PORTFOLIO_LIMIT = 6
LEAN_STOCK_LIMIT = 6


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


CONTINUOUS_TARGETS = [
    "fwd_20d_realized_vol",
    "fwd_20d_max_drawdown",
    "fwd_5d_return",
]


BINARY_TARGETS = [
    "fwd_20d_high_vol_flag",
    "fwd_20d_drawdown_flag",
    "fwd_20d_risk_flag",
    "fwd_5d_negative_return_flag",
]


PROVENANCE_LIKE_COLUMNS = {
    "portfolio_text_has_evidence",
    "portfolio_text_doc_count",
    "portfolio_text_unique_doc_count",
    "stock_text_has_evidence",
    "stock_text_doc_count",
    "stock_text_unique_doc_count",
}


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


def _safe_auc(y_true: pd.Series, score: pd.Series) -> float | None:
    mask = y_true.notna() & score.notna()
    if mask.sum() == 0 or y_true.loc[mask].nunique() < 2:
        return None
    try:
        return float(roc_auc_score(y_true.loc[mask], score.loc[mask]))
    except ValueError:
        return None


def _spearman(x: pd.Series, y: pd.Series) -> float | None:
    mask = x.notna() & y.notna()
    if mask.sum() < 50 or x.loc[mask].nunique() <= 1 or y.loc[mask].nunique() <= 1:
        return None
    value = x.loc[mask].corr(y.loc[mask], method="spearman")
    if pd.isna(value):
        return None
    return float(value)


def _forward_compound_return(returns: pd.Series, horizon: int) -> pd.Series:
    log_returns = np.log1p(returns.clip(lower=-0.999999))
    return np.expm1(log_returns.rolling(horizon).sum().shift(-horizon))


def _forward_realized_vol(returns: pd.Series, horizon: int) -> pd.Series:
    return returns.rolling(horizon).std(ddof=0).shift(-horizon) * math.sqrt(252)


def _forward_max_drawdown(returns: pd.Series, horizon: int) -> pd.Series:
    values = returns.to_numpy(dtype=float)
    out = np.full(len(values), np.nan, dtype=float)
    for idx in range(0, len(values) - horizon):
        window = values[idx + 1 : idx + horizon + 1]
        if np.isnan(window).any():
            continue
        wealth = np.concatenate([[1.0], np.cumprod(1.0 + window)])
        peaks = np.maximum.accumulate(wealth)
        drawdowns = wealth / peaks - 1.0
        out[idx] = drawdowns.min()
    return pd.Series(out, index=returns.index)


def add_forward_targets(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, float]]:
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values(["tic", "date"]).reset_index(drop=True)
    grouped = df.groupby("tic", group_keys=False)["daily_return"]
    df["fwd_5d_return"] = grouped.apply(lambda s: _forward_compound_return(s, SHORT_RETURN_HORIZON))
    df["fwd_20d_return"] = grouped.apply(lambda s: _forward_compound_return(s, FORWARD_HORIZON))
    df["fwd_20d_realized_vol"] = grouped.apply(lambda s: _forward_realized_vol(s, FORWARD_HORIZON))
    df["fwd_20d_max_drawdown"] = grouped.apply(lambda s: _forward_max_drawdown(s, FORWARD_HORIZON))

    train_mask = df["date"] < pd.Timestamp(TRAIN_END)
    thresholds = {
        "fwd_20d_realized_vol_q75_train": float(df.loc[train_mask, "fwd_20d_realized_vol"].quantile(0.75)),
        "fwd_20d_max_drawdown_q25_train": float(df.loc[train_mask, "fwd_20d_max_drawdown"].quantile(0.25)),
    }
    df["fwd_20d_high_vol_flag"] = (
        df["fwd_20d_realized_vol"] >= thresholds["fwd_20d_realized_vol_q75_train"]
    ).astype(int)
    df["fwd_20d_drawdown_flag"] = (
        df["fwd_20d_max_drawdown"] <= thresholds["fwd_20d_max_drawdown_q25_train"]
    ).astype(int)
    df["fwd_20d_risk_flag"] = (
        (df["fwd_20d_high_vol_flag"] == 1) | (df["fwd_20d_drawdown_flag"] == 1)
    ).astype(int)
    df["fwd_5d_negative_return_flag"] = (df["fwd_5d_return"] < 0).astype(int)
    return df, thresholds


def text_feature_columns(df: pd.DataFrame) -> list[str]:
    prefixes = ("stock_text_", "portfolio_text_", "stock_signal_", "portfolio_signal_")
    return sorted([column for column in df.columns if column.startswith(prefixes)])


def load_feature_sets(plan_path: Path) -> dict[str, list[str]]:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    return {variant["name"]: list(variant["feature_columns"]) for variant in plan["variants"]}


def split_masks(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    train = df["date"] < pd.Timestamp(TRAIN_END)
    test = (df["date"] >= pd.Timestamp(TEST_START)) & (df["date"] < pd.Timestamp(TEST_END))
    valid_targets = df[CONTINUOUS_TARGETS + BINARY_TARGETS].notna().all(axis=1)
    return train & valid_targets, test & valid_targets


def feature_relation_rows(
    df: pd.DataFrame,
    features: list[str],
    train_mask: pd.Series,
    test_mask: pd.Series,
    scope: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for feature in features:
        series = df[feature]
        row: dict[str, Any] = {
            "scope": scope,
            "feature": feature,
            "feature_family": "portfolio" if feature.startswith("portfolio_") else "stock",
            "is_provenance_like": int(feature in PROVENANCE_LIKE_COLUMNS),
            "train_nonzero_rate": float((series.loc[train_mask] != 0).mean()),
            "test_nonzero_rate": float((series.loc[test_mask] != 0).mean()),
            "train_std": float(series.loc[train_mask].std(ddof=0)),
            "test_std": float(series.loc[test_mask].std(ddof=0)),
        }
        best_abs_train_corr = 0.0
        best_abs_test_corr = 0.0
        stable_sign_count = 0
        for target in CONTINUOUS_TARGETS:
            train_corr = _spearman(series.loc[train_mask], df.loc[train_mask, target])
            test_corr = _spearman(series.loc[test_mask], df.loc[test_mask, target])
            row[f"{target}_train_spearman"] = train_corr
            row[f"{target}_test_spearman"] = test_corr
            if train_corr is not None:
                best_abs_train_corr = max(best_abs_train_corr, abs(train_corr))
            if test_corr is not None:
                best_abs_test_corr = max(best_abs_test_corr, abs(test_corr))
            if train_corr is not None and test_corr is not None and np.sign(train_corr) == np.sign(test_corr):
                stable_sign_count += 1
        best_auc_edge = 0.0
        for target in BINARY_TARGETS:
            train_auc = _safe_auc(df.loc[train_mask, target], series.loc[train_mask])
            test_auc = _safe_auc(df.loc[test_mask, target], series.loc[test_mask])
            row[f"{target}_train_auc"] = train_auc
            row[f"{target}_test_auc"] = test_auc
            if train_auc is not None:
                best_auc_edge = max(best_auc_edge, abs(train_auc - 0.5))
        row["best_abs_train_spearman"] = best_abs_train_corr
        row["best_abs_test_spearman"] = best_abs_test_corr
        row["stable_continuous_target_signs"] = stable_sign_count
        row["best_train_auc_edge"] = best_auc_edge
        row["train_only_screen_score"] = best_abs_train_corr + 2.0 * best_auc_edge
        rows.append(row)
    return rows


def portfolio_date_level_frame(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, float]]:
    daily = (
        df.groupby("date", as_index=False)
        .agg(
            daily_return=("daily_return", "mean"),
            **{feature: (feature, "first") for feature in text_feature_columns(df) if feature.startswith("portfolio_")},
        )
        .sort_values("date")
        .reset_index(drop=True)
    )
    returns = daily["daily_return"]
    daily["fwd_5d_return"] = _forward_compound_return(returns, SHORT_RETURN_HORIZON)
    daily["fwd_20d_return"] = _forward_compound_return(returns, FORWARD_HORIZON)
    daily["fwd_20d_realized_vol"] = _forward_realized_vol(returns, FORWARD_HORIZON)
    daily["fwd_20d_max_drawdown"] = _forward_max_drawdown(returns, FORWARD_HORIZON)
    train_mask = daily["date"] < pd.Timestamp(TRAIN_END)
    thresholds = {
        "portfolio_fwd_20d_realized_vol_q75_train": float(daily.loc[train_mask, "fwd_20d_realized_vol"].quantile(0.75)),
        "portfolio_fwd_20d_max_drawdown_q25_train": float(daily.loc[train_mask, "fwd_20d_max_drawdown"].quantile(0.25)),
    }
    daily["fwd_20d_high_vol_flag"] = (
        daily["fwd_20d_realized_vol"] >= thresholds["portfolio_fwd_20d_realized_vol_q75_train"]
    ).astype(int)
    daily["fwd_20d_drawdown_flag"] = (
        daily["fwd_20d_max_drawdown"] <= thresholds["portfolio_fwd_20d_max_drawdown_q25_train"]
    ).astype(int)
    daily["fwd_20d_risk_flag"] = (
        (daily["fwd_20d_high_vol_flag"] == 1) | (daily["fwd_20d_drawdown_flag"] == 1)
    ).astype(int)
    daily["fwd_5d_negative_return_flag"] = (daily["fwd_5d_return"] < 0).astype(int)
    return daily, thresholds


def fit_classification_models(
    df: pd.DataFrame,
    feature_sets: dict[str, list[str]],
    train_mask: pd.Series,
    test_mask: pd.Series,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    metric_rows: list[dict[str, Any]] = []
    coef_rows: list[dict[str, Any]] = []
    for variant, features in feature_sets.items():
        missing = [feature for feature in features if feature not in df.columns]
        if missing:
            continue
        x_train = df.loc[train_mask, features]
        x_test = df.loc[test_mask, features]
        for target in BINARY_TARGETS:
            y_train = df.loc[train_mask, target].astype(int)
            y_test = df.loc[test_mask, target].astype(int)
            if y_train.nunique() < 2 or y_test.nunique() < 2:
                continue
            model = make_pipeline(
                StandardScaler(),
                LogisticRegression(max_iter=1000, class_weight="balanced", solver="lbfgs"),
            )
            model.fit(x_train, y_train)
            proba = model.predict_proba(x_test)[:, 1]
            pred = (proba >= 0.5).astype(int)
            metric_rows.append(
                {
                    "task": "classification",
                    "variant": variant,
                    "target": target,
                    "feature_count": len(features),
                    "train_rows": int(len(x_train)),
                    "test_rows": int(len(x_test)),
                    "test_positive_rate": float(y_test.mean()),
                    "roc_auc": float(roc_auc_score(y_test, proba)),
                    "average_precision": float(average_precision_score(y_test, proba)),
                    "balanced_accuracy_at_0_5": float(balanced_accuracy_score(y_test, pred)),
                }
            )
            estimator = model.named_steps["logisticregression"]
            for feature, coef in zip(features, estimator.coef_[0]):
                coef_rows.append(
                    {
                        "task": "classification",
                        "variant": variant,
                        "target": target,
                        "feature": feature,
                        "coefficient": float(coef),
                        "abs_coefficient": float(abs(coef)),
                    }
                )
    return metric_rows, coef_rows


def fit_regression_models(
    df: pd.DataFrame,
    feature_sets: dict[str, list[str]],
    train_mask: pd.Series,
    test_mask: pd.Series,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    metric_rows: list[dict[str, Any]] = []
    coef_rows: list[dict[str, Any]] = []
    for variant, features in feature_sets.items():
        missing = [feature for feature in features if feature not in df.columns]
        if missing:
            continue
        x_train = df.loc[train_mask, features]
        x_test = df.loc[test_mask, features]
        for target in CONTINUOUS_TARGETS:
            y_train = df.loc[train_mask, target]
            y_test = df.loc[test_mask, target]
            model = make_pipeline(StandardScaler(), Ridge(alpha=10.0))
            model.fit(x_train, y_train)
            pred = model.predict(x_test)
            spearman = pd.Series(pred, index=y_test.index).corr(y_test, method="spearman")
            metric_rows.append(
                {
                    "task": "regression",
                    "variant": variant,
                    "target": target,
                    "feature_count": len(features),
                    "train_rows": int(len(x_train)),
                    "test_rows": int(len(x_test)),
                    "target_test_mean": float(y_test.mean()),
                    "r2": float(r2_score(y_test, pred)),
                    "mae": float(mean_absolute_error(y_test, pred)),
                    "prediction_target_spearman": None if pd.isna(spearman) else float(spearman),
                }
            )
            estimator = model.named_steps["ridge"]
            for feature, coef in zip(features, estimator.coef_):
                coef_rows.append(
                    {
                        "task": "regression",
                        "variant": variant,
                        "target": target,
                        "feature": feature,
                        "coefficient": float(coef),
                        "abs_coefficient": float(abs(coef)),
                    }
                )
    return metric_rows, coef_rows


def _drop_redundant_features(df: pd.DataFrame, candidates: list[str], max_abs_corr: float = 0.95) -> list[str]:
    selected: list[str] = []
    for feature in candidates:
        keep = True
        for existing in selected:
            corr = df[[feature, existing]].corr(method="spearman").iloc[0, 1]
            if not pd.isna(corr) and abs(float(corr)) >= max_abs_corr:
                keep = False
                break
        if keep:
            selected.append(feature)
    return selected


def select_lean_text_features(
    df: pd.DataFrame,
    relation_rows: list[dict[str, Any]],
    train_mask: pd.Series,
) -> list[str]:
    relation_df = pd.DataFrame(relation_rows)
    relation_df = relation_df[relation_df["train_std"] > 0].copy()
    relation_df["is_stock"] = relation_df["feature"].str.startswith("stock_")
    relation_df["is_portfolio"] = relation_df["feature"].str.startswith("portfolio_")
    relation_df = relation_df[relation_df["is_provenance_like"] == 0].copy()
    relation_df = relation_df[~relation_df["feature"].str.endswith("_flag")].copy()
    relation_df = relation_df[relation_df["train_nonzero_rate"] < 0.999].copy()
    relation_df["penalty"] = 0.0
    relation_df["selection_score"] = relation_df["train_only_screen_score"] - relation_df["penalty"]

    def ordered_candidates(scope_column: str, limit: int) -> list[str]:
        scoped = relation_df[relation_df[scope_column]].copy()
        if scope_column == "is_stock":
            scoped = scoped[scoped["train_nonzero_rate"] >= 0.005]
        else:
            scoped = scoped[scoped["train_nonzero_rate"] >= 0.01]
        scoped = scoped.sort_values(["selection_score", "best_abs_train_spearman"], ascending=False)
        ordered = list(scoped["feature"])
        filtered = _drop_redundant_features(df.loc[train_mask], ordered)
        return filtered[:limit]

    portfolio = ordered_candidates("is_portfolio", LEAN_PORTFOLIO_LIMIT)
    stock = ordered_candidates("is_stock", LEAN_STOCK_LIMIT)
    selected = portfolio + stock
    return selected[:LEAN_TEXT_FEATURE_LIMIT]


def target_summary_rows(df: pd.DataFrame, train_mask: pd.Series, test_mask: pd.Series) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for split_name, mask in [("train", train_mask), ("test", test_mask)]:
        for target in CONTINUOUS_TARGETS:
            series = df.loc[mask, target]
            rows.append(
                {
                    "split": split_name,
                    "target": target,
                    "type": "continuous",
                    "rows": int(series.notna().sum()),
                    "mean": float(series.mean()),
                    "std": float(series.std(ddof=0)),
                    "min": float(series.min()),
                    "p25": float(series.quantile(0.25)),
                    "median": float(series.median()),
                    "p75": float(series.quantile(0.75)),
                    "max": float(series.max()),
                }
            )
        for target in BINARY_TARGETS:
            series = df.loc[mask, target]
            rows.append(
                {
                    "split": split_name,
                    "target": target,
                    "type": "binary",
                    "rows": int(series.notna().sum()),
                    "positive_rate": float(series.mean()),
                }
            )
    return rows


def detect_action_columns(df: pd.DataFrame) -> list[str]:
    patterns = (
        "teacher_action",
        "action_code",
        "latent_action",
        "position_delta",
        "position_change",
        "target_weight",
        "portfolio_weight",
        "flat_nonflat",
    )
    return [column for column in df.columns if any(pattern in column.lower() for pattern in patterns)]


def metric_delta_rows(
    classification_rows: list[dict[str, Any]],
    regression_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    cls = pd.DataFrame(classification_rows)
    if not cls.empty:
        for target, group in cls.groupby("target"):
            base = group[group["variant"] == "base_macro"]
            if base.empty:
                continue
            base_row = base.iloc[0]
            for _, row in group.iterrows():
                rows.append(
                    {
                        "task": "classification",
                        "variant": row["variant"],
                        "target": target,
                        "delta_roc_auc_vs_base": float(row["roc_auc"] - base_row["roc_auc"]),
                        "delta_average_precision_vs_base": float(row["average_precision"] - base_row["average_precision"]),
                        "delta_balanced_accuracy_vs_base": float(row["balanced_accuracy_at_0_5"] - base_row["balanced_accuracy_at_0_5"]),
                    }
                )
    reg = pd.DataFrame(regression_rows)
    if not reg.empty:
        for target, group in reg.groupby("target"):
            base = group[group["variant"] == "base_macro"]
            if base.empty:
                continue
            base_row = base.iloc[0]
            for _, row in group.iterrows():
                rows.append(
                    {
                        "task": "regression",
                        "variant": row["variant"],
                        "target": target,
                        "delta_r2_vs_base": float(row["r2"] - base_row["r2"]),
                        "delta_mae_vs_base": float(row["mae"] - base_row["mae"]),
                        "delta_prediction_target_spearman_vs_base": float(
                            row["prediction_target_spearman"] - base_row["prediction_target_spearman"]
                        ),
                    }
                )
    return rows


def _markdown_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
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
    for row in rows:
        lines.append("| " + " | ".join(fmt(row.get(column)) for column in columns) + " |")
    return "\n".join(lines)


def write_findings_report(
    path: Path,
    summary: dict[str, Any],
    classification_rows: list[dict[str, Any]],
    regression_rows: list[dict[str, Any]],
    delta_rows: list[dict[str, Any]],
    lean_text_features: list[str],
) -> None:
    cls = pd.DataFrame(classification_rows)
    reg = pd.DataFrame(regression_rows)
    deltas = pd.DataFrame(delta_rows)
    cls_rows: list[dict[str, Any]] = []
    reg_rows: list[dict[str, Any]] = []

    if not cls.empty and not deltas.empty:
        cls_delta = deltas[deltas["task"] == "classification"]
        for variant in [
            "base_macro",
            "base_macro_plus_portfolio_text_core",
            "base_macro_plus_stock_text_core",
            "base_macro_plus_all_text_core",
            "base_macro_plus_all_text_all",
            "base_macro_plus_text_lean_v1",
        ]:
            row: dict[str, Any] = {"variant": variant}
            for target in ["fwd_20d_drawdown_flag", "fwd_20d_high_vol_flag", "fwd_20d_risk_flag"]:
                metric_match = cls[(cls["variant"] == variant) & (cls["target"] == target)]
                delta_match = cls_delta[(cls_delta["variant"] == variant) & (cls_delta["target"] == target)]
                if not metric_match.empty:
                    row[f"{target}_auc"] = float(metric_match.iloc[0]["roc_auc"])
                if not delta_match.empty:
                    row[f"{target}_delta_auc"] = float(delta_match.iloc[0]["delta_roc_auc_vs_base"])
            cls_rows.append(row)

    if not reg.empty and not deltas.empty:
        reg_delta = deltas[deltas["task"] == "regression"]
        for variant in [
            "base_macro",
            "base_macro_plus_stock_text_core",
            "base_macro_plus_all_text_core",
            "base_macro_plus_all_text_all",
            "base_macro_plus_text_lean_v1",
        ]:
            row = {"variant": variant}
            for target in ["fwd_20d_realized_vol", "fwd_20d_max_drawdown", "fwd_5d_return"]:
                metric_match = reg[(reg["variant"] == variant) & (reg["target"] == target)]
                delta_match = reg_delta[(reg_delta["variant"] == variant) & (reg_delta["target"] == target)]
                if not metric_match.empty:
                    row[f"{target}_spearman"] = float(metric_match.iloc[0]["prediction_target_spearman"])
                if not delta_match.empty:
                    row[f"{target}_delta_spearman"] = float(delta_match.iloc[0]["delta_prediction_target_spearman_vs_base"])
            reg_rows.append(row)

    max_positive_text_auc_delta = 0.0
    if not deltas.empty:
        cls_delta = deltas[(deltas["task"] == "classification") & (deltas["variant"] != "base_macro")]
        if not cls_delta.empty:
            max_positive_text_auc_delta = float(cls_delta["delta_roc_auc_vs_base"].max())

    conclusion = (
        "The text features show weak incremental signal in simple OOS models. "
        "Use the lean feature set as the first PPO sanity ablation; do not start "
        "with the 56-feature core state unless the lean run is stable."
        if max_positive_text_auc_delta < 0.005
        else "Some text variants show material simple-model lift; prioritize the best OOS variant for PPO."
    )

    content = [
        "# Pre-PPO Diagnostics Findings",
        "",
        f"- valid train rows: `{summary['rows_train_valid']}`",
        f"- valid OOS rows: `{summary['rows_test_valid']}`",
        f"- text features audited: `{summary['text_feature_count']}`",
        f"- lean feature count: `{summary['lean_total_feature_count']}` total = 12 base + {len(lean_text_features)} text",
        f"- teacher action diagnostics: `{summary['teacher_action_diagnostics']}`",
        "",
        "## Conclusion",
        "",
        conclusion,
        "",
        "## Lean Text Features",
        "",
        "\n".join(f"- `{feature}`" for feature in lean_text_features),
        "",
        "## Classification ROC AUC",
        "",
        _markdown_table(
            cls_rows,
            [
                "variant",
                "fwd_20d_drawdown_flag_auc",
                "fwd_20d_drawdown_flag_delta_auc",
                "fwd_20d_high_vol_flag_auc",
                "fwd_20d_high_vol_flag_delta_auc",
                "fwd_20d_risk_flag_auc",
                "fwd_20d_risk_flag_delta_auc",
            ],
        ),
        "",
        "## Regression Spearman",
        "",
        _markdown_table(
            reg_rows,
            [
                "variant",
                "fwd_20d_realized_vol_spearman",
                "fwd_20d_realized_vol_delta_spearman",
                "fwd_20d_max_drawdown_spearman",
                "fwd_20d_max_drawdown_delta_spearman",
                "fwd_5d_return_spearman",
                "fwd_5d_return_delta_spearman",
            ],
        ),
        "",
    ]
    path.write_text("\n".join(content), encoding="utf-8")


def run_diagnostics(panel_path: Path, plan_path: Path, output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    df = pd.read_csv(panel_path)
    df, thresholds = add_forward_targets(df)
    train_mask, test_mask = split_masks(df)
    text_features = text_feature_columns(df)
    feature_sets = load_feature_sets(plan_path)
    primary_feature_sets = {
        name: features
        for name, features in feature_sets.items()
        if name
        in {
            "base_macro",
            "base_macro_plus_portfolio_text_core",
            "base_macro_plus_stock_text_core",
            "base_macro_plus_all_text_core",
            "base_macro_plus_all_text_all",
        }
    }

    stock_relation_rows = feature_relation_rows(df, text_features, train_mask, test_mask, "stock_row_level")
    lean_text_features = select_lean_text_features(df, stock_relation_rows, train_mask)
    feature_sets["base_macro_plus_text_lean_v1"] = BASE_MACRO_FEATURES + lean_text_features
    primary_feature_sets["base_macro_plus_text_lean_v1"] = BASE_MACRO_FEATURES + lean_text_features

    portfolio_df, portfolio_thresholds = portfolio_date_level_frame(df)
    portfolio_train_mask, portfolio_test_mask = split_masks(portfolio_df)
    portfolio_features = [feature for feature in text_features if feature.startswith("portfolio_")]
    portfolio_relation_rows = feature_relation_rows(
        portfolio_df,
        portfolio_features,
        portfolio_train_mask,
        portfolio_test_mask,
        "portfolio_date_level",
    )

    classification_rows, classification_coef_rows = fit_classification_models(
        df,
        primary_feature_sets,
        train_mask,
        test_mask,
    )
    regression_rows, regression_coef_rows = fit_regression_models(
        df,
        primary_feature_sets,
        train_mask,
        test_mask,
    )
    delta_rows = metric_delta_rows(classification_rows, regression_rows)

    target_rows = target_summary_rows(df, train_mask, test_mask)
    _write_csv(output_dir / "target_summary.csv", target_rows)
    _write_csv(output_dir / "feature_target_relations_stock_level.csv", stock_relation_rows)
    _write_csv(output_dir / "feature_target_relations_portfolio_level.csv", portfolio_relation_rows)
    _write_csv(output_dir / "simple_oos_classification_metrics.csv", classification_rows)
    _write_csv(output_dir / "simple_oos_regression_metrics.csv", regression_rows)
    _write_csv(output_dir / "simple_oos_model_deltas_vs_base.csv", delta_rows)
    _write_csv(output_dir / "model_coefficients.csv", classification_coef_rows + regression_coef_rows)

    lean_feature_set = {
        "name": "base_macro_plus_text_lean_v1",
        "dataset_path": str(panel_path),
        "index_columns": ["date", "tic"],
        "feature_columns": BASE_MACRO_FEATURES + lean_text_features,
        "base_feature_columns": BASE_MACRO_FEATURES,
        "text_feature_columns": lean_text_features,
        "feature_count": len(BASE_MACRO_FEATURES) + len(lean_text_features),
        "selection_rule": "Train-only screen over forward volatility/drawdown/risk targets; redundant text features removed at Spearman |rho| >= 0.95.",
    }
    _write_json(output_dir / "feature_set_base_macro_plus_text_lean_v1.json", lean_feature_set)
    (output_dir / "feature_set_base_macro_plus_text_lean_v1.txt").write_text(
        "\n".join(lean_feature_set["feature_columns"]) + "\n",
        encoding="utf-8",
    )
    (output_dir / "recommended_text_features_lean_v1.txt").write_text(
        "\n".join(lean_text_features) + "\n",
        encoding="utf-8",
    )

    summary = {
        "panel_path": str(panel_path),
        "plan_path": str(plan_path),
        "output_dir": str(output_dir),
        "rows_total": int(len(df)),
        "rows_train_valid": int(train_mask.sum()),
        "rows_test_valid": int(test_mask.sum()),
        "date_min": str(df["date"].min().date()),
        "date_max": str(df["date"].max().date()),
        "ticker_count": int(df["tic"].nunique()),
        "text_feature_count": len(text_features),
        "thresholds_train_only": {**thresholds, **portfolio_thresholds},
        "teacher_action_columns_detected": detect_action_columns(df),
        "teacher_action_diagnostics": "not_run_no_teacher_action_or_position_file_found",
        "lean_text_features": lean_text_features,
        "lean_total_feature_count": len(BASE_MACRO_FEATURES) + len(lean_text_features),
        "model_variants": list(primary_feature_sets.keys()),
        "outputs": [
            "target_summary.csv",
            "feature_target_relations_stock_level.csv",
            "feature_target_relations_portfolio_level.csv",
            "simple_oos_classification_metrics.csv",
            "simple_oos_regression_metrics.csv",
            "simple_oos_model_deltas_vs_base.csv",
            "model_coefficients.csv",
            "recommended_text_features_lean_v1.txt",
            "feature_set_base_macro_plus_text_lean_v1.json",
            "feature_set_base_macro_plus_text_lean_v1.txt",
        ],
    }
    write_findings_report(
        output_dir / "diagnostics_findings.md",
        summary,
        classification_rows,
        regression_rows,
        delta_rows,
        lean_text_features,
    )
    summary["outputs"].append("diagnostics_findings.md")
    _write_json(output_dir / "diagnostics_summary.json", summary)
    readme = """# Pre-PPO Text Feature Diagnostics

This folder contains cheap diagnostics before any PPO run. Targets are built
from future returns and are used only for feature screening:

- `fwd_20d_realized_vol`
- `fwd_20d_max_drawdown`
- `fwd_20d_high_vol_flag`
- `fwd_20d_drawdown_flag`
- `fwd_20d_risk_flag`
- `fwd_5d_return`

The split is fixed:

- train: dates `< 2021-10-01`
- OOS/test: `2021-10-01` through available data before `2023-03-01`

Teacher-action diagnostics were not run because no teacher action/position file
was found in the current workspace. The script can be extended to merge those
columns once `walk_forward_test_actions.csv` is available.
"""
    (output_dir / "README.md").write_text(readme, encoding="utf-8")
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--panel",
        type=Path,
        default=Path("data") / "exports" / "daily_retrieval_ppo_full_dis_legacy" / "rl_panel_codex_rule_text_features.csv",
    )
    parser.add_argument(
        "--plan",
        type=Path,
        default=Path("data") / "exports" / "daily_retrieval_ppo_full_dis_legacy" / "ppo_ablation_package" / "ppo_ablation_plan.json",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data") / "exports" / "daily_retrieval_ppo_full_dis_legacy" / "ppo_ablation_package" / "pre_ppo_diagnostics",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = run_diagnostics(args.panel, args.plan, args.output_dir)
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=_json_default))


if __name__ == "__main__":
    main()
