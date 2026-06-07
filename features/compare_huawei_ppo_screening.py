"""Aggregate Huawei one-seed PPO text ablation outputs."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd


MANIFEST_NAME = "huawei_ppo_screening_manifest.json"


def _annualized_sharpe(returns: pd.Series) -> float:
    clean = pd.to_numeric(returns, errors="coerce").dropna()
    if len(clean) < 2:
        return math.nan
    std = float(clean.std(ddof=1))
    if std <= 0.0 or not math.isfinite(std):
        return math.nan
    return float(clean.mean() / std * math.sqrt(252.0))


def _cumulative_return(returns: pd.Series) -> float:
    clean = pd.to_numeric(returns, errors="coerce").fillna(0.0)
    if clean.empty:
        return math.nan
    return float((1.0 + clean).prod() - 1.0)


def _max_drawdown_from_returns(returns: pd.Series) -> float:
    clean = pd.to_numeric(returns, errors="coerce").fillna(0.0)
    if clean.empty:
        return math.nan
    equity = (1.0 + clean).cumprod()
    peak = equity.cummax()
    drawdown = equity / peak - 1.0
    return float(drawdown.min())


def _median_numeric(df: pd.DataFrame, column: str) -> float:
    if column not in df.columns:
        return math.nan
    values = pd.to_numeric(df[column], errors="coerce").dropna()
    return float(values.median()) if not values.empty else math.nan


def _mean_numeric(df: pd.DataFrame, column: str) -> float:
    if column not in df.columns:
        return math.nan
    values = pd.to_numeric(df[column], errors="coerce").dropna()
    return float(values.mean()) if not values.empty else math.nan


def _summarize_daily(daily_path: Path) -> dict[str, Any]:
    if not daily_path.exists():
        return {
            "daily_rows": 0,
            "daily_start": None,
            "daily_end": None,
            "daily_return_mean": math.nan,
            "daily_return_std": math.nan,
            "daily_sharpe_ann": math.nan,
            "daily_cumulative_return": math.nan,
            "daily_max_drawdown": math.nan,
            "daily_turnover_mean": math.nan,
            "daily_excess_return_mean": math.nan,
        }
    daily = pd.read_csv(daily_path)
    if daily.empty or "daily_return" not in daily.columns:
        return {
            "daily_rows": int(len(daily)),
            "daily_start": None,
            "daily_end": None,
            "daily_return_mean": math.nan,
            "daily_return_std": math.nan,
            "daily_sharpe_ann": math.nan,
            "daily_cumulative_return": math.nan,
            "daily_max_drawdown": math.nan,
            "daily_turnover_mean": math.nan,
            "daily_excess_return_mean": math.nan,
        }
    if "date" in daily.columns:
        daily["date"] = pd.to_datetime(daily["date"], errors="coerce")
        group_cols = ["date"]
        if "seed" in daily.columns:
            group_cols.append("seed")
        aggregations = {"daily_return": ("daily_return", "mean")}
        if "turnover" in daily.columns:
            aggregations["turnover"] = ("turnover", "mean")
        if "excess_return_vs_benchmark" in daily.columns:
            aggregations["excess_return_vs_benchmark"] = ("excess_return_vs_benchmark", "mean")
        daily = daily.groupby(group_cols, dropna=False).agg(**aggregations).reset_index().sort_values(group_cols)
    returns = pd.to_numeric(daily["daily_return"], errors="coerce")
    turnover = pd.to_numeric(daily.get("turnover"), errors="coerce") if "turnover" in daily.columns else pd.Series(dtype=float)
    excess = (
        pd.to_numeric(daily.get("excess_return_vs_benchmark"), errors="coerce")
        if "excess_return_vs_benchmark" in daily.columns
        else pd.Series(dtype=float)
    )
    return {
        "daily_rows": int(len(daily)),
        "daily_start": str(daily["date"].min().date()) if "date" in daily.columns and daily["date"].notna().any() else None,
        "daily_end": str(daily["date"].max().date()) if "date" in daily.columns and daily["date"].notna().any() else None,
        "daily_return_mean": float(returns.mean()) if returns.notna().any() else math.nan,
        "daily_return_std": float(returns.std(ddof=1)) if returns.notna().sum() > 1 else math.nan,
        "daily_sharpe_ann": _annualized_sharpe(returns),
        "daily_cumulative_return": _cumulative_return(returns),
        "daily_max_drawdown": _max_drawdown_from_returns(returns),
        "daily_turnover_mean": float(turnover.mean()) if turnover.notna().any() else math.nan,
        "daily_excess_return_mean": float(excess.mean()) if excess.notna().any() else math.nan,
    }


def _summarize_experiment(package_dir: Path, experiment: Mapping[str, Any]) -> dict[str, Any]:
    output_dir = package_dir / str(experiment.get("output_dir"))
    unique_path = output_dir / "unique_run_level_results.csv"
    run_manifest_path = output_dir / "run_manifest.json"
    row: dict[str, Any] = {
        "variant": str(experiment.get("name")),
        "seed": experiment.get("seed"),
        "feature_count": experiment.get("feature_count"),
        "text_feature_count": experiment.get("text_feature_count"),
        "output_dir": str(output_dir),
        "status": "complete" if unique_path.exists() else "pending",
        "run_manifest_exists": run_manifest_path.exists(),
    }
    if not unique_path.exists():
        row.update(
            {
                "fold_count": 0,
                "test_sharpe_median": math.nan,
                "test_sharpe_mean": math.nan,
                "test_return_pct_median": math.nan,
                "test_max_drawdown_median": math.nan,
                "test_turnover_median": math.nan,
                "validation_sharpe_median": math.nan,
            }
        )
        row.update(_summarize_daily(output_dir / "walk_forward_daily_test_returns.csv"))
        return row

    unique = pd.read_csv(unique_path)
    row.update(
        {
            "fold_count": int(unique["fold_id"].nunique()) if "fold_id" in unique.columns else int(len(unique)),
            "run_count": int(unique["run_key"].nunique()) if "run_key" in unique.columns else int(len(unique)),
            "n_features_median": _median_numeric(unique, "n_features"),
            "validation_sharpe_median": _median_numeric(unique, "validation_sharpe"),
            "validation_return_pct_median": _median_numeric(unique, "validation_return_pct"),
            "validation_max_drawdown_median": _median_numeric(unique, "validation_max_drawdown"),
            "validation_turnover_median": _median_numeric(unique, "validation_turnover"),
            "test_sharpe_median": _median_numeric(unique, "test_sharpe"),
            "test_sharpe_mean": _mean_numeric(unique, "test_sharpe"),
            "test_return_pct_median": _median_numeric(unique, "test_return_pct"),
            "test_max_drawdown_median": _median_numeric(unique, "test_max_drawdown"),
            "test_turnover_median": _median_numeric(unique, "test_turnover"),
            "robust_selection_score_median": _median_numeric(unique, "robust_selection_score"),
        }
    )
    row.update(_summarize_daily(output_dir / "walk_forward_daily_test_returns.csv"))
    return row


def _add_base_deltas(results: pd.DataFrame, baseline_name: str = "base_macro") -> pd.DataFrame:
    out = results.copy()
    base_rows = out[out["variant"].eq(baseline_name)]
    if base_rows.empty:
        out["screening_note"] = "baseline_missing"
        return out
    base = base_rows.iloc[0]
    metrics = [
        "test_sharpe_median",
        "test_return_pct_median",
        "test_max_drawdown_median",
        "test_turnover_median",
        "daily_sharpe_ann",
        "daily_cumulative_return",
        "daily_max_drawdown",
        "daily_turnover_mean",
        "daily_excess_return_mean",
    ]
    for metric in metrics:
        if metric in out.columns:
            out[f"{metric}_delta_vs_base"] = pd.to_numeric(out[metric], errors="coerce") - float(base.get(metric, math.nan))

    base_drawdown_abs = abs(float(base.get("daily_max_drawdown", math.nan)))
    base_turnover = float(base.get("daily_turnover_mean", math.nan))
    passes = []
    notes = []
    for _, row in out.iterrows():
        if row["variant"] == baseline_name:
            passes.append(False)
            notes.append("baseline")
            continue
        if row.get("status") != "complete":
            passes.append(False)
            notes.append("pending")
            continue
        sharpe_delta = float(row.get("daily_sharpe_ann_delta_vs_base", math.nan))
        return_delta = float(row.get("daily_cumulative_return_delta_vs_base", math.nan))
        drawdown_abs = abs(float(row.get("daily_max_drawdown", math.nan)))
        turnover = float(row.get("daily_turnover_mean", math.nan))
        drawdown_ok = math.isnan(base_drawdown_abs) or drawdown_abs <= base_drawdown_abs + 0.03
        turnover_ok = math.isnan(base_turnover) or turnover <= base_turnover * 1.25 + 1e-12
        passed = sharpe_delta > 0.0 and return_delta > 0.0 and drawdown_ok and turnover_ok
        passes.append(bool(passed))
        notes.append(
            "pass" if passed else "fail_or_needs_review"
        )
    out["passes_one_seed_screening_gate"] = passes
    out["screening_note"] = notes
    return out


def _write_markdown_summary(results: pd.DataFrame, output_path: Path) -> None:
    lines = [
        "# Huawei PPO Screening Comparison",
        "",
        "Primary decision rule for this one-seed screen: keep a text variant only if it improves daily OOS Sharpe and cumulative return vs `base_macro` without materially worsening drawdown or turnover.",
        "",
        "| variant | status | daily Sharpe | cumulative return | max drawdown | turnover | gate |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for _, row in results.iterrows():
        lines.append(
            "| {variant} | {status} | {sharpe:.4f} | {cum:.4f} | {mdd:.4f} | {turnover:.4f} | {gate} |".format(
                variant=row.get("variant"),
                status=row.get("status"),
                sharpe=float(row.get("daily_sharpe_ann", math.nan)),
                cum=float(row.get("daily_cumulative_return", math.nan)),
                mdd=float(row.get("daily_max_drawdown", math.nan)),
                turnover=float(row.get("daily_turnover_mean", math.nan)),
                gate=row.get("screening_note", ""),
            )
        )
    lines.append("")
    lines.append("If a text variant passes this screen, rerun it with seeds `42, 123, 999` before treating it as a PPO result.")
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def compare_screening_outputs(package_dir: str | Path) -> pd.DataFrame:
    package = Path(package_dir).expanduser().resolve()
    manifest_path = package / MANIFEST_NAME
    if not manifest_path.exists():
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    rows = [_summarize_experiment(package, experiment) for experiment in manifest.get("experiments", [])]
    results = pd.DataFrame(rows)
    results = _add_base_deltas(results)
    output_csv = package / "ppo_ablation_results.csv"
    results.to_csv(output_csv, index=False)
    _write_markdown_summary(results, package / "ppo_ablation_comparison.md")

    daily_frames: list[pd.DataFrame] = []
    for experiment in manifest.get("experiments", []):
        daily_path = package / str(experiment.get("output_dir")) / "walk_forward_daily_test_returns.csv"
        if not daily_path.exists():
            continue
        daily = pd.read_csv(daily_path)
        daily["screening_variant"] = str(experiment.get("name"))
        daily_frames.append(daily)
    if daily_frames:
        combined_daily = pd.concat(daily_frames, ignore_index=True)
        combined_daily.to_csv(package / "ppo_ablation_daily_returns_combined.csv", index=False)
    return results


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--package-dir",
        default=Path(__file__).resolve().parent
        if (Path(__file__).resolve().parent / MANIFEST_NAME).exists()
        else Path("data")
        / "exports"
        / "daily_retrieval_ppo_full_dis_legacy"
        / "ppo_ablation_package"
        / "huawei_screening_seed42",
        type=Path,
        help="Directory containing huawei_ppo_screening_manifest.json and output dirs.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    results = compare_screening_outputs(args.package_dir)
    print(f"wrote {len(results)} screening rows to {Path(args.package_dir).resolve() / 'ppo_ablation_results.csv'}")


if __name__ == "__main__":
    main()
