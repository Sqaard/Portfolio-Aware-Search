"""Audit source quality for causal retrieval and LLM extraction readiness."""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from finportfolio_ir.io_utils import read_jsonl, write_jsonl
from finportfolio_ir.time_utils import parse_datetime


SIGNAL_PREFIX = "signal_"
REQUIRED_CONTEXT_FIELDS = (
    "doc_id",
    "document_hash",
    "duplicate_cluster_id",
    "source",
    "source_type",
    "source_registry_id",
    "source_reliability_tier",
    "url",
    "canonical_url",
    "published_at",
    "available_at",
    "decision_time",
    "matched_tickers",
    "matched_holdings",
    "title",
    "body_excerpt",
    "fetch_status",
)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        out = float(value)
        if math.isnan(out) or math.isinf(out):
            return default
        return out
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    return int(round(_safe_float(value, float(default))))


def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 6) if values else 0.0


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def source_family(source_type: str) -> str:
    if source_type.startswith("official_macro"):
        return "official_macro"
    if source_type.startswith("sec_filing"):
        return "sec_edgar"
    if source_type.startswith("company_"):
        return "company_ir"
    return "other"


def _field_present(row: dict[str, Any], field: str) -> bool:
    value = row.get(field)
    if value is None or value == "":
        return False
    if isinstance(value, list):
        return len(value) > 0
    return True


def provenance_completeness(row: dict[str, Any]) -> float:
    present = sum(1 for field in REQUIRED_CONTEXT_FIELDS if _field_present(row, field))
    return present / len(REQUIRED_CONTEXT_FIELDS)


def is_causal(row: dict[str, Any]) -> bool:
    try:
        return parse_datetime(str(row.get("available_at"))) <= parse_datetime(str(row.get("decision_time")))
    except Exception:
        return False


def load_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path or not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        fieldnames = []
        for row in rows:
            for key in row:
                if key not in fieldnames:
                    fieldnames.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _aggregate(rows: list[dict[str, Any]], key: str, features_by_context: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row.get(key) or "missing")].append(row)

    out: list[dict[str, Any]] = []
    for group_value, group_rows in groups.items():
        feature_rows = [features_by_context.get(str(row.get("daily_context_id")), {}) for row in group_rows]
        source_types = Counter(str(row.get("source_type") or "missing") for row in group_rows)
        families = Counter(source_family(str(row.get("source_type") or "")) for row in group_rows)
        signal_counts: list[float] = []
        non_neutral = 0
        for feat in feature_rows:
            signal_cols = [col for col in feat if col.startswith(SIGNAL_PREFIX)]
            signal_counts.append(sum(_safe_float(feat.get(col)) for col in signal_cols))
            if str(feat.get("impact_direction") or "neutral") != "neutral":
                non_neutral += 1

        context_count = len(group_rows)
        unique_docs = {str(row.get("doc_id") or "") for row in group_rows}
        train_rows = sum(1 for row in group_rows if str(row.get("split")) == "train")
        test_rows = sum(1 for row in group_rows if str(row.get("split")) == "test")
        causal_rows = sum(1 for row in group_rows if is_causal(row))
        duplicate_clusters = {str(row.get("duplicate_cluster_id") or row.get("doc_id") or "") for row in group_rows}
        avg_signal_count = _mean(signal_counts)
        provenance_score = _mean([provenance_completeness(row) for row in group_rows])
        causal_score = _ratio(causal_rows, context_count)
        retrieval_score = _mean([_safe_float(row.get("final_score")) for row in group_rows])
        extraction_proxy_score = round(
            0.35 * min(avg_signal_count / 5.0, 1.0)
            + 0.20 * _ratio(non_neutral, context_count)
            + 0.20 * _mean([_safe_float(feat.get("portfolio_action_relevance")) for feat in feature_rows])
            + 0.15 * _mean([_safe_float(feat.get("risk_intensity")) for feat in feature_rows])
            + 0.10 * _mean([_safe_float(feat.get("uncertainty_intensity")) for feat in feature_rows]),
            6,
        )
        source_integrity_score = round(0.65 * provenance_score + 0.25 * causal_score + 0.10 * min(len(unique_docs) / 100.0, 1.0), 6)
        out.append(
            {
                key: group_value,
                "source_family_top": families.most_common(1)[0][0] if families else "",
                "source_type_top": source_types.most_common(1)[0][0] if source_types else "",
                "context_rows": context_count,
                "unique_docs": len(unique_docs),
                "train_rows": train_rows,
                "test_rows": test_rows,
                "test_share": _ratio(test_rows, context_count),
                "causal_valid_rate": causal_score,
                "provenance_completeness": round(provenance_score, 6),
                "avg_final_score": retrieval_score,
                "avg_age_days": _mean([_safe_float(row.get("age_days")) for row in group_rows]),
                "duplicate_cluster_count": len(duplicate_clusters),
                "duplicate_context_share": round(1.0 - len(duplicate_clusters) / context_count, 6) if context_count else 0.0,
                "avg_signal_count_rule_proxy": avg_signal_count,
                "non_neutral_share_rule_proxy": _ratio(non_neutral, context_count),
                "avg_risk_intensity_rule_proxy": _mean([_safe_float(feat.get("risk_intensity")) for feat in feature_rows]),
                "avg_uncertainty_intensity_rule_proxy": _mean([_safe_float(feat.get("uncertainty_intensity")) for feat in feature_rows]),
                "avg_action_relevance_rule_proxy": _mean([_safe_float(feat.get("portfolio_action_relevance")) for feat in feature_rows]),
                "source_integrity_score": source_integrity_score,
                "extraction_readiness_proxy_score": extraction_proxy_score,
                "audit_note": "Proxy only; validate with source-stratified LLM/human labels before promoting to PPO.",
            }
        )
    out.sort(key=lambda row: (row["source_integrity_score"], row["extraction_readiness_proxy_score"], row["context_rows"]), reverse=True)
    return out


def _mistral_rows_by_source(comparison_rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in comparison_rows:
        groups[str(row.get("source_type") or "missing")].append(row)
    out: list[dict[str, Any]] = []
    for source_type, rows in groups.items():
        out.append(
            {
                "source_type": source_type,
                "mistral_eval_rows": len(rows),
                "impact_direction_accuracy_vs_codex_teacher": _mean([_safe_float(row.get("impact_direction_match")) for row in rows]),
                "signal_f1_vs_codex_teacher": _mean([_safe_float(row.get("signal_f1")) for row in rows]),
                "signal_precision_vs_codex_teacher": _mean([_safe_float(row.get("signal_precision")) for row in rows]),
                "signal_recall_vs_codex_teacher": _mean([_safe_float(row.get("signal_recall")) for row in rows]),
                "risk_mae_vs_codex_teacher": _mean([_safe_float(row.get("abs_error_risk_intensity")) for row in rows]),
                "uncertainty_mae_vs_codex_teacher": _mean([_safe_float(row.get("abs_error_uncertainty_intensity")) for row in rows]),
                "action_relevance_mae_vs_codex_teacher": _mean([_safe_float(row.get("abs_error_portfolio_action_relevance")) for row in rows]),
                "eval_caveat": "Comparison is against Codex-rule teacher, not human truth.",
            }
        )
    out.sort(key=lambda row: (row["mistral_eval_rows"], row["signal_f1_vs_codex_teacher"]), reverse=True)
    return out


def build_llm_eval_seed(
    contexts: list[dict[str, Any]],
    features_by_context: dict[str, dict[str, str]],
    *,
    per_source_type: int,
) -> list[dict[str, Any]]:
    by_source_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in contexts:
        by_source_type[str(row.get("source_type") or "missing")].append(row)

    selected: list[dict[str, Any]] = []
    for source_type, rows in sorted(by_source_type.items()):
        rows = sorted(
            rows,
            key=lambda row: (
                str(row.get("split") or ""),
                str(row.get("query_intent_primary") or ""),
                _safe_float(row.get("final_score")),
                -_safe_float(row.get("age_days")),
            ),
            reverse=True,
        )
        seen_docs: set[str] = set()
        source_selected: list[dict[str, Any]] = []
        for row in rows:
            doc_id = str(row.get("doc_id") or "")
            if doc_id in seen_docs:
                continue
            seen_docs.add(doc_id)
            feat = features_by_context.get(str(row.get("daily_context_id")), {})
            signal_cols = [col for col in feat if col.startswith(SIGNAL_PREFIX)]
            labels = {
                "impact_direction_rule_proxy": feat.get("impact_direction", ""),
                "risk_intensity_rule_proxy": _safe_float(feat.get("risk_intensity")),
                "uncertainty_intensity_rule_proxy": _safe_float(feat.get("uncertainty_intensity")),
                "sentiment_proxy_rule_proxy": _safe_float(feat.get("sentiment_proxy")),
                "portfolio_action_relevance_rule_proxy": _safe_float(feat.get("portfolio_action_relevance")),
                "active_signal_rule_proxy": sorted([col for col in signal_cols if _safe_float(feat.get(col)) > 0]),
            }
            source_selected.append(
                {
                    "eval_id": f"source_quality:{source_type}:{len(source_selected)+1:03d}",
                    "daily_context_id": row.get("daily_context_id"),
                    "doc_id": row.get("doc_id"),
                    "source_family": source_family(source_type),
                    "source_type": source_type,
                    "source": row.get("source"),
                    "source_registry_id": row.get("source_registry_id"),
                    "source_reliability_tier": row.get("source_reliability_tier"),
                    "retrieval_layer": row.get("retrieval_layer"),
                    "target_ticker": row.get("target_ticker"),
                    "query_intent_primary": row.get("query_intent_primary"),
                    "split": row.get("split"),
                    "document_split": row.get("document_split"),
                    "regime": row.get("regime"),
                    "decision_time": row.get("decision_time"),
                    "available_at": row.get("available_at"),
                    "title": row.get("title"),
                    "url": row.get("url"),
                    "excerpt": row.get("body_excerpt"),
                    "rule_proxy_labels": labels,
                    "human_fields_to_fill": {
                        "is_relevant": "",
                        "is_causally_safe": "",
                        "llm_extraction_correctness": "",
                        "useful_signals": "",
                        "notes": "",
                    },
                }
            )
            if len(source_selected) >= per_source_type:
                break
        selected.extend(source_selected)
    return selected


def write_markdown(
    path: Path,
    *,
    source_type_rows: list[dict[str, Any]],
    source_family_rows: list[dict[str, Any]],
    mistral_rows: list[dict[str, Any]],
    seed_rows: list[dict[str, Any]],
) -> None:
    lines = [
        "# Source Quality Audit",
        "",
        "This report separates source integrity from extraction quality. Rule-based feature metrics are proxy diagnostics, not ground truth.",
        "",
        "## Source Families",
        "",
        "| source_family | contexts | unique_docs | integrity | extraction_proxy | provenance | causal |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in source_family_rows:
        lines.append(
            f"| {row['source_family']} | {row['context_rows']} | {row['unique_docs']} | "
            f"{row['source_integrity_score']:.3f} | {row['extraction_readiness_proxy_score']:.3f} | "
            f"{row['provenance_completeness']:.3f} | {row['causal_valid_rate']:.3f} |"
        )
    lines.extend(["", "## Source Types", "", "| source_type | contexts | unique_docs | integrity | extraction_proxy | avg signals | non-neutral |", "|---|---:|---:|---:|---:|---:|---:|"])
    for row in source_type_rows:
        lines.append(
            f"| {row['source_type']} | {row['context_rows']} | {row['unique_docs']} | "
            f"{row['source_integrity_score']:.3f} | {row['extraction_readiness_proxy_score']:.3f} | "
            f"{row['avg_signal_count_rule_proxy']:.2f} | {row['non_neutral_share_rule_proxy']:.3f} |"
        )
    lines.extend(["", "## Existing Mistral-vs-Codex Teacher Evidence", ""])
    if mistral_rows:
        lines.extend(["| source_type | rows | signal_f1 | impact_acc | risk_mae | caveat |", "|---|---:|---:|---:|---:|---|"])
        for row in mistral_rows:
            lines.append(
                f"| {row['source_type']} | {row['mistral_eval_rows']} | {row['signal_f1_vs_codex_teacher']:.3f} | "
                f"{row['impact_direction_accuracy_vs_codex_teacher']:.3f} | {row['risk_mae_vs_codex_teacher']:.3f} | teacher, not truth |"
            )
    else:
        lines.append("No Mistral comparison rows were provided.")
    lines.extend(
        [
            "",
            "## Next LLM Evaluation",
            "",
            f"Prepared `{len(seed_rows)}` source-stratified rows for LLM/human evaluation.",
            "Use this seed to compare Mistral/Codex/FinBERT/lexicon by source_type before promoting any source family into PPO features.",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_audit(args: argparse.Namespace) -> dict[str, Any]:
    contexts = read_jsonl(args.contexts)
    feature_rows = load_csv_rows(args.doc_features)
    features_by_context = {str(row.get("daily_context_id") or ""): row for row in feature_rows}
    comparison_rows = load_csv_rows(args.mistral_comparison) if args.mistral_comparison else []

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    source_type_rows = _aggregate(contexts, "source_type", features_by_context)
    source_family_rows = _aggregate(
        [dict(row, source_family=source_family(str(row.get("source_type") or ""))) for row in contexts],
        "source_family",
        features_by_context,
    )
    source_rows = _aggregate(contexts, "source", features_by_context)
    registry_rows = _aggregate(contexts, "source_registry_id", features_by_context)
    mistral_rows = _mistral_rows_by_source(comparison_rows)
    seed_rows = build_llm_eval_seed(contexts, features_by_context, per_source_type=args.seed_per_source_type)

    write_csv(output_dir / "source_quality_by_source_type.csv", source_type_rows)
    write_csv(output_dir / "source_quality_by_source_family.csv", source_family_rows)
    write_csv(output_dir / "source_quality_by_source.csv", source_rows)
    write_csv(output_dir / "source_quality_by_source_registry_id.csv", registry_rows)
    write_csv(output_dir / "mistral_vs_codex_by_source_type.csv", mistral_rows)
    write_jsonl(output_dir / "source_quality_llm_eval_seed.jsonl", seed_rows)
    write_markdown(
        output_dir / "SOURCE_QUALITY_AUDIT.md",
        source_type_rows=source_type_rows,
        source_family_rows=source_family_rows,
        mistral_rows=mistral_rows,
        seed_rows=seed_rows,
    )

    summary = {
        "contexts": str(args.contexts),
        "doc_features": str(args.doc_features),
        "mistral_comparison": str(args.mistral_comparison) if args.mistral_comparison else "",
        "context_rows": len(contexts),
        "feature_rows": len(feature_rows),
        "source_type_count": len(source_type_rows),
        "source_family_count": len(source_family_rows),
        "llm_eval_seed_rows": len(seed_rows),
        "outputs": {
            "source_quality_by_source_type": str(output_dir / "source_quality_by_source_type.csv"),
            "source_quality_by_source_family": str(output_dir / "source_quality_by_source_family.csv"),
            "source_quality_by_source": str(output_dir / "source_quality_by_source.csv"),
            "source_quality_by_source_registry_id": str(output_dir / "source_quality_by_source_registry_id.csv"),
            "mistral_vs_codex_by_source_type": str(output_dir / "mistral_vs_codex_by_source_type.csv"),
            "source_quality_llm_eval_seed": str(output_dir / "source_quality_llm_eval_seed.jsonl"),
            "markdown": str(output_dir / "SOURCE_QUALITY_AUDIT.md"),
        },
    }
    (output_dir / "source_quality_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contexts", required=True, type=Path)
    parser.add_argument("--doc-features", required=True, type=Path)
    parser.add_argument("--mistral-comparison", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=Path("data/exports/source_quality_audit"))
    parser.add_argument("--seed-per-source-type", type=int, default=20)
    args = parser.parse_args()
    summary = run_audit(args)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
