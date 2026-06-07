"""Prepare Huawei Cloud PPO screening notebooks and manifest."""

from __future__ import annotations

import argparse
import csv
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


FINIR_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PACKAGE_ROOT = (
    FINIR_ROOT
    / "data"
    / "exports"
    / "daily_retrieval_ppo_full_dis_legacy"
    / "ppo_ablation_package"
)
DEFAULT_OUTPUT_DIR = DEFAULT_PACKAGE_ROOT / "huawei_screening_seed42"
DEFAULT_TEXT_PANEL = (
    FINIR_ROOT
    / "data"
    / "exports"
    / "daily_retrieval_ppo_full_dis_legacy"
    / "rl_panel_codex_rule_text_features.csv"
)
DEFAULT_TIMESTEPS = 200_000
DEFAULT_SEED = 42


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = list(rows[0].keys()) if rows else []
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def _load_feature_spec(path: Path) -> dict[str, Any]:
    spec = _read_json(path)
    feature_columns = [str(column) for column in spec.get("feature_columns", [])]
    base_columns = [str(column) for column in spec.get("base_feature_columns", [])]
    text_columns = [str(column) for column in spec.get("text_feature_columns", [])]
    if not feature_columns:
        raise ValueError(f"Feature spec has no feature_columns: {path}")
    return {
        "name": str(spec["name"]),
        "feature_columns": feature_columns,
        "base_feature_columns": base_columns,
        "text_feature_columns": text_columns,
        "feature_count": int(spec.get("feature_count", len(feature_columns))),
        "text_feature_count": len(text_columns),
        "selection_rule": str(spec.get("selection_rule", "")),
        "source_feature_spec": str(path.relative_to(FINIR_ROOT)),
    }


def _build_experiments(package_root: Path, seed: int) -> list[dict[str, Any]]:
    base_spec = _load_feature_spec(package_root / "feature_sets" / "base_macro.json")
    lean_spec = _load_feature_spec(
        package_root
        / "pre_ppo_diagnostics"
        / "feature_set_base_macro_plus_text_lean_v1.json"
    )
    action_spec = _load_feature_spec(
        package_root
        / "pre_ppo_diagnostics"
        / "action_primitive_text_diagnostics"
        / "feature_set_base_macro_plus_text_action_primitive_v1.json"
    )

    experiments = []
    for spec in [base_spec, lean_spec, action_spec]:
        output_name = f"{spec['name']}_seed{seed}"
        notebook_name = f"run_ppo_{spec['name']}_seed{seed}.ipynb"
        experiments.append(
            {
                **spec,
                "seed": int(seed),
                "output_dir": f"outputs/{output_name}",
                "notebook": f"notebooks/{notebook_name}",
                "purpose": (
                    "baseline"
                    if spec["name"] == "base_macro"
                    else "one_seed_text_screen"
                ),
            }
        )
    return experiments


def _make_manifest(output_dir: Path, package_root: Path, text_panel: Path, seed: int, timesteps: int) -> dict[str, Any]:
    experiments = _build_experiments(package_root, seed)
    return {
        "name": "huawei_ppo_text_screening_seed42",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "created_by": "features/prepare_huawei_ppo_screening_package.py",
        "description": (
            "One-seed PPO screening package for base_macro vs two selected "
            "FinIR/FinGPT text feature sets."
        ),
        "dataset": {
            "copy_name": "rl_panel_codex_rule_text_features.csv",
            "finportfolio_ir_relative_path": str(
                Path("data")
                / "exports"
                / "daily_retrieval_ppo_full_dis_legacy"
                / "rl_panel_codex_rule_text_features.csv"
            ),
            "local_source_path": str(text_panel.resolve()),
            "train_end_exclusive": "2021-10-01",
            "test_start_inclusive": "2021-10-01",
            "test_end_inclusive": "2023-03-01",
        },
        "ppo_runner": {
            "ablation_root_hint": "Ablation Ladder v2",
            "python_env_hint": "tensorflow",
            "runner_kwargs": {
                "base_config_name": "custom_custom",
                "candidate_feature_families": [],
                "seeds": [int(seed)],
                "total_timesteps": int(timesteps),
                "max_folds": None,
                "es_mode": "relaxed",
                "dropout_p": 0.1,
                "eval_freq": 8192,
                "checkpoint_freq": 4096,
                "checkpoint_selection_rule": "checkpoint_robust_score",
                "domain_reward_scaling": {},
                "action_regularization": {
                    "enabled": True,
                    "turnover_penalty": 0.01,
                    "smoothness_penalty": 0.03,
                    "concentration_penalty": 0.0,
                    "max_weight_penalty": 0.0,
                    "kl_to_previous_penalty": 0.0,
                    "normalize_penalties": True,
                    "train_only": True,
                },
                "verbose": 0,
            },
        },
        "screening_gate": {
            "baseline": "base_macro",
            "promote_to_three_seeds_if": [
                "daily_sharpe_ann_delta_vs_base > 0",
                "daily_cumulative_return_delta_vs_base > 0",
                "daily_max_drawdown no worse than base by more than 3 percentage points",
                "daily_turnover_mean no more than 25 percent above base",
            ],
        },
        "expected_outputs_per_experiment": [
            "walk_forward_results.csv",
            "unique_run_level_results.csv",
            "walk_forward_daily_test_returns.csv",
            "walk_forward_test_actions.csv",
            "walk_forward_test_observations.csv",
            "benchmark_suite_daily.csv",
            "artifact_index.json",
            "run_manifest.json",
        ],
        "post_run_outputs": [
            "ppo_ablation_results.csv",
            "ppo_ablation_daily_returns_combined.csv",
            "ppo_ablation_comparison.md",
        ],
        "experiments": experiments,
    }


def _notebook_payload(variant_name: str, title: str) -> dict[str, Any]:
    code = f'''from pathlib import Path
import sys

PROJECT_ROOT = Path.cwd().resolve()
PACKAGE_DIR = None
ABLATION_ROOT = None
DATASET_PATH = None
OUTPUT_ROOT = None
VARIANT_NAME = "{variant_name}"

def _find_helper(start: Path) -> Path:
    relative_package = Path(
        "FinPortfolio_IR",
        "data",
        "exports",
        "daily_retrieval_ppo_full_dis_legacy",
        "ppo_ablation_package",
        "huawei_screening_seed42",
        "huawei_ppo_screening_runtime.py",
    )
    candidates = []
    for root in [start, *list(start.parents)[:8]]:
        candidates.extend([
            root / "huawei_ppo_screening_runtime.py",
            root / "huawei_screening_seed42" / "huawei_ppo_screening_runtime.py",
            root / relative_package,
        ])
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    raise FileNotFoundError("Could not find huawei_ppo_screening_runtime.py")

helper_path = _find_helper(PROJECT_ROOT)
if str(helper_path.parent) not in sys.path:
    sys.path.insert(0, str(helper_path.parent))

from huawei_ppo_screening_runtime import run_screening_experiment

run_info = run_screening_experiment(
    VARIANT_NAME,
    project_root=PROJECT_ROOT,
    package_dir=PACKAGE_DIR,
    ablation_root=ABLATION_ROOT,
    dataset_path=DATASET_PATH,
    output_root=OUTPUT_ROOT,
)
run_info
'''
    return {
        "cells": [
            {
                "cell_type": "markdown",
                "metadata": {},
                "source": [
                    f"# {title}\n",
                    "\n",
                    "Run this notebook from the PPO project root on Huawei Cloud. Override PROJECT_ROOT, PACKAGE_DIR, ABLATION_ROOT, DATASET_PATH, or OUTPUT_ROOT in the first code cell only if auto-discovery fails.\n",
                ],
            },
            {
                "cell_type": "code",
                "execution_count": None,
                "metadata": {},
                "outputs": [],
                "source": [line + "\n" for line in code.splitlines()],
            },
        ],
        "metadata": {
            "kernelspec": {
                "display_name": "Python 3",
                "language": "python",
                "name": "python3",
            },
            "language_info": {
                "name": "python",
                "pygments_lexer": "ipython3",
            },
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }


def _write_notebooks(output_dir: Path, experiments: list[dict[str, Any]]) -> None:
    notebook_dir = output_dir / "notebooks"
    notebook_dir.mkdir(parents=True, exist_ok=True)
    for experiment in experiments:
        notebook_path = output_dir / str(experiment["notebook"])
        title = f"PPO Screening: {experiment['name']} seed {experiment['seed']}"
        _write_json(notebook_path, _notebook_payload(str(experiment["name"]), title))


def _copy_runtime_files(output_dir: Path) -> None:
    source_dir = Path(__file__).resolve().parent
    for name in ["huawei_ppo_screening_runtime.py", "compare_huawei_ppo_screening.py"]:
        shutil.copyfile(source_dir / name, output_dir / name)


def _write_readme(output_dir: Path, manifest: dict[str, Any]) -> None:
    experiment_lines = "\n".join(
        f"- `{experiment['name']}` -> `{experiment['notebook']}` -> `{experiment['output_dir']}`"
        for experiment in manifest["experiments"]
    )
    content = f"""# Huawei PPO Text Screening

This folder contains the one-seed Huawei Cloud screening run for:

{experiment_lines}

## What to copy to Huawei

Use one of these layouts:

1. Run from the full project root that contains `FinPortfolio_IR/` and `Ablation Ladder v2/`.
2. Or copy this `huawei_screening_seed42` folder plus `rl_panel_codex_rule_text_features.csv` into the PPO project root.

The notebooks auto-discover:

- `huawei_ppo_screening_manifest.json`
- `rl_panel_codex_rule_text_features.csv`
- `Ablation Ladder v2`

If auto-discovery fails, edit only the path variables at the top of the first code cell.

## Run Order

Run the three notebooks in parallel if the machine has enough resources:

1. `notebooks/run_ppo_base_macro_seed42.ipynb`
2. `notebooks/run_ppo_base_macro_plus_text_lean_v1_seed42.ipynb`
3. `notebooks/run_ppo_base_macro_plus_text_action_primitive_v1_seed42.ipynb`

Each notebook writes a separate directory under `outputs/`.

## Post-Run Comparison

After all notebooks finish:

```powershell
python compare_huawei_ppo_screening.py --package-dir .
```

This writes:

- `ppo_ablation_results.csv`
- `ppo_ablation_daily_returns_combined.csv`
- `ppo_ablation_comparison.md`

Promote a text variant to the 3-seed run only if it beats `base_macro` in this
one-seed screen without a clear drawdown or turnover regression.
"""
    (output_dir / "README.md").write_text(content, encoding="utf-8")


def build_package(output_dir: Path, package_root: Path, text_panel: Path, seed: int, timesteps: int) -> dict[str, Any]:
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "outputs").mkdir(exist_ok=True)

    manifest = _make_manifest(output_dir, package_root, text_panel, seed, timesteps)
    _write_json(output_dir / "huawei_ppo_screening_manifest.json", manifest)
    _write_csv(
        output_dir / "huawei_ppo_screening_manifest.csv",
        [
            {
                "name": experiment["name"],
                "seed": experiment["seed"],
                "feature_count": experiment["feature_count"],
                "text_feature_count": experiment["text_feature_count"],
                "notebook": experiment["notebook"],
                "output_dir": experiment["output_dir"],
                "purpose": experiment["purpose"],
            }
            for experiment in manifest["experiments"]
        ],
    )
    _write_notebooks(output_dir, manifest["experiments"])
    _copy_runtime_files(output_dir)
    _write_readme(output_dir, manifest)
    for experiment in manifest["experiments"]:
        run_dir = output_dir / str(experiment["output_dir"])
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / ".gitkeep").write_text("", encoding="utf-8")
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--package-root", type=Path, default=DEFAULT_PACKAGE_ROOT)
    parser.add_argument("--text-panel", type=Path, default=DEFAULT_TEXT_PANEL)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--timesteps", type=int, default=DEFAULT_TIMESTEPS)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest = build_package(
        output_dir=args.output_dir,
        package_root=args.package_root,
        text_panel=args.text_panel,
        seed=args.seed,
        timesteps=args.timesteps,
    )
    print(f"wrote Huawei PPO screening package to {args.output_dir.resolve()}")
    print(f"experiments: {len(manifest['experiments'])}")


if __name__ == "__main__":
    main()

