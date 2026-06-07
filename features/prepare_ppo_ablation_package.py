"""Prepare a PPO ablation package for rule-based text features.

The package is intentionally runner-agnostic: it validates the merge-ready panel,
writes fixed feature-set definitions, and records the exact dataset paths to use
from the PPO training code.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import shutil
from pathlib import Path
from typing import Any

import pandas as pd


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


PORTFOLIO_TEXT_CORE_FEATURES = [
    "portfolio_text_has_evidence",
    "portfolio_text_doc_count",
    "portfolio_text_unique_doc_count",
    "portfolio_text_avg_final_score",
    "portfolio_text_avg_action_relevance",
    "portfolio_text_avg_age_days",
    "portfolio_text_avg_risk_intensity",
    "portfolio_text_avg_sentiment_proxy",
    "portfolio_text_avg_uncertainty",
    "portfolio_text_max_event_severity",
    "portfolio_signal_credit_count",
    "portfolio_signal_credit_flag",
    "portfolio_signal_macro_rates_count",
    "portfolio_signal_macro_rates_flag",
    "portfolio_signal_consumer_demand_count",
    "portfolio_signal_labor_growth_count",
]


STOCK_TEXT_CORE_FEATURES = [
    "stock_text_has_evidence",
    "stock_text_doc_count",
    "stock_text_unique_doc_count",
    "stock_text_avg_final_score",
    "stock_text_avg_action_relevance",
    "stock_text_avg_age_days",
    "stock_text_avg_risk_intensity",
    "stock_text_avg_sentiment_proxy",
    "stock_text_avg_uncertainty",
    "stock_text_max_event_severity",
    "stock_signal_capital_return_count",
    "stock_signal_capital_return_flag",
    "stock_signal_company_risk_count",
    "stock_signal_company_risk_flag",
    "stock_signal_consumer_demand_count",
    "stock_signal_consumer_demand_flag",
    "stock_signal_earnings_guidance_count",
    "stock_signal_earnings_guidance_flag",
    "stock_signal_labor_growth_count",
    "stock_signal_labor_growth_flag",
    "stock_signal_legal_regulatory_count",
    "stock_signal_legal_regulatory_flag",
    "stock_signal_macro_rates_count",
    "stock_signal_macro_rates_flag",
    "stock_signal_margin_pressure_count",
    "stock_signal_margin_pressure_flag",
    "stock_signal_mna_count",
    "stock_signal_mna_flag",
]


EXPLORATORY_TEXT_FEATURES = [
    "portfolio_signal_consumer_demand_flag",
    "portfolio_signal_energy_count",
    "portfolio_signal_energy_flag",
    "portfolio_signal_inflation_count",
    "portfolio_signal_inflation_flag",
    "portfolio_signal_labor_growth_flag",
    "portfolio_signal_market_volatility_count",
    "portfolio_signal_market_volatility_flag",
    "stock_signal_credit_count",
    "stock_signal_credit_flag",
    "stock_signal_energy_count",
    "stock_signal_energy_flag",
    "stock_signal_housing_count",
    "stock_signal_housing_flag",
    "stock_signal_inflation_count",
    "stock_signal_inflation_flag",
    "stock_signal_market_volatility_count",
    "stock_signal_market_volatility_flag",
    "stock_signal_supply_chain_count",
    "stock_signal_supply_chain_flag",
]


KEY_COLUMNS = ["date", "tic"]
TRAIN_END = "2021-10-01"
TEST_START = "2021-10-01"
TEST_END = "2023-02-28"
SEEDS = [42, 123, 999]


def _as_posix_path(path: Path) -> str:
    return path.resolve().as_posix()


def _relative_posix_path(path: Path, base_dir: Path) -> str:
    return Path(os.path.relpath(path.resolve(), base_dir.resolve())).as_posix()


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _feature_set(
    name: str,
    dataset_key: str,
    dataset_path: str,
    base_features: list[str],
    text_features: list[str],
    description: str,
    stage: str,
) -> dict[str, Any]:
    features = _dedupe(base_features + text_features)
    return {
        "name": name,
        "stage": stage,
        "dataset_key": dataset_key,
        "dataset_path": dataset_path,
        "dataset_path_base": "ppo_ablation_package_dir",
        "index_columns": KEY_COLUMNS,
        "feature_columns": features,
        "base_feature_columns": base_features,
        "text_feature_columns": text_features,
        "feature_count": len(features),
        "description": description,
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _write_txt(path: Path, columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(columns) + "\n", encoding="utf-8")


def _missing_counts(df: pd.DataFrame, columns: list[str]) -> dict[str, int]:
    return {column: int(df[column].isna().sum()) for column in columns if column in df.columns}


def _non_numeric_columns(df: pd.DataFrame, columns: list[str]) -> list[str]:
    return [
        column
        for column in columns
        if column in df.columns and not pd.api.types.is_numeric_dtype(df[column])
    ]


def _inf_counts(df: pd.DataFrame, columns: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for column in columns:
        if column not in df.columns or not pd.api.types.is_numeric_dtype(df[column]):
            continue
        values = df[column].to_numpy()
        count = int((values == math.inf).sum() + (values == -math.inf).sum())
        if count:
            counts[column] = count
    return counts


def _duplicate_key_count(df: pd.DataFrame) -> int:
    return int(df.duplicated(KEY_COLUMNS).sum())


def _split_counts(df: pd.DataFrame) -> dict[str, int]:
    date = pd.to_datetime(df["date"], errors="coerce")
    train_rows = int((date < pd.Timestamp(TRAIN_END)).sum())
    test_rows = int(((date >= pd.Timestamp(TEST_START)) & (date <= pd.Timestamp(TEST_END))).sum())
    outside_rows = int((~((date < pd.Timestamp(TRAIN_END)) | ((date >= pd.Timestamp(TEST_START)) & (date <= pd.Timestamp(TEST_END))))).sum())
    return {"train_rows": train_rows, "test_rows": test_rows, "outside_rows": outside_rows}


def _text_columns(df: pd.DataFrame) -> list[str]:
    return sorted([column for column in df.columns if column.startswith(("stock_text_", "portfolio_text_", "stock_signal_", "portfolio_signal_"))])


def _coverage(df: pd.DataFrame) -> dict[str, float]:
    result: dict[str, float] = {}
    for column in ["stock_text_has_evidence", "portfolio_text_has_evidence"]:
        if column in df.columns:
            result[column] = float((df[column] != 0).mean())
    return result


def _ticker_counts(df: pd.DataFrame) -> dict[str, int]:
    return {str(key): int(value) for key, value in df["tic"].value_counts().sort_index().items()}


def _make_feature_sets(base_panel: Path, merged_panel: Path, package_dir: Path) -> dict[str, dict[str, Any]]:
    base_dataset_path = _relative_posix_path(base_panel, package_dir)
    merged_dataset_path = _relative_posix_path(merged_panel, package_dir)
    portfolio_plus_exploratory = _dedupe(PORTFOLIO_TEXT_CORE_FEATURES + EXPLORATORY_TEXT_FEATURES)
    stock_plus_exploratory = _dedupe(STOCK_TEXT_CORE_FEATURES + EXPLORATORY_TEXT_FEATURES)
    all_core = _dedupe(PORTFOLIO_TEXT_CORE_FEATURES + STOCK_TEXT_CORE_FEATURES)
    all_text = _dedupe(PORTFOLIO_TEXT_CORE_FEATURES + STOCK_TEXT_CORE_FEATURES + EXPLORATORY_TEXT_FEATURES)

    return {
        "base_macro": _feature_set(
            "base_macro",
            "base_panel",
            base_dataset_path,
            BASE_MACRO_FEATURES,
            [],
            "Original PPO macro/technical baseline without IR/FinGPT text features.",
            "primary",
        ),
        "base_macro_plus_portfolio_text_core": _feature_set(
            "base_macro_plus_portfolio_text_core",
            "merged_text_panel",
            merged_dataset_path,
            BASE_MACRO_FEATURES,
            PORTFOLIO_TEXT_CORE_FEATURES,
            "Adds dense portfolio-level macro/market text context. This is the first text ablation to run.",
            "primary",
        ),
        "base_macro_plus_stock_text_core": _feature_set(
            "base_macro_plus_stock_text_core",
            "merged_text_panel",
            merged_dataset_path,
            BASE_MACRO_FEATURES,
            STOCK_TEXT_CORE_FEATURES,
            "Adds stock-level SEC text context. Coverage is sparse by construction, so this isolates stock evidence impact.",
            "primary",
        ),
        "base_macro_plus_all_text_core": _feature_set(
            "base_macro_plus_all_text_core",
            "merged_text_panel",
            merged_dataset_path,
            BASE_MACRO_FEATURES,
            all_core,
            "Adds both portfolio-level and stock-level core text features.",
            "primary",
        ),
        "base_macro_plus_portfolio_text_all": _feature_set(
            "base_macro_plus_portfolio_text_all",
            "merged_text_panel",
            merged_dataset_path,
            BASE_MACRO_FEATURES,
            portfolio_plus_exploratory,
            "Exploratory portfolio text run including very sparse signal families.",
            "secondary",
        ),
        "base_macro_plus_all_text_all": _feature_set(
            "base_macro_plus_all_text_all",
            "merged_text_panel",
            merged_dataset_path,
            BASE_MACRO_FEATURES,
            all_text,
            "Exploratory full text run including sparse one-hot/count signals.",
            "secondary",
        ),
    }


def _validate_feature_sets(
    base_df: pd.DataFrame,
    merged_df: pd.DataFrame,
    feature_sets: dict[str, dict[str, Any]],
    base_panel: Path,
    merged_panel: Path,
) -> list[str]:
    hard_issues: list[str] = []
    for name, spec in feature_sets.items():
        dataset = base_df if spec["dataset_key"] == "base_panel" else merged_df
        missing = [column for column in spec["feature_columns"] if column not in dataset.columns]
        if missing:
            hard_issues.append(f"{name}: missing columns {missing}")
        non_numeric = _non_numeric_columns(dataset, spec["feature_columns"])
        if non_numeric:
            hard_issues.append(f"{name}: non-numeric feature columns {non_numeric}")
        missing_counts = _missing_counts(dataset, spec["feature_columns"])
        nonzero_missing = {key: value for key, value in missing_counts.items() if value}
        if nonzero_missing:
            hard_issues.append(f"{name}: NaN values in feature columns {nonzero_missing}")
        inf_counts = _inf_counts(dataset, spec["feature_columns"])
        if inf_counts:
            hard_issues.append(f"{name}: inf values in feature columns {inf_counts}")
    return hard_issues


def _write_feature_set_summary(path: Path, feature_sets: dict[str, dict[str, Any]]) -> None:
    fieldnames = ["name", "stage", "feature_count", "base_feature_count", "text_feature_count", "dataset_key", "dataset_path", "description"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for spec in feature_sets.values():
            writer.writerow(
                {
                    "name": spec["name"],
                    "stage": spec["stage"],
                    "feature_count": spec["feature_count"],
                    "base_feature_count": len(spec["base_feature_columns"]),
                    "text_feature_count": len(spec["text_feature_columns"]),
                    "dataset_key": spec["dataset_key"],
                    "dataset_path": spec["dataset_path"],
                    "description": spec["description"],
                }
            )


def _write_results_template(path: Path, feature_sets: dict[str, dict[str, Any]]) -> None:
    fieldnames = [
        "variant",
        "stage",
        "seed",
        "dataset_key",
        "feature_count",
        "text_feature_count",
        "train_end_exclusive",
        "test_start_inclusive",
        "test_end_inclusive",
        "oos_sharpe",
        "oos_cumulative_return",
        "oos_annualized_return",
        "max_drawdown",
        "annualized_turnover",
        "transaction_cost_bps",
        "benchmark_oos_sharpe",
        "excess_sharpe_vs_base_macro",
        "excess_return_vs_base_macro",
        "status",
        "notes",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for spec in feature_sets.values():
            for seed in SEEDS:
                writer.writerow(
                    {
                        "variant": spec["name"],
                        "stage": spec["stage"],
                        "seed": seed,
                        "dataset_key": spec["dataset_key"],
                        "feature_count": spec["feature_count"],
                        "text_feature_count": len(spec["text_feature_columns"]),
                        "train_end_exclusive": TRAIN_END,
                        "test_start_inclusive": TEST_START,
                        "test_end_inclusive": TEST_END,
                        "status": "pending",
                    }
                )


def _write_notebook_cell(path: Path, plan_path: Path, project_root: Path) -> None:
    plan_from_project = Path(os.path.relpath(plan_path.resolve(), project_root.resolve())).as_posix()
    plan_from_workspace = (Path(project_root.name) / plan_from_project).as_posix()
    content = f'''"""Notebook bootstrap for PPO text-feature ablations.

Paste or run this cell before the PPO training loop, then iterate over
PPO_ABLATION_VARIANTS and set the runner's dataset/features from each spec.
"""

from pathlib import Path
import json

PPO_ABLATION_PLAN_CANDIDATES = [
    Path(r"{plan_from_workspace}"),
    Path(r"{plan_from_project}"),
    Path("ppo_ablation_plan.json"),
]

for candidate in PPO_ABLATION_PLAN_CANDIDATES:
    if candidate.exists():
        PPO_ABLATION_PLAN_PATH = candidate
        break
else:
    raise FileNotFoundError(
        "Cannot locate ppo_ablation_plan.json. Run this cell from the workspace root, "
        "FinPortfolio_IR root, or the PPO ablation package directory."
    )

PPO_ABLATION_PLAN = json.loads(PPO_ABLATION_PLAN_PATH.read_text(encoding="utf-8"))
PPO_ABLATION_VARIANTS = PPO_ABLATION_PLAN["variants"]
PPO_ABLATION_PACKAGE_DIR = PPO_ABLATION_PLAN_PATH.parent

for variant in PPO_ABLATION_VARIANTS:
    raw_dataset_path = Path(variant["dataset_path"])
    if raw_dataset_path.is_absolute():
        resolved_dataset_path = raw_dataset_path
    else:
        resolved_dataset_path = PPO_ABLATION_PACKAGE_DIR / raw_dataset_path
    variant["resolved_dataset_path"] = str(resolved_dataset_path.resolve())

for variant in PPO_ABLATION_VARIANTS:
    print(
        variant["name"],
        "features=", len(variant["feature_columns"]),
        "dataset=", variant["dataset_path"],
    )

# Adapter pattern for an existing PPO notebook/runner:
# for variant in PPO_ABLATION_VARIANTS:
#     DATASET_PATH = Path(variant["resolved_dataset_path"])
#     TECHNICAL_INDICATORS_LIST = variant["feature_columns"]
#     EXPERIMENT_NAME = variant["name"]
#     for seed in PPO_ABLATION_PLAN["seeds"]:
#         run_ppo_ablation(DATASET_PATH, TECHNICAL_INDICATORS_LIST, seed, EXPERIMENT_NAME)
'''
    path.write_text(content, encoding="utf-8")


def _write_readme(path: Path, plan_path: Path, preflight_path: Path, summary_path: Path) -> None:
    content = f"""# PPO Ablation Package

This package prepares fixed feature lists for comparing the current PPO baseline against
FinIR/Codex-rule text features.

## Files

- `ppo_ablation_plan.json`: canonical experiment manifest.
- `preflight_report.json`: merge and feature validation report.
- `feature_set_summary.csv`: compact feature-set summary.
- `feature_sets/*.json`: machine-readable feature definitions.
- `feature_sets/*.txt`: one feature column per line for notebooks/scripts.
- `launch_notebook_cell.py`: minimal bootstrap cell for an existing PPO notebook.
- `ppo_results_template.csv`: seed-by-variant results table to fill after PPO runs.
- `text_feature_column_audit.csv`: text-column sparsity and distribution audit.
- `pre_ppo_diagnostics/`: optional diagnostics from `features/run_pre_ppo_text_diagnostics.py`.

## Primary Runs

Run these first with identical PPO hyperparameters and seeds:

1. `base_macro`
2. `base_macro_plus_portfolio_text_core`
3. `base_macro_plus_stock_text_core`
4. `base_macro_plus_all_text_core`

The secondary `*_all` variants include very sparse text signal families and should be
treated as exploratory after the core ablation is stable.

## Split Discipline

- train: dates `< 2021-10-01`
- OOS/test: `2021-10-01` through `2023-02-28`
- default seeds: `42, 123, 999`

Keep the PPO environment, reward function, transaction costs, normalization policy,
rebalance cadence, and train/test split identical across variants.

## Where to Start

Read `{plan_path.name}`, check `{preflight_path.name}`, then paste/run
`launch_notebook_cell.py` in the PPO notebook and map:

- `variant["dataset_path"]` to the runner dataset path
- `variant["resolved_dataset_path"]` to the fully resolved local path
- `variant["feature_columns"]` to the observation feature list
- `variant["name"]` to the experiment/run name

Use `{summary_path.name}` when you need a quick overview of feature counts.

## Pre-PPO Diagnostics

Before running PPO, generate the cheap target diagnostics:

```powershell
python -B features\\run_pre_ppo_text_diagnostics.py
```

If the diagnostics recommend `base_macro_plus_text_lean_v1`, start with the
generated lean feature set before trying the wider `*_core` or `*_all` variants.
"""
    path.write_text(content, encoding="utf-8")


def build_package(
    base_panel: Path,
    merged_panel: Path,
    output_dir: Path,
    merge_audit: Path | None,
    text_audit: Path | None,
    package_name: str = "ppo_text_feature_ablation_dis_legacy",
) -> dict[str, Any]:
    base_panel = base_panel.resolve()
    merged_panel = merged_panel.resolve()
    output_dir = output_dir.resolve()
    feature_dir = output_dir / "feature_sets"
    output_dir.mkdir(parents=True, exist_ok=True)
    feature_dir.mkdir(parents=True, exist_ok=True)

    base_df = pd.read_csv(base_panel)
    merged_df = pd.read_csv(merged_panel)
    feature_sets = _make_feature_sets(base_panel, merged_panel, output_dir)

    hard_issues: list[str] = []
    for label, df in [("base_panel", base_df), ("merged_panel", merged_df)]:
        missing_key_columns = [column for column in KEY_COLUMNS if column not in df.columns]
        if missing_key_columns:
            hard_issues.append(f"{label}: missing key columns {missing_key_columns}")
        else:
            duplicate_count = _duplicate_key_count(df)
            if duplicate_count:
                hard_issues.append(f"{label}: duplicate date/tic rows {duplicate_count}")

    if all(column in base_df.columns for column in KEY_COLUMNS) and all(column in merged_df.columns for column in KEY_COLUMNS):
        base_keys = base_df[KEY_COLUMNS].astype(str)
        merged_keys = merged_df[KEY_COLUMNS].astype(str)
        if len(base_keys) != len(merged_keys):
            hard_issues.append(f"row-count mismatch: base={len(base_keys)} merged={len(merged_keys)}")
        elif not base_keys.equals(merged_keys):
            hard_issues.append("date/tic key order differs between base and merged panels")

    hard_issues.extend(_validate_feature_sets(base_df, merged_df, feature_sets, base_panel, merged_panel))

    plan = {
        "name": package_name,
        "created_by": "prepare_ppo_ablation_package.py",
        "path_base": "ppo_ablation_package_dir",
        "base_panel": _relative_posix_path(base_panel, output_dir),
        "merged_text_panel": _relative_posix_path(merged_panel, output_dir),
        "train_end_exclusive": TRAIN_END,
        "test_start_inclusive": TEST_START,
        "test_end_inclusive": TEST_END,
        "seeds": SEEDS,
        "selection_rule": {
            "primary_metric": "median_oos_sharpe_across_seeds",
            "secondary_metrics": [
                "median_oos_cumulative_return",
                "max_drawdown",
                "turnover",
                "hit_rate",
                "seed_stability",
            ],
            "requirement": "Compare every text variant against base_macro under identical PPO settings.",
        },
        "variants": list(feature_sets.values()),
        "results_template": "ppo_results_template.csv",
    }

    for name, spec in feature_sets.items():
        _write_json(feature_dir / f"{name}.json", spec)
        _write_txt(feature_dir / f"{name}.txt", spec["feature_columns"])

    summary_path = output_dir / "feature_set_summary.csv"
    _write_feature_set_summary(summary_path, feature_sets)
    results_template_path = output_dir / "ppo_results_template.csv"
    _write_results_template(results_template_path, feature_sets)

    if text_audit and text_audit.exists():
        shutil.copyfile(text_audit, output_dir / "text_feature_column_audit.csv")

    merge_audit_payload: dict[str, Any] | None = None
    if merge_audit and merge_audit.exists():
        merge_audit_payload = json.loads(merge_audit.read_text(encoding="utf-8"))

    text_cols = _text_columns(merged_df)
    preflight = {
        "status": "passed" if not hard_issues else "failed",
        "hard_issues": hard_issues,
        "base_panel": {
            "path": _relative_posix_path(base_panel, output_dir),
            "rows": int(len(base_df)),
            "columns": int(len(base_df.columns)),
            "date_min": str(base_df["date"].min()) if "date" in base_df.columns else None,
            "date_max": str(base_df["date"].max()) if "date" in base_df.columns else None,
            "ticker_count": int(base_df["tic"].nunique()) if "tic" in base_df.columns else None,
            "duplicate_date_tic_rows": _duplicate_key_count(base_df) if all(column in base_df.columns for column in KEY_COLUMNS) else None,
            "split_counts": _split_counts(base_df) if "date" in base_df.columns else None,
            "ticker_rows": _ticker_counts(base_df) if "tic" in base_df.columns else None,
        },
        "merged_text_panel": {
            "path": _relative_posix_path(merged_panel, output_dir),
            "rows": int(len(merged_df)),
            "columns": int(len(merged_df.columns)),
            "date_min": str(merged_df["date"].min()) if "date" in merged_df.columns else None,
            "date_max": str(merged_df["date"].max()) if "date" in merged_df.columns else None,
            "ticker_count": int(merged_df["tic"].nunique()) if "tic" in merged_df.columns else None,
            "duplicate_date_tic_rows": _duplicate_key_count(merged_df) if all(column in merged_df.columns for column in KEY_COLUMNS) else None,
            "split_counts": _split_counts(merged_df) if "date" in merged_df.columns else None,
            "text_column_count": len(text_cols),
            "text_coverage": _coverage(merged_df),
            "text_missing_total": int(merged_df[text_cols].isna().sum().sum()) if text_cols else 0,
            "text_inf_total": int(sum(_inf_counts(merged_df, text_cols).values())),
        },
        "feature_sets": {
            name: {
                "stage": spec["stage"],
                "feature_count": spec["feature_count"],
                "text_feature_count": len(spec["text_feature_columns"]),
                "missing_columns": [
                    column
                    for column in spec["feature_columns"]
                    if column not in (base_df.columns if spec["dataset_key"] == "base_panel" else merged_df.columns)
                ],
            }
            for name, spec in feature_sets.items()
        },
        "merge_readiness_audit": merge_audit_payload,
    }

    plan_path = output_dir / "ppo_ablation_plan.json"
    preflight_path = output_dir / "preflight_report.json"
    _write_json(plan_path, plan)
    _write_json(preflight_path, preflight)
    _write_notebook_cell(output_dir / "launch_notebook_cell.py", plan_path, Path(__file__).resolve().parents[1])
    _write_readme(output_dir / "README.md", plan_path, preflight_path, summary_path)

    return {
        "output_dir": _as_posix_path(output_dir),
        "plan_path": _as_posix_path(plan_path),
        "preflight_path": _as_posix_path(preflight_path),
        "feature_set_count": len(feature_sets),
        "status": preflight["status"],
        "hard_issues": hard_issues,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-panel",
        type=Path,
        default=Path("..") / "processed_final_fixed_external_lagclean_full.csv",
    )
    parser.add_argument(
        "--merged-panel",
        type=Path,
        default=Path("data") / "exports" / "daily_retrieval_ppo_full_dis_legacy" / "rl_panel_codex_rule_text_features.csv",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data") / "exports" / "daily_retrieval_ppo_full_dis_legacy" / "ppo_ablation_package",
    )
    parser.add_argument(
        "--merge-audit",
        type=Path,
        default=Path("data") / "exports" / "daily_retrieval_ppo_full_dis_legacy" / "merge_readiness_audit.json",
    )
    parser.add_argument(
        "--text-audit",
        type=Path,
        default=Path("data") / "exports" / "daily_retrieval_ppo_full_dis_legacy" / "text_feature_column_audit.csv",
    )
    parser.add_argument("--package-name", default="ppo_text_feature_ablation_dis_legacy")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = build_package(
        base_panel=args.base_panel,
        merged_panel=args.merged_panel,
        output_dir=args.output_dir,
        merge_audit=args.merge_audit,
        text_audit=args.text_audit,
        package_name=args.package_name,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
