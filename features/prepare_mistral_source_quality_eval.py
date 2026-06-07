"""Prepare source-stratified source-quality rows for the Mistral comparison runner."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from finportfolio_ir.io_utils import read_jsonl, write_jsonl


MOJIBAKE_REPLACEMENTS = {
    "вЂ“": "-",
    "вЂ”": "-",
    "вЂ™": "'",
    "вЂ˜": "'",
    "вЂњ": '"',
    "вЂќ": '"',
    "вЂ¦": "...",
    "В ": " ",
}


def clean_for_prompt(value: Any) -> str:
    text = str(value or "")
    for bad, good in MOJIBAKE_REPLACEMENTS.items():
        text = text.replace(bad, good)
    return " ".join(text.split())


def convert_row(row: dict[str, Any]) -> dict[str, Any]:
    labels = row.get("rule_proxy_labels", {}) if isinstance(row.get("rule_proxy_labels"), dict) else {}
    decision_time = str(row.get("decision_time") or "")
    active_signals = labels.get("active_signal_rule_proxy", [])
    if not isinstance(active_signals, list):
        active_signals = []
    return {
        "teacher_id": str(row.get("eval_id") or row.get("daily_context_id") or row.get("doc_id") or ""),
        "feature_version": "source_quality_rule_proxy_v1",
        "daily_context_id": row.get("daily_context_id", ""),
        "doc_id": row.get("doc_id", ""),
        "decision_date": decision_time[:10],
        "decision_time": decision_time,
        "available_at": row.get("available_at", ""),
        "split": row.get("split", ""),
        "document_split": row.get("document_split", ""),
        "regime": row.get("regime", ""),
        "retrieval_layer": row.get("retrieval_layer", ""),
        "target_ticker": row.get("target_ticker", ""),
        "source_family": row.get("source_family", ""),
        "source_type": row.get("source_type", ""),
        "source": row.get("source", ""),
        "source_registry_id": row.get("source_registry_id", ""),
        "source_reliability_tier": row.get("source_reliability_tier", ""),
        "query_intent_primary": row.get("query_intent_primary", ""),
        "title": clean_for_prompt(row.get("title", "")),
        "url": row.get("url", ""),
        "excerpt": clean_for_prompt(row.get("excerpt", "")),
        "labels": {
            "impact_direction": labels.get("impact_direction_rule_proxy", "neutral"),
            "risk_intensity": labels.get("risk_intensity_rule_proxy", 0.0),
            "uncertainty_intensity": labels.get("uncertainty_intensity_rule_proxy", 0.0),
            "sentiment_proxy": labels.get("sentiment_proxy_rule_proxy", 0.0),
            "portfolio_action_relevance": labels.get("portfolio_action_relevance_rule_proxy", 0.0),
            "active_signals": sorted(str(item) for item in active_signals),
        },
        "teacher_confidence": "rule_proxy_not_ground_truth",
        "teacher_rationale": "Converted from deterministic source-quality rule proxy labels for Mistral source audit.",
        "comparison_use": "source_quality_mistral_vs_rule_proxy",
        "human_fields_to_fill": row.get("human_fields_to_fill", {}),
    }


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-quality-seed",
        type=Path,
        default=Path("data/exports/daily_retrieval_ppo_full_company_ir/source_quality_audit/source_quality_llm_eval_seed.jsonl"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/exports/daily_retrieval_ppo_full_company_ir/mistral_source_quality_eval"),
    )
    parser.add_argument("--model", default="mistral-small-latest")
    parser.add_argument("--base-url", default="https://api.mistral.ai/v1/chat/completions")
    parser.add_argument("--sleep-seconds", type=float, default=0.15)
    parser.add_argument("--timeout-seconds", type=int, default=90)
    parser.add_argument("--max-retries", type=int, default=3)
    args = parser.parse_args()

    rows = read_jsonl(args.source_quality_seed)
    converted = [convert_row(row) for row in rows]
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    seed_path = output_dir / "mistral_source_quality_teacher_seed.jsonl"
    write_jsonl(seed_path, converted)

    command = (
        "python -B .\\features\\run_mistral_teacher_seed_comparison.py "
        f"--seed {seed_path} "
        f"--output-dir {output_dir} "
        f"--model {args.model} "
        f"--base-url {args.base_url} "
        f"--sleep-seconds {args.sleep_seconds} "
        f"--timeout-seconds {args.timeout_seconds} "
        f"--max-retries {args.max_retries}"
    )
    dry_run_command = command + " --dry-run"
    write_text(output_dir / "run_mistral_source_quality_eval.ps1", command + "\n")
    write_text(output_dir / "dry_run_mistral_source_quality_eval.ps1", dry_run_command + "\n")
    write_text(
        output_dir / "README.md",
        "\n".join(
            [
                "# Mistral Source Quality Evaluation",
                "",
                "This folder contains a source-stratified Mistral evaluation seed converted from the source-quality audit.",
                "",
                "Important caveat: labels are deterministic rule-proxy labels, not human truth. Use the output to find source/prompt failure modes, then adjudicate disputed rows manually.",
                "",
                "Run a prompt preview:",
                "",
                "```powershell",
                dry_run_command,
                "```",
                "",
                "Run the API pass after `MISTRAL_API_KEY` or `LLM_API_KEY` is set:",
                "",
                "```powershell",
                command,
                "```",
                "",
                "Expected outputs:",
                "",
                "- `mistral_predictions.jsonl`",
                "- `comparison_rows.csv`",
                "- `comparison_summary.json`",
                "- `failed_rows.jsonl`",
                "- `prompt_preview.json` from dry-run",
            ]
        )
        + "\n",
    )
    config = {
        "source_quality_seed": str(args.source_quality_seed),
        "teacher_seed": str(seed_path),
        "rows": len(converted),
        "model": args.model,
        "base_url": args.base_url,
        "run_command": command,
        "dry_run_command": dry_run_command,
    }
    write_text(output_dir / "mistral_source_quality_eval_config.json", json.dumps(config, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(config, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
