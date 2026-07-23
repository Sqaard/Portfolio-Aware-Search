"""Build a methodology closure and monitoring report for FinPortfolio IR.

The report is deliberately file/artifact based. It does not declare that the
system is a finished production service; it declares whether the current local
methodology stages have enough auditable artifacts to be considered closed for
this research/demo checkpoint.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from crawler.source_registry import validate_source_registry


DEFAULT_OUTPUT_DIR = Path("data/exports/methodology_closure_v1")


@dataclass(frozen=True)
class PhaseStatus:
    phase_id: str
    title: str
    status: str
    metric: str
    artifact: str
    evidence: str
    risk: str
    next_action: str


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return {}


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _count_csv_rows(path: Path) -> int:
    return len(_read_csv_rows(path))


def _count_jsonl_rows(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


def _first_csv_row(path: Path) -> dict[str, str]:
    rows = _read_csv_rows(path)
    return rows[0] if rows else {}


def _clean(value: Any, default: str = "unknown") -> str:
    text = str(value or "").strip()
    return text if text else default


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value or "").strip())
    except ValueError:
        return default


def _closed(condition: bool) -> str:
    return "closed" if condition else "open"


def _file_metric(path: Path) -> str:
    if not path.exists():
        return "missing"
    return f"present:{path.stat().st_size} bytes"


def build_phase_status(project_root: Path) -> list[PhaseStatus]:
    registry_path = project_root / "data/source_registry/source_registry.csv"
    registry_errors = validate_source_registry(registry_path) if registry_path.exists() else ["missing registry"]
    registry_rows = _count_csv_rows(registry_path)

    corpus_path = project_root / "data/processed_documents/sec_macro_company_ir_ppo_2010_2023_documents.jsonl"
    corpus_rows = _count_jsonl_rows(corpus_path)

    evidence_summary_path = (
        project_root / "data/exports/document_vs_evidence_units_company_ir_v1/evidence_units_summary.json"
    )
    evidence_summary = _read_json(evidence_summary_path)
    evidence_units = int(evidence_summary.get("evidence_unit_count") or 0)
    missing_available = int(evidence_summary.get("missing_available_at") or 0)
    missing_hash = int(evidence_summary.get("missing_document_hash") or 0)

    index_path = project_root / "data/search_index/finportfolio_search.sqlite"
    baseline_metrics_path = project_root / "data/exports/ablation_batch_sample/ablation_metrics_by_method.csv"
    baseline_metrics_rows = _count_csv_rows(baseline_metrics_path)

    promotion_path = (
        project_root
        / "data/exports/document_vs_evidence_units_company_ir_holdout_v1"
        / "promotion_gate_mixed_v1/evidence_unit_promotion_mixed_v1_acceptance.csv"
    )
    promotion = _first_csv_row(promotion_path)
    promotion_accepted = _clean(promotion.get("decision")) == "accept_guarded_promotion"
    promotion_delta = _float(promotion.get("delta_ndcg_at_10"))
    promotion_precision_delta = _float(promotion.get("delta_precision_at_10"))

    qrels_path = project_root / "data/annotations/search_quality_qrels_mixed_v10.csv"
    qrels_rows = _count_csv_rows(qrels_path)

    web_app_path = project_root / "web_app.py"
    web_app_text = web_app_path.read_text(encoding="utf-8", errors="ignore") if web_app_path.exists() else ""
    llm_markers = all(
        marker in web_app_text
        for marker in (
            "/api/search/folder-analysis",
            "_call_llm_for_document_summary",
            "/api/chart-lab/analyze",
            "/api/portfolio/analyze",
        )
    )

    handoff_manifest_path = project_root / "data/exports/fingpt_handoff_sample/handoff_manifest.json"
    handoff_validation_path = project_root / "data/exports/fingpt_handoff_sample/handoff_validation.json"
    handoff_manifest = _read_json(handoff_manifest_path)
    handoff_validation = _read_json(handoff_validation_path)
    handoff_ok = handoff_manifest.get("status") == "passed" and handoff_validation.get("contexts", {}).get("status") == "passed"

    smoke_path = project_root / "data/exports/live_search_smoke_v1/live_search_smoke_summary.csv"
    smoke = _first_csv_row(smoke_path)
    smoke_ok = _clean(smoke.get("status")) == "passed" and _clean(smoke.get("failed_count"), "1") == "0"

    skill_summary_path = project_root / "data/exports/skillopt_ir_loops_v1/run_summary.json"
    skill_summary = _read_json(skill_summary_path)
    skill_score = _float(skill_summary.get("final_score", {}).get("ratio"))
    skill_ok = int(skill_summary.get("accepted_loops") or 0) >= 5 and skill_score >= 1.0

    return [
        PhaseStatus(
            "1",
            "Source Registry",
            _closed(registry_rows > 0 and not registry_errors),
            f"registry_rows={registry_rows}; errors={len(registry_errors)}",
            str(registry_path),
            "source cards validate and keep favorite sites separate from credibility",
            "; ".join(registry_errors[:3]) if registry_errors else "none",
            "continue source-card expansion only through validator",
        ),
        PhaseStatus(
            "2",
            "Crawler And Normalized Corpus",
            _closed(corpus_rows > 0 and (project_root / "crawler/live_incremental_fetch.py").exists()),
            f"normalized_rows={corpus_rows}",
            str(corpus_path),
            "historical corpus plus live incremental adapter exist",
            "live adapter must remain small and official-source only",
            "monitor fetch errors and freshness windows",
        ),
        PhaseStatus(
            "3",
            "Evidence Ledger",
            _closed(evidence_units > corpus_rows and missing_available == 0 and missing_hash == 0),
            f"evidence_units={evidence_units}; missing_available_at={missing_available}; missing_hash={missing_hash}",
            str(evidence_summary_path),
            "documents are split into auditable evidence units",
            "evidence-unit quality depends on source/type calibration",
            "keep parent source document IDs in every unit",
        ),
        PhaseStatus(
            "4",
            "Indexing",
            _closed(index_path.exists() and index_path.stat().st_size > 0),
            _file_metric(index_path),
            str(index_path),
            "SQLite FTS/BM25 index is present for fast exact financial search",
            "index freshness must be monitored after live updates",
            "rebuild index after accepted live backfills",
        ),
        PhaseStatus(
            "5",
            "Baseline Retrieval",
            _closed(baseline_metrics_rows > 0),
            f"metrics_rows={baseline_metrics_rows}",
            str(baseline_metrics_path),
            "BM25/hybrid baselines have measured retrieval metrics",
            "older baseline artifact is a reference, not final gold",
            "rerun when qrels or corpus changes",
        ),
        PhaseStatus(
            "6",
            "Hybrid Ranking And Promotion Gate",
            _closed(promotion_accepted and promotion_delta >= 0.02 and promotion_precision_delta >= -0.02),
            f"decision={_clean(promotion.get('decision'))}; delta_ndcg_at_10={promotion_delta:.6f}; delta_precision_at_10={promotion_precision_delta:.6f}",
            str(promotion_path),
            "calibrated, intent-guarded evidence-unit path passed mixed-qrels gate",
            "mixed qrels are not a fully human-labeled benchmark",
            "grow independent human labels before learned reranking",
        ),
        PhaseStatus(
            "7",
            "LLM Analysis Layer",
            _closed(web_app_path.exists() and llm_markers),
            f"web_app={_file_metric(web_app_path)}; markers={llm_markers}",
            str(web_app_path),
            "LLM analysis is downstream of retrieval and source citations",
            "avoid synchronous all-document LLM jobs in live paths",
            "cache summaries and keep prompts schema-bound",
        ),
        PhaseStatus(
            "8",
            "Evaluation And Qrels",
            _closed(qrels_rows >= 100),
            f"mixed_qrels_rows={qrels_rows}",
            str(qrels_path),
            "retrieval changes are evaluated with qrels/NDCG/MRR/Precision",
            "qrels include mixed provenance; report label sources honestly",
            "expand human qrels beyond spot-check labels",
        ),
        PhaseStatus(
            "9",
            "FinGPT Handoff",
            _closed(handoff_ok),
            f"manifest={_clean(handoff_manifest.get('status'))}; contexts={_clean(handoff_validation.get('contexts', {}).get('status'))}",
            str(handoff_manifest_path),
            "retrieved contexts and evidence bundles validate for downstream feature extraction",
            "sample handoff is small; PPO handoff should be separately validated",
            "run handoff smoke after schema changes",
        ),
        PhaseStatus(
            "10",
            "Production Monitoring",
            _closed(smoke_ok and promotion_accepted and not registry_errors),
            f"smoke={_clean(smoke.get('status'))}; passed={_clean(smoke.get('passed_count'))}/{_clean(smoke.get('case_count'))}; max_latency_ms={_clean(smoke.get('max_latency_ms'))}",
            str(smoke_path),
            "live smoke, promotion provenance, and registry validity are monitored together",
            "monitoring is artifact-based, not a full production observability stack",
            "add scheduled smoke history before long public demos",
        ),
        PhaseStatus(
            "11",
            "Self-Improvement Loop",
            _closed(skill_ok),
            f"accepted_loops={skill_summary.get('accepted_loops', 0)}; skill_score={skill_score:.4f}",
            str(skill_summary_path),
            "agent skill is updated through gated SkillLens/SkillOpt-style loops",
            "loop validates procedure, not model correctness",
            "rerun loops after every methodology stage",
        ),
    ]


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def build_markdown(phases: list[PhaseStatus], summary: dict[str, Any]) -> str:
    lines = [
        "# FinPortfolio IR Methodology Closure",
        "",
        f"Generated at UTC: {summary['generated_at_utc']}",
        "",
        f"Status: **{summary['status']}**",
        f"Closed phases: **{summary['closed_phases']}/{summary['phase_count']}**",
        "",
        "| Phase | Status | Metric | Evidence |",
        "|---|---|---|---|",
    ]
    for phase in phases:
        lines.append(f"| {phase.phase_id}. {phase.title} | {phase.status} | {phase.metric} | `{phase.artifact}` |")
    lines.extend(
        [
            "",
            "## Protocol",
            "",
            "after every methodology stage, rerun the stage closure loop.",
            "A methodology stage is not closed until its stage-specific checks pass and",
            "the SkillOpt loop",
            "`python tools\\skillopt_ir\\run_ir_skill_loops.py --loops 5` has been rerun.",
            "The closure report must then be regenerated with",
            "`python evaluation\\build_ir_methodology_closure.py --strict`.",
            "",
            "## Caveats",
            "",
            "- This is a research/demo closure report, not a full production SLO system.",
            "- Mixed qrels must be reported with label-source provenance.",
            "- Learned reranking still requires larger independent human qrels.",
        ]
    )
    return "\n".join(lines) + "\n"


def build_closure_report(project_root: Path, output_dir: Path) -> dict[str, Any]:
    phases = build_phase_status(project_root)
    closed = sum(1 for phase in phases if phase.status == "closed")
    status = "closed" if closed == len(phases) else "open"
    generated = datetime.now(timezone.utc).isoformat()

    fields = ["phase_id", "title", "status", "metric", "artifact", "evidence", "risk", "next_action"]
    phase_rows = [phase.__dict__ for phase in phases]
    write_csv(output_dir / "phase_status.csv", phase_rows, fields)

    monitoring_rows = [
        row
        for row in phase_rows
        if row["phase_id"] in {"1", "6", "8", "9", "10", "11"}
    ]
    write_csv(output_dir / "monitoring_checks.csv", monitoring_rows, fields)

    summary = {
        "status": status,
        "generated_at_utc": generated,
        "phase_count": len(phases),
        "closed_phases": closed,
        "open_phases": [f"{phase.phase_id}:{phase.title}" for phase in phases if phase.status != "closed"],
        "phase_status_path": str(output_dir / "phase_status.csv"),
        "monitoring_checks_path": str(output_dir / "monitoring_checks.csv"),
    }
    write_json(output_dir / "run_summary.json", summary)
    write_json(output_dir / "phase_status.json", {"phases": phase_rows})
    (output_dir / "methodology_closure.md").write_text(build_markdown(phases, summary), encoding="utf-8")
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build FinPortfolio IR methodology closure report.")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--strict", action="store_true", help="Exit non-zero unless every phase is closed.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    project_root = Path(args.project_root).resolve()
    output_dir = (project_root / args.output_dir).resolve()
    summary = build_closure_report(project_root, output_dir)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    if args.strict and summary["status"] != "closed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
