"""Diagnose text features against PPO teacher actions and behavior primitives.

This is a pre-PPO screen. It asks whether FinIR/FinGPT text features explain
the frozen `base_macro` teacher's action regimes and discovered behavior
primitives before adding text to the PPO observation state.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, balanced_accuracy_score, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler


BASE_MACRO_COLUMNS = [
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

ACTION_TARGETS = [
    "nonflat_flag",
    "action_code_change_flag",
    "large_action_l1_flag",
    "large_action_delta_flag",
]

BEHAVIOR_TARGETS = [
    "bad_primitive_flag",
    "primitive_04_flag",
    "primitive_05_flag",
    "profitable_reliable_primitive_flag",
    "high_turnover_primitive_flag",
]

PRIMARY_ACTION_TARGET_WEIGHTS = {
    "nonflat_flag": 1.0,
    "action_code_change_flag": 1.2,
    "large_action_l1_flag": 1.0,
    "large_action_delta_flag": 1.2,
}

PRIMARY_BEHAVIOR_TARGET_WEIGHTS = {
    "bad_primitive_flag": 1.2,
    "primitive_04_flag": 1.4,
    "primitive_05_flag": 1.4,
    "profitable_reliable_primitive_flag": 0.8,
    "high_turnover_primitive_flag": 1.2,
}

META_COLUMNS = {
    "run_key",
    "feature_set",
    "feature_family",
    "fold_id",
    "seed",
    "split_name",
    "date",
    "action_step",
    "action_row_id",
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
        fieldnames = []
        for row in rows:
            for key in row:
                if key not in fieldnames:
                    fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _text_columns(frame: pd.DataFrame) -> list[str]:
    return sorted(
        column
        for column in frame.columns
        if column.startswith(("stock_text_", "stock_signal_", "portfolio_text_", "portfolio_signal_"))
    )


def _portfolio_text_columns(frame: pd.DataFrame) -> list[str]:
    return [column for column in _text_columns(frame) if column.startswith("portfolio_")]


def _stock_text_columns(frame: pd.DataFrame) -> list[str]:
    return [column for column in _text_columns(frame) if column.startswith("stock_")]


def _is_provenance_like(feature: str) -> bool:
    patterns = (
        "has_evidence",
        "doc_count",
        "unique_doc_count",
        "coverage",
        "age_days",
        "final_score",
    )
    return any(pattern in feature for pattern in patterns)


def build_date_level_text_features(text_panel_path: Path) -> tuple[pd.DataFrame, list[str], list[str]]:
    panel = pd.read_csv(text_panel_path)
    panel["date"] = pd.to_datetime(panel["date"])
    portfolio_cols = _portfolio_text_columns(panel)
    stock_cols = _stock_text_columns(panel)

    portfolio = panel.groupby("date", as_index=False)[portfolio_cols].max()

    stock_rows: list[dict[str, Any]] = []
    for date, group in panel.groupby("date", sort=True):
        row: dict[str, Any] = {"date": date}
        has_evidence = group.get("stock_text_has_evidence", pd.Series(0, index=group.index)).astype(float) > 0
        row["stock_text_coverage_rate"] = float(has_evidence.mean())
        row["stock_text_ticker_count_with_evidence"] = int(has_evidence.sum())
        for column in stock_cols:
            values = group[column].astype(float)
            if column == "stock_text_has_evidence":
                continue
            if column.startswith("stock_signal_") and column.endswith("_count"):
                stem = column.removesuffix("_count")
                row[f"{column}_sum"] = float(values.sum())
                row[f"{column}_max"] = float(values.max())
                row[f"{stem}_share"] = float((values > 0).mean())
            elif column.startswith("stock_signal_") and column.endswith("_flag"):
                stem = column.removesuffix("_flag")
                row[f"{stem}_flag_share"] = float((values > 0).mean())
            elif column in {"stock_text_doc_count", "stock_text_unique_doc_count"}:
                row[f"{column}_sum"] = float(values.sum())
                row[f"{column}_max"] = float(values.max())
            elif column.startswith("stock_text_avg_") or column == "stock_text_max_event_severity":
                evidence_values = values.loc[has_evidence]
                row[f"{column}_mean_all"] = float(values.mean())
                row[f"{column}_max"] = float(values.max())
                row[f"{column}_evidence_mean"] = float(evidence_values.mean()) if not evidence_values.empty else 0.0
            else:
                row[f"{column}_mean_all"] = float(values.mean())
                row[f"{column}_max"] = float(values.max())
        stock_rows.append(row)
    stock = pd.DataFrame(stock_rows)

    base_aggs: dict[str, tuple[str, str]] = {
        "base_daily_return_mean": ("daily_return", "mean"),
        "base_daily_return_std": ("daily_return", "std"),
        "base_atr_rel_mean": ("atr_rel", "mean"),
        "base_macd_mean": ("macd", "mean"),
        "base_rsi_30_mean": ("rsi_30", "mean"),
        "base_cci_30_mean": ("cci_30", "mean"),
        "base_dx_30_mean": ("dx_30", "mean"),
        "base_volume_ratio_mean": ("volume_ratio", "mean"),
        "base_obv_pct_change_mean": ("obv_pct_change", "mean"),
        "base_turbulence_mean": ("turbulence", "mean"),
        "base_turbulence_max": ("turbulence", "max"),
        "base_10Y_Yield": ("10Y_Yield", "first"),
        "base_VIX": ("VIX", "first"),
        "base_SP500_Trend": ("SP500_Trend", "first"),
    }
    present_base_aggs = {
        name: spec for name, spec in base_aggs.items() if spec[0] in panel.columns
    }
    base = panel.groupby("date", as_index=False).agg(**present_base_aggs)
    base = base.fillna(0.0)

    merged = base.merge(portfolio, on="date", how="left").merge(stock, on="date", how="left").fillna(0.0)
    text_features = [column for column in merged.columns if column not in {"date"} and not column.startswith("base_")]
    base_features = [column for column in merged.columns if column.startswith("base_")]
    return merged, base_features, text_features


def _infer_action_cols(simple_codes: pd.DataFrame) -> list[str]:
    excluded = set(META_COLUMNS) | {
        "action_l1",
        "action_l2",
        "action_max_abs",
        "active_action_dims",
        "positive_action_dims",
        "negative_action_dims",
        "direction_code",
        "magnitude_code",
        "concentration_code",
        "simple_action_code",
    }
    numeric_cols = [
        column
        for column in simple_codes.columns
        if column not in excluded and pd.api.types.is_numeric_dtype(simple_codes[column])
    ]
    return numeric_cols


def load_action_dataset(simple_codes_path: Path, date_text: pd.DataFrame) -> pd.DataFrame:
    actions = pd.read_csv(simple_codes_path)
    actions["date"] = pd.to_datetime(actions["date"])
    action_cols = _infer_action_cols(actions)
    sort_cols = [column for column in ["run_key", "fold_id", "seed", "date", "action_step"] if column in actions.columns]
    actions = actions.sort_values(sort_cols).reset_index(drop=True)
    group_cols = [column for column in ["run_key", "fold_id", "seed"] if column in actions.columns]
    actions["previous_simple_action_code"] = actions.groupby(group_cols)["simple_action_code"].shift(1)
    actions["is_first_action_in_run"] = actions["previous_simple_action_code"].isna()
    actions["nonflat_flag"] = (actions["simple_action_code"].astype(str) != "flat__flat__flat").astype(int)
    actions["previous_nonflat_flag"] = actions.groupby(group_cols)["nonflat_flag"].shift(1)
    actions["action_code_change_flag"] = (
        actions["simple_action_code"].astype(str) != actions["previous_simple_action_code"].astype(str)
    ).astype(float)
    actions.loc[actions["is_first_action_in_run"], "action_code_change_flag"] = np.nan
    actions["previous_action_code_change_flag"] = actions.groupby(group_cols)["action_code_change_flag"].shift(1)
    action_values = actions[action_cols].astype(float)
    action_delta = action_values.groupby([actions[column] for column in group_cols]).diff().abs().sum(axis=1)
    actions["action_delta_l1"] = action_delta
    actions.loc[actions["is_first_action_in_run"], "action_delta_l1"] = np.nan
    l1_threshold = actions["action_l1"].quantile(0.75)
    delta_threshold = actions["action_delta_l1"].quantile(0.75)
    actions["large_action_l1_flag"] = (actions["action_l1"] >= l1_threshold).astype(int)
    actions["large_action_delta_flag"] = (actions["action_delta_l1"] >= delta_threshold).astype(float)
    actions.loc[actions["action_delta_l1"].isna(), "large_action_delta_flag"] = np.nan
    actions["previous_large_action_l1_flag"] = actions.groupby(group_cols)["large_action_l1_flag"].shift(1)
    actions["previous_large_action_delta_flag"] = actions.groupby(group_cols)["large_action_delta_flag"].shift(1)
    merged = actions.merge(date_text, on="date", how="left")
    return merged


def load_behavior_dataset(
    behavior_path: Path,
    behavior_summary_path: Path,
    date_text: pd.DataFrame,
) -> pd.DataFrame:
    behavior = pd.read_csv(behavior_path)
    summary = pd.read_csv(behavior_summary_path)
    behavior["date"] = pd.to_datetime(behavior["date"])
    behavior = behavior.merge(summary[["primitive_id", "primitive_type"]], on="primitive_id", how="left")
    behavior["bad_primitive_flag"] = behavior["primitive_type"].eq("bad_or_noisy_primitive").astype(int)
    behavior["profitable_reliable_primitive_flag"] = behavior["primitive_type"].eq("profitable_reliable_candidate").astype(int)
    behavior["primitive_04_flag"] = behavior["primitive_id"].eq("primitive_04").astype(int)
    behavior["primitive_05_flag"] = behavior["primitive_id"].eq("primitive_05").astype(int)
    turnover_threshold = behavior["action_change_l1_wmean"].quantile(0.75)
    behavior["high_turnover_primitive_flag"] = (
        behavior["primitive_id"].eq("primitive_04") | (behavior["action_change_l1_wmean"] >= turnover_threshold)
    ).astype(int)
    group_cols = [column for column in ["run_key", "fold_id", "seed"] if column in behavior.columns]
    sort_cols = group_cols + ["date"]
    behavior = behavior.sort_values(sort_cols).reset_index(drop=True)
    for target in BEHAVIOR_TARGETS:
        behavior[f"previous_{target}"] = behavior.groupby(group_cols)[target].shift(1)
    merged = behavior.merge(date_text, on="date", how="left")
    return merged


def _safe_auc(y_true: pd.Series, score: pd.Series) -> float | None:
    mask = y_true.notna() & score.notna()
    if mask.sum() < 20 or y_true.loc[mask].nunique() < 2 or score.loc[mask].nunique() < 2:
        return None
    try:
        return float(roc_auc_score(y_true.loc[mask], score.loc[mask]))
    except ValueError:
        return None


def _fold_univariate_scores(
    frame: pd.DataFrame,
    features: Sequence[str],
    targets: Sequence[str],
    weights: dict[str, float],
    scope: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    folds = sorted(frame["fold_id"].dropna().unique())
    for feature in features:
        if feature not in frame.columns:
            continue
        values = frame[feature]
        feature_row: dict[str, Any] = {
            "scope": scope,
            "feature": feature,
            "feature_family": (
                "portfolio_text" if feature.startswith("portfolio_") else "stock_text_aggregate"
            ),
            "is_provenance_like": int(_is_provenance_like(feature)),
            "nonzero_rate": float((values.fillna(0) != 0).mean()),
            "std": float(values.std(ddof=0)),
        }
        combined = 0.0
        weighted = 0.0
        for target in targets:
            aucs: list[float] = []
            for fold in folds:
                test = frame["fold_id"].eq(fold)
                auc = _safe_auc(frame.loc[test, target], frame.loc[test, feature])
                if auc is not None:
                    aucs.append(auc)
            if aucs:
                mean_auc = float(np.mean(aucs))
                edge = abs(mean_auc - 0.5)
                direction = 1 if mean_auc >= 0.5 else -1
                stable = float(np.mean([(auc - 0.5) * direction > 0 for auc in aucs]))
            else:
                mean_auc = np.nan
                edge = 0.0
                stable = 0.0
            feature_row[f"{target}_mean_fold_auc"] = None if pd.isna(mean_auc) else mean_auc
            feature_row[f"{target}_auc_edge"] = edge
            feature_row[f"{target}_direction_stability"] = stable
            combined += edge
            weighted += edge * weights.get(target, 1.0) * max(stable, 0.25)
        feature_row["unweighted_auc_edge_sum"] = combined
        feature_row["weighted_stable_score"] = weighted
        rows.append(feature_row)
    return rows


def _classification_metrics(y_true: pd.Series, proba: np.ndarray) -> dict[str, float]:
    pred = (proba >= 0.5).astype(int)
    return {
        "roc_auc": float(roc_auc_score(y_true, proba)),
        "average_precision": float(average_precision_score(y_true, proba)),
        "balanced_accuracy": float(balanced_accuracy_score(y_true, pred)),
    }


def _fit_binary_models(
    frame: pd.DataFrame,
    feature_sets: dict[str, list[str]],
    targets: Sequence[str],
    scope: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    folds = sorted(frame["fold_id"].dropna().unique())
    for target in targets:
        for feature_set_name, features in feature_sets.items():
            fold_metrics: list[dict[str, float]] = []
            present_features = [feature for feature in features if feature in frame.columns]
            if not present_features:
                continue
            for fold in folds:
                train = frame["fold_id"].ne(fold) & frame[target].notna()
                test = frame["fold_id"].eq(fold) & frame[target].notna()
                if train.sum() < 50 or test.sum() < 20:
                    continue
                y_train = frame.loc[train, target].astype(int)
                y_test = frame.loc[test, target].astype(int)
                if y_train.nunique() < 2 or y_test.nunique() < 2:
                    continue
                x_train = frame.loc[train, present_features].fillna(0.0)
                x_test = frame.loc[test, present_features].fillna(0.0)
                model = make_pipeline(
                    StandardScaler(),
                    LogisticRegression(max_iter=1000, class_weight="balanced", solver="lbfgs"),
                )
                model.fit(x_train, y_train)
                proba = model.predict_proba(x_test)[:, 1]
                metrics = _classification_metrics(y_test, proba)
                metrics["fold_id"] = fold
                fold_metrics.append(metrics)
            if fold_metrics:
                metric_frame = pd.DataFrame(fold_metrics)
                rows.append(
                    {
                        "scope": scope,
                        "target": target,
                        "feature_set": feature_set_name,
                        "feature_count": len(present_features),
                        "folds_evaluated": int(len(metric_frame)),
                        "mean_roc_auc": float(metric_frame["roc_auc"].mean()),
                        "mean_average_precision": float(metric_frame["average_precision"].mean()),
                        "mean_balanced_accuracy": float(metric_frame["balanced_accuracy"].mean()),
                    }
                )

        previous_col = f"previous_{target}"
        if previous_col in frame.columns:
            fold_metrics = []
            for fold in folds:
                test = frame["fold_id"].eq(fold) & frame[target].notna() & frame[previous_col].notna()
                if test.sum() < 20:
                    continue
                y_test = frame.loc[test, target].astype(int)
                previous = frame.loc[test, previous_col].astype(float).clip(0.0, 1.0).to_numpy()
                if y_test.nunique() < 2 or len(np.unique(previous)) < 2:
                    continue
                metrics = _classification_metrics(y_test, previous)
                metrics["fold_id"] = fold
                fold_metrics.append(metrics)
            if fold_metrics:
                metric_frame = pd.DataFrame(fold_metrics)
                rows.append(
                    {
                        "scope": scope,
                        "target": target,
                        "feature_set": "previous_target_baseline",
                        "feature_count": 1,
                        "folds_evaluated": int(len(metric_frame)),
                        "mean_roc_auc": float(metric_frame["roc_auc"].mean()),
                        "mean_average_precision": float(metric_frame["average_precision"].mean()),
                        "mean_balanced_accuracy": float(metric_frame["balanced_accuracy"].mean()),
                    }
                )
    return rows


def _model_delta_rows(model_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    frame = pd.DataFrame(model_rows)
    if frame.empty:
        return []
    rows: list[dict[str, Any]] = []
    for (scope, target), group in frame.groupby(["scope", "target"]):
        base = group[group["feature_set"] == "base_date"]
        if base.empty:
            continue
        base_row = base.iloc[0]
        for _, row in group.iterrows():
            rows.append(
                {
                    "scope": scope,
                    "target": target,
                    "feature_set": row["feature_set"],
                    "delta_roc_auc_vs_base_date": float(row["mean_roc_auc"] - base_row["mean_roc_auc"]),
                    "delta_average_precision_vs_base_date": float(
                        row["mean_average_precision"] - base_row["mean_average_precision"]
                    ),
                    "delta_balanced_accuracy_vs_base_date": float(
                        row["mean_balanced_accuracy"] - base_row["mean_balanced_accuracy"]
                    ),
                }
            )
    return rows


def _drop_redundant_features(frame: pd.DataFrame, candidates: list[str], max_abs_corr: float = 0.95) -> list[str]:
    selected: list[str] = []
    for feature in candidates:
        keep = True
        for existing in selected:
            corr = frame[[feature, existing]].corr(method="spearman").iloc[0, 1]
            if not pd.isna(corr) and abs(float(corr)) >= max_abs_corr:
                keep = False
                break
        if keep:
            selected.append(feature)
    return selected


def _build_scoreboard(
    action_scores: list[dict[str, Any]],
    behavior_scores: list[dict[str, Any]],
    date_text: pd.DataFrame,
) -> pd.DataFrame:
    action = pd.DataFrame(action_scores)
    behavior = pd.DataFrame(behavior_scores)
    action_part = action[["feature", "weighted_stable_score"]].rename(
        columns={"weighted_stable_score": "action_weighted_score"}
    )
    behavior_part = behavior[["feature", "weighted_stable_score"]].rename(
        columns={"weighted_stable_score": "behavior_weighted_score"}
    )
    score = action_part.merge(behavior_part, on="feature", how="outer").fillna(0.0)
    metadata_cols = ["feature", "feature_family", "is_provenance_like", "nonzero_rate", "std"]
    metadata = pd.concat([action[metadata_cols], behavior[metadata_cols]], ignore_index=True)
    metadata = metadata.drop_duplicates("feature")
    score = metadata.merge(score, on="feature", how="left").fillna(0.0)
    score["combined_action_behavior_score"] = score["action_weighted_score"] + score["behavior_weighted_score"]
    score["semantic_score"] = np.where(
        score["is_provenance_like"].astype(int).eq(1),
        score["combined_action_behavior_score"] * 0.35,
        score["combined_action_behavior_score"],
    )
    score = score.sort_values(["semantic_score", "combined_action_behavior_score"], ascending=False).reset_index(drop=True)
    score["rank"] = np.arange(1, len(score) + 1)
    candidates = score[
        (score["std"] > 0)
        & (score["semantic_score"] > 0)
        & (score["feature"].isin(date_text.columns))
        & (~score["feature"].str.endswith("_flag_share"))
    ]["feature"].tolist()
    selected = _drop_redundant_features(date_text, candidates)[:12]
    score["recommended_action_behavior_v1"] = score["feature"].isin(selected).astype(int)
    return score


def _markdown_table(frame: pd.DataFrame, columns: Sequence[str], max_rows: int = 12) -> str:
    def fmt(value: Any) -> str:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return ""
        if isinstance(value, float):
            return f"{value:.4f}"
        return str(value)

    subset = frame.loc[:, [column for column in columns if column in frame.columns]].head(max_rows)
    lines = [
        "| " + " | ".join(subset.columns) + " |",
        "| " + " | ".join(["---"] * len(subset.columns)) + " |",
    ]
    for _, row in subset.iterrows():
        lines.append("| " + " | ".join(fmt(row[column]) for column in subset.columns) + " |")
    return "\n".join(lines)


def _write_findings(
    path: Path,
    summary: dict[str, Any],
    scoreboard: pd.DataFrame,
    model_metrics: pd.DataFrame,
    model_deltas: pd.DataFrame,
    recommended: list[str],
) -> None:
    best_models = model_metrics.sort_values("mean_roc_auc", ascending=False)
    positive_deltas = model_deltas[
        model_deltas["feature_set"].isin(["base_plus_text_all", "base_plus_text_recommended", "text_all"])
    ].sort_values("delta_roc_auc_vs_base_date", ascending=False)
    lines = [
        "# Action/Behavior Text Diagnostics",
        "",
        f"- action rows: `{summary['action_rows']}`",
        f"- behavior rows: `{summary['behavior_rows']}`",
        f"- date-level text features: `{summary['date_level_text_feature_count']}`",
        f"- recommended features: `{len(recommended)}`",
        "",
        "## Interpretation",
        "",
        "This diagnostic screens text features against frozen `base_macro` teacher actions and behavior primitives. It does not prove PPO improvement. A feature is useful here if it helps explain action regimes or bad/reliable primitives under fold-held-out checks.",
        "",
        "## Recommended Features",
        "",
        "\n".join(f"- `{feature}`" for feature in recommended),
        "",
        "## Top Text Scoreboard",
        "",
        _markdown_table(
            scoreboard,
            [
                "rank",
                "feature",
                "feature_family",
                "is_provenance_like",
                "action_weighted_score",
                "behavior_weighted_score",
                "combined_action_behavior_score",
                "semantic_score",
            ],
            max_rows=15,
        ),
        "",
        "## Best Fold-Held-Out Models",
        "",
        _markdown_table(
            best_models,
            ["scope", "target", "feature_set", "feature_count", "folds_evaluated", "mean_roc_auc", "mean_balanced_accuracy"],
            max_rows=16,
        ),
        "",
        "## Best Deltas Vs Base Date Features",
        "",
        _markdown_table(
            positive_deltas,
            ["scope", "target", "feature_set", "delta_roc_auc_vs_base_date", "delta_balanced_accuracy_vs_base_date"],
            max_rows=16,
        ),
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def run_diagnostics(
    text_panel: Path,
    latent_root: Path,
    output_dir: Path,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    date_text, base_features, text_features = build_date_level_text_features(text_panel)
    date_text.to_csv(output_dir / "date_level_text_features.csv", index=False)

    action_path = latent_root / "Latent Actions" / "research_outputs_phase2_teacher_action_audit" / "latent_action_teacher_simple_codes.csv"
    behavior_path = latent_root / "Behavior Interpretability Audit" / "research_outputs_behavior_interpretability_base_macro" / "behavior_primitive_assignments.csv"
    behavior_summary_path = latent_root / "Behavior Interpretability Audit" / "research_outputs_behavior_interpretability_base_macro" / "behavior_primitive_summary.csv"

    action = load_action_dataset(action_path, date_text)
    behavior = load_behavior_dataset(behavior_path, behavior_summary_path, date_text)
    action.to_csv(output_dir / "action_text_dataset.csv", index=False)
    behavior.to_csv(output_dir / "behavior_text_dataset.csv", index=False)

    action_scores = _fold_univariate_scores(action, text_features, ACTION_TARGETS, PRIMARY_ACTION_TARGET_WEIGHTS, "action")
    behavior_scores = _fold_univariate_scores(behavior, text_features, BEHAVIOR_TARGETS, PRIMARY_BEHAVIOR_TARGET_WEIGHTS, "behavior")
    _write_csv(output_dir / "action_text_univariate_scores.csv", action_scores)
    _write_csv(output_dir / "behavior_text_univariate_scores.csv", behavior_scores)

    scoreboard = _build_scoreboard(action_scores, behavior_scores, date_text)
    scoreboard.to_csv(output_dir / "text_feature_action_behavior_scoreboard.csv", index=False)
    recommended = scoreboard.loc[scoreboard["recommended_action_behavior_v1"].eq(1), "feature"].tolist()
    (output_dir / "recommended_text_features_action_behavior_v1.txt").write_text(
        "\n".join(recommended) + "\n",
        encoding="utf-8",
    )
    _write_json(
        output_dir / "feature_set_action_behavior_text_v1.json",
        {
            "name": "base_date_plus_action_behavior_text_v1",
            "feature_columns": base_features + recommended,
            "base_date_feature_columns": base_features,
            "text_feature_columns": recommended,
            "feature_count": len(base_features) + len(recommended),
            "note": "Date-level diagnostic feature set for action/behavior screening, not a direct PPO observation list.",
        },
    )

    text_recommended = recommended
    portfolio_text = [feature for feature in text_features if feature.startswith("portfolio_")]
    stock_text = [feature for feature in text_features if feature.startswith("stock_")]
    feature_sets = {
        "base_date": base_features,
        "text_all": text_features,
        "portfolio_text": portfolio_text,
        "stock_text_aggregate": stock_text,
        "text_recommended": text_recommended,
        "base_plus_text_all": base_features + text_features,
        "base_plus_text_recommended": base_features + text_recommended,
    }
    action_model_rows = _fit_binary_models(action, feature_sets, ACTION_TARGETS, "action")
    behavior_model_rows = _fit_binary_models(behavior, feature_sets, BEHAVIOR_TARGETS, "behavior")
    model_rows = action_model_rows + behavior_model_rows
    model_delta_rows = _model_delta_rows(model_rows)
    _write_csv(output_dir / "action_behavior_model_metrics.csv", model_rows)
    _write_csv(output_dir / "action_behavior_model_deltas_vs_base.csv", model_delta_rows)

    model_metrics = pd.DataFrame(model_rows)
    model_deltas = pd.DataFrame(model_delta_rows)
    summary = {
        "text_panel": str(text_panel),
        "latent_root": str(latent_root),
        "output_dir": str(output_dir),
        "date_rows": int(len(date_text)),
        "action_rows": int(len(action)),
        "behavior_rows": int(len(behavior)),
        "action_date_match_rate": float(action[text_features].notna().all(axis=1).mean()),
        "behavior_date_match_rate": float(behavior[text_features].notna().all(axis=1).mean()),
        "base_date_feature_count": int(len(base_features)),
        "date_level_text_feature_count": int(len(text_features)),
        "recommended_text_features": recommended,
        "recommended_total_date_feature_count": int(len(base_features) + len(recommended)),
        "outputs": [
            "date_level_text_features.csv",
            "action_text_dataset.csv",
            "behavior_text_dataset.csv",
            "action_text_univariate_scores.csv",
            "behavior_text_univariate_scores.csv",
            "text_feature_action_behavior_scoreboard.csv",
            "recommended_text_features_action_behavior_v1.txt",
            "feature_set_action_behavior_text_v1.json",
            "action_behavior_model_metrics.csv",
            "action_behavior_model_deltas_vs_base.csv",
            "ACTION_BEHAVIOR_TEXT_DIAGNOSTICS.md",
        ],
    }
    _write_json(output_dir / "action_behavior_text_diagnostics_summary.json", summary)
    _write_findings(
        output_dir / "ACTION_BEHAVIOR_TEXT_DIAGNOSTICS.md",
        summary,
        scoreboard,
        model_metrics,
        model_deltas,
        recommended,
    )
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--text-panel",
        type=Path,
        default=Path("data") / "exports" / "daily_retrieval_ppo_full_dis_legacy" / "rl_panel_codex_rule_text_features.csv",
    )
    parser.add_argument(
        "--latent-root",
        type=Path,
        default=Path("..") / "Supportive_project_FinGPT_as_feature_engine" / "latent_actions_previous_experiments",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data")
        / "exports"
        / "daily_retrieval_ppo_full_dis_legacy"
        / "ppo_ablation_package"
        / "pre_ppo_diagnostics"
        / "action_behavior_text_diagnostics",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = run_diagnostics(args.text_panel, args.latent_root, args.output_dir)
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=_json_default))


if __name__ == "__main__":
    main()
