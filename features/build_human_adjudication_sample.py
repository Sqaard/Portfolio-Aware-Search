"""Build a human adjudication sample from Mistral-vs-Codex comparison rows."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from features.build_text_feature_baseline import SIGNAL_COLUMNS  # noqa: E402


DEFAULT_SEED = "data/exports/daily_retrieval_ppo_full_dis_legacy/codex_rule_text_features/codex_teacher_seed.jsonl"
DEFAULT_COMPARISON = "data/exports/mistral_vs_codex_seed/comparison_rows.csv"
DEFAULT_PREDICTIONS = "data/exports/mistral_vs_codex_seed/mistral_predictions.jsonl"
DEFAULT_OUTPUT = "data/exports/mistral_vs_codex_seed/human_adjudication_sample_top50.csv"
DEFAULT_GUIDE = "data/exports/mistral_vs_codex_seed/human_adjudication_guide.md"


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def disagreement_score(row: dict[str, Any]) -> float:
    impact_penalty = 2.0 if str(row.get("impact_direction_match", "")) != "1" else 0.0
    signal_penalty = 1.75 * (1.0 - _safe_float(row.get("signal_f1"), 1.0))
    fn_fp_penalty = 0.12 * (_safe_int(row.get("signal_fn")) + _safe_int(row.get("signal_fp")))
    action_penalty = 1.00 * min(1.0, _safe_float(row.get("abs_error_portfolio_action_relevance")))
    risk_penalty = 0.70 * min(1.0, _safe_float(row.get("abs_error_risk_intensity")))
    uncertainty_penalty = 0.45 * min(1.0, _safe_float(row.get("abs_error_uncertainty_intensity")))
    sentiment_penalty = 0.25 * min(1.0, _safe_float(row.get("abs_error_sentiment_proxy")))
    return round(
        impact_penalty
        + signal_penalty
        + fn_fp_penalty
        + action_penalty
        + risk_penalty
        + uncertainty_penalty
        + sentiment_penalty,
        6,
    )


def disagreement_reasons(row: dict[str, Any]) -> str:
    reasons: list[str] = []
    if str(row.get("impact_direction_match", "")) != "1":
        reasons.append("impact_direction_mismatch")
    if _safe_float(row.get("signal_f1"), 1.0) < 0.5:
        reasons.append("low_signal_f1")
    if _safe_int(row.get("signal_fn")) >= 3:
        reasons.append("many_missing_signals")
    if _safe_int(row.get("signal_fp")) >= 2:
        reasons.append("many_extra_signals")
    if _safe_float(row.get("abs_error_portfolio_action_relevance")) >= 0.4:
        reasons.append("high_action_relevance_gap")
    if _safe_float(row.get("abs_error_risk_intensity")) >= 0.3:
        reasons.append("high_risk_gap")
    if _safe_float(row.get("abs_error_uncertainty_intensity")) >= 0.4:
        reasons.append("high_uncertainty_gap")
    return "|".join(reasons) or "moderate_combined_disagreement"


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _read_jsonl_by_id(path: Path, key: str) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            value = str(row.get(key, ""))
            if value:
                rows[value] = row
    return rows


def build_sample(
    *,
    seed_path: Path,
    comparison_path: Path,
    predictions_path: Path,
    output_path: Path,
    guide_path: Path,
    sample_size: int,
    stratify_column: str = "",
    min_per_stratum: int = 0,
) -> dict[str, Any]:
    seed_by_id = _read_jsonl_by_id(seed_path, "teacher_id")
    predictions_by_id = _read_jsonl_by_id(predictions_path, "teacher_id")
    comparison_rows = _read_csv(comparison_path)

    scored_rows = sorted(
        comparison_rows,
        key=lambda row: (
            -disagreement_score(row),
            str(row.get("query_intent_primary", "")),
            str(row.get("teacher_id", "")),
        ),
    )
    selection_note = "top rows by deterministic disagreement_score"
    if stratify_column and min_per_stratum > 0:
        grouped_rows: dict[str, list[dict[str, str]]] = {}
        for row in scored_rows:
            teacher_id = str(row.get("teacher_id", ""))
            seed = seed_by_id.get(teacher_id, {})
            stratum = str(row.get(stratify_column) or seed.get(stratify_column) or "missing")
            grouped_rows.setdefault(stratum, []).append(row)

        selected: list[dict[str, str]] = []
        selected_ids: set[str] = set()
        for stratum in sorted(grouped_rows):
            for row in grouped_rows[stratum][:min_per_stratum]:
                teacher_id = str(row.get("teacher_id", ""))
                if teacher_id in selected_ids:
                    continue
                selected.append(row)
                selected_ids.add(teacher_id)
                if len(selected) >= sample_size:
                    break
            if len(selected) >= sample_size:
                break

        for row in scored_rows:
            teacher_id = str(row.get("teacher_id", ""))
            if teacher_id in selected_ids:
                continue
            selected.append(row)
            selected_ids.add(teacher_id)
            if len(selected) >= sample_size:
                break
        ranked = selected[:sample_size]
        selection_note = (
            f"top rows by disagreement_score with at least {min_per_stratum} "
            f"rows per `{stratify_column}` where available"
        )
    else:
        ranked = scored_rows[:sample_size]

    output_rows: list[dict[str, Any]] = []
    for rank, row in enumerate(ranked, start=1):
        teacher_id = str(row.get("teacher_id", ""))
        seed = seed_by_id.get(teacher_id, {})
        prediction = predictions_by_id.get(teacher_id, {})
        mistral_labels = prediction.get("mistral_labels", {}) if isinstance(prediction.get("mistral_labels"), dict) else {}
        labels = seed.get("labels", {}) if isinstance(seed.get("labels"), dict) else {}
        output_rows.append(
            {
                "adjudication_rank": rank,
                "disagreement_score": disagreement_score(row),
                "disagreement_reasons": disagreement_reasons(row),
                "teacher_id": teacher_id,
                "doc_id": row.get("doc_id", ""),
                "document_split": row.get("document_split", seed.get("document_split", "")),
                "regime": row.get("regime", seed.get("regime", "")),
                "retrieval_layer": row.get("retrieval_layer", seed.get("retrieval_layer", "")),
                "source_family": row.get("source_family", seed.get("source_family", "")),
                "source_type": row.get("source_type", seed.get("source_type", "")),
                "source": row.get("source", seed.get("source", "")),
                "source_registry_id": row.get("source_registry_id", seed.get("source_registry_id", "")),
                "source_reliability_tier": row.get("source_reliability_tier", seed.get("source_reliability_tier", "")),
                "query_intent_primary": row.get("query_intent_primary", seed.get("query_intent_primary", "")),
                "target_ticker": row.get("target_ticker", seed.get("target_ticker", "")),
                "title": seed.get("title", ""),
                "excerpt": seed.get("excerpt", ""),
                "codex_impact_direction": row.get("gold_impact_direction", labels.get("impact_direction", "")),
                "mistral_impact_direction": row.get("mistral_impact_direction", mistral_labels.get("impact_direction", "")),
                "codex_active_signals": row.get("gold_active_signals", "|".join(labels.get("active_signals", []))),
                "mistral_active_signals": row.get("mistral_active_signals", "|".join(mistral_labels.get("active_signals", []))),
                "missing_signals_from_mistral": row.get("missing_signals", ""),
                "extra_signals_from_mistral": row.get("extra_signals", ""),
                "codex_risk_intensity": row.get("gold_risk_intensity", labels.get("risk_intensity", "")),
                "mistral_risk_intensity": row.get("mistral_risk_intensity", mistral_labels.get("risk_intensity", "")),
                "codex_uncertainty_intensity": row.get("gold_uncertainty_intensity", labels.get("uncertainty_intensity", "")),
                "mistral_uncertainty_intensity": row.get("mistral_uncertainty_intensity", mistral_labels.get("uncertainty_intensity", "")),
                "codex_sentiment_proxy": row.get("gold_sentiment_proxy", labels.get("sentiment_proxy", "")),
                "mistral_sentiment_proxy": row.get("mistral_sentiment_proxy", mistral_labels.get("sentiment_proxy", "")),
                "codex_action_relevance": row.get("gold_portfolio_action_relevance", labels.get("portfolio_action_relevance", "")),
                "mistral_action_relevance": row.get("mistral_portfolio_action_relevance", mistral_labels.get("portfolio_action_relevance", "")),
                "mistral_rationale": mistral_labels.get("rationale", ""),
                "codex_rationale": " | ".join(seed.get("teacher_rationale", [])) if isinstance(seed.get("teacher_rationale"), list) else seed.get("teacher_rationale", ""),
                "human_preferred_source": "",
                "human_impact_direction": "",
                "human_active_signals": "",
                "human_risk_intensity": "",
                "human_uncertainty_intensity": "",
                "human_sentiment_proxy": "",
                "human_action_relevance": "",
                "human_notes": "",
                "human_done": "",
            }
        )

    fieldnames = list(output_rows[0].keys()) if output_rows else []
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(output_rows)

    guide_path.parent.mkdir(parents=True, exist_ok=True)
    guide_path.write_text(build_guide(output_path, sample_size, len(comparison_rows), selection_note), encoding="utf-8")
    return {
        "output": str(output_path),
        "guide": str(guide_path),
        "sample_rows": len(output_rows),
        "source_comparison_rows": len(comparison_rows),
        "selection": selection_note,
    }


def build_guide(output_path: Path, sample_size: int, comparison_rows: int, selection_note: str) -> str:
    signals = "\n".join(f"- `{signal}`" for signal in SIGNAL_COLUMNS)
    return f"""# Human Adjudication Guide

File to annotate:

`{output_path}`

You are reviewing the top {sample_size} Mistral-vs-Codex disagreements from
{comparison_rows} comparison rows. The goal is not to reward either system.
The goal is to write the best label for future FinIR/FinGPT/PPO text features.

Selection rule: {selection_note}.

## Where the Rows Came From

The full daily retrieval package has many causal contexts. We do not want to
send all of them to an API or manually label all of them first. So FinIR builds
small stratified teacher seeds from the full retrieval package.

For the broad daily retrieval QA pass, this was a 300-row seed. For the current
source-quality pass, it is a source-stratified seed that intentionally balances
company IR, SEC EDGAR, and official macro rows. This file selects the most
disputed rows from the selected comparison.

## What To Fill

Fill only these columns:

- `human_preferred_source`: `codex`, `mistral`, `hybrid`, or `neither`
- `human_impact_direction`: `positive`, `negative`, `neutral`, or `mixed`
- `human_active_signals`: pipe-separated signal names, for example
  `signal_company_risk|signal_credit`
- `human_risk_intensity`: number from 0 to 1
- `human_uncertainty_intensity`: number from 0 to 1
- `human_sentiment_proxy`: number from -1 to 1
- `human_action_relevance`: number from 0 to 1
- `human_notes`: short explanation if useful
- `human_done`: `yes`

## Scale

- Risk: `0.0` none, `0.3` mild, `0.6` material, `0.9` severe.
- Uncertainty: `0.0` none, `0.3` some, `0.6` meaningful, `0.9` high.
- Action relevance: `0.2` background, `0.5` useful context, `0.8` important,
  `1.0` highly actionable.
- Sentiment proxy: `-1` very negative, `0` neutral, `1` very positive.

## Allowed Signals

{signals}

## Practical Rule

Read `title` and `excerpt`. Use Codex/Mistral labels as suggestions, not truth.
If a signal is clearly mentioned or strongly implied by the section/source
metadata, include it. If both systems overreach, choose `neither` and write a
cleaner label.
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build top-disagreement human adjudication sample.")
    parser.add_argument("--seed", default=DEFAULT_SEED)
    parser.add_argument("--comparison", default=DEFAULT_COMPARISON)
    parser.add_argument("--predictions", default=DEFAULT_PREDICTIONS)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--guide-output", default=DEFAULT_GUIDE)
    parser.add_argument("--sample-size", type=int, default=50)
    parser.add_argument("--stratify-column", default="", help="Optional column for balanced high-disagreement sampling.")
    parser.add_argument("--min-per-stratum", type=int, default=0, help="Minimum rows to take from each stratum before global fill.")
    args = parser.parse_args(argv)

    summary = build_sample(
        seed_path=Path(args.seed),
        comparison_path=Path(args.comparison),
        predictions_path=Path(args.predictions),
        output_path=Path(args.output),
        guide_path=Path(args.guide_output),
        sample_size=args.sample_size,
        stratify_column=args.stratify_column,
        min_per_stratum=args.min_per_stratum,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
