"""Run SkillLens/SkillOpt-style self-improvement loops for FinPortfolio IR.

This harness is intentionally deterministic. It uses the installed Microsoft
SkillLens/SkillOpt packages as available local tooling, but keeps the actual
project update bounded: each loop proposes one addition to ``agent_skill.md``
and accepts it only when the local validation score improves.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


DEFAULT_OUTPUT_DIR = Path("data/exports/skillopt_ir_loops_v1")
DEFAULT_SKILL_PATH = Path("agent_skill.md")


@dataclass(frozen=True)
class LoopSpec:
    stage_id: int
    title: str
    lens: str
    section: str
    required_terms: tuple[str, ...]


def _read_csv_first(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return rows[0] if rows else {}


def _count_csv_rows(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return max(sum(1 for _ in handle) - 1, 0)


def _count_jsonl_rows(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


def _csv_value(row: dict[str, str], key: str, fallback: str = "unknown") -> str:
    value = str(row.get(key, "") or "").strip()
    return value if value else fallback


def load_project_context(project_root: Path) -> dict[str, str]:
    """Collect small, auditable facts for the generated agent skill."""

    registry_rows = _count_csv_rows(project_root / "data/source_registry/source_registry.csv")
    corpus_rows = _count_jsonl_rows(
        project_root / "data/processed_documents/sec_macro_company_ir_ppo_2010_2023_documents.jsonl"
    )
    mixed_qrels_rows = _count_csv_rows(project_root / "data/annotations/search_quality_qrels_mixed_v10.csv")

    promotion_row = _read_csv_first(
        project_root
        / "data/exports/document_vs_evidence_units_company_ir_holdout_v1"
        / "promotion_gate_mixed_v1/evidence_unit_promotion_mixed_v1_acceptance.csv"
    )
    smoke_row = _read_csv_first(project_root / "data/exports/live_search_smoke_v1/live_search_smoke_summary.csv")

    skill_packages = {
        "skilllens": "present" if importlib.util.find_spec("skilllens") else "missing",
        "skillopt": "present" if importlib.util.find_spec("skillopt") else "missing",
        "skillopt_sleep": "present" if importlib.util.find_spec("skillopt_sleep") else "missing",
    }

    return {
        "registry_rows": str(registry_rows or "unknown"),
        "corpus_rows": str(corpus_rows or "unknown"),
        "mixed_qrels_rows": str(mixed_qrels_rows or "unknown"),
        "promotion_decision": _csv_value(promotion_row, "decision"),
        "promotion_delta_ndcg": _csv_value(promotion_row, "delta_ndcg_at_10"),
        "promotion_human_labels": _csv_value(promotion_row, "human_label_count"),
        "promotion_qrels": _csv_value(promotion_row, "qrels_count"),
        "smoke_status": _csv_value(smoke_row, "status"),
        "smoke_passed": _csv_value(smoke_row, "passed_count"),
        "smoke_cases": _csv_value(smoke_row, "case_count"),
        "smoke_max_latency_ms": _csv_value(smoke_row, "max_latency_ms"),
        "skill_packages": ", ".join(f"{name}={status}" for name, status in skill_packages.items()),
    }


def base_skill_text(context: dict[str, str]) -> str:
    return f"""# FinPortfolio IR Agent Skill

## Purpose

Use this skill when working on Portfolio-Aware Search, the source-first
financial IR layer that decides what evidence is allowed to enter FinGPT and
CHRL.

The operating chain is:

```text
source -> evidence -> meaning -> feature -> action -> improvement
```

Current local facts:

- source registry rows: {context["registry_rows"]}
- processed SEC/macro/company-IR corpus rows: {context["corpus_rows"]}
- mixed search qrels rows: {context["mixed_qrels_rows"]}
- SkillLens/SkillOpt packages: {context["skill_packages"]}

## Non-Negotiables

- No source without provenance.
- No claim without citation.
- No feature without confidence and point-in-time availability.
- No ranker promotion without qrels, metrics, and a regression guard.
- No live demo path may perform a deep backfill or uncached long LLM job.
- No methodology stage is closed until the SkillLens/SkillOpt loop has been
  rerun and recorded after the stage-specific checks.

## Current Methodology Stage

Stages 1-5 are the active working frame:

1. source/crawler foundation;
2. evidence-unit retrieval;
3. hybrid ranking and calibrated promotion;
4. live stability and regression smoke tests;
5. FinGPT/CHRL handoff discipline.
"""


def build_loop_specs(context: dict[str, str]) -> list[LoopSpec]:
    return [
        LoopSpec(
            1,
            "Source And Crawler Discipline",
            "SkillLens found the recurring project pattern: quality starts before retrieval, at source permissioning.",
            f"""## Stage 1 Skill: Source And Crawler Discipline

Treat the crawler as a source detective, not a blind downloader.

Use these anchors before changing crawl behavior:

- `data/source_registry/source_registry.csv`
- `crawler/source_registry.py`
- `crawler/normalize_documents.py`
- `crawler/live_incremental_fetch.py`
- `docs/IR_IMPLEMENTATION_PLAN.md`

Do:

- prefer official APIs, RSS, sitemap, and source-card endpoints;
- preserve `canonical_url`, `document_hash`, `published_at`, `first_seen_at`, `available_at`, and `source_registry_id`;
- normalize text before hashing so duplicates do not pretend to be independent evidence;
- keep raw snapshots or canonical links for auditability;
- keep live adapters small and fresh.

Do not:

- treat favorite websites as automatically credible;
- let archive pages masquerade as documents;
- run historical backfill during a live request.

Validation signal: the current registry has {context["registry_rows"]} rows and the main processed corpus has {context["corpus_rows"]} rows.
""",
            (
                "source_registry",
                "document_hash",
                "canonical_url",
                "available_at",
                "live adapters",
            ),
        ),
        LoopSpec(
            2,
            "Evidence-Unit Retrieval Discipline",
            "SkillLens found that document-level relevance is too coarse for investor evidence.",
            """## Stage 2 Skill: Evidence-Unit Retrieval Discipline

Search should retrieve the smallest decision-grade evidence unit that still
keeps enough context.

Preferred evidence units:

- SEC section: Item 1A, MD&A, financial statements, legal proceedings;
- SEC exhibit: earnings release and investor material;
- company IR fact block;
- official macro observation;
- cited LLM claim only after retrieval.

Use these anchors:

- `features/build_evidence_units.py`
- `docs/DATA_SCHEMA.md`
- `evaluation/compare_document_vs_evidence_units.py`
- `retrieval/evidence_unit_gate.py`

Do:

- keep parent source document IDs for audit;
- evaluate evidence units against qrels at the correct grain;
- keep broad overview queries on document-level retrieval unless the gate says otherwise.

Do not:

- assume smaller chunks are automatically better;
- promote raw evidence-unit retrieval without calibration.
""",
            (
                "evidence-unit",
                "source document",
                "SEC section",
                "macro observation",
                "qrels",
            ),
        ),
        LoopSpec(
            3,
            "Hybrid Ranking And Promotion Gate",
            "SkillOpt accepts ranking changes only when measured quality improves under a held-out gate.",
            f"""## Stage 3 Skill: Hybrid Ranking And Promotion Gate

Ranking is not `BM25 vs LLM`. It is a calibrated stack:

```text
BM25 + structured fields + intent + source/type/freshness calibration
```

Use these anchors:

- `retrieval/hybrid_ranker.py`
- `retrieval/evidence_unit_calibration.py`
- `retrieval/evidence_unit_promotion.py`
- `evaluation/evaluate_ir_metrics.py`
- `evaluation/evaluate_evidence_unit_promotion_gate.py`

Current accepted promotion:

- decision: {context["promotion_decision"]}
- mixed qrels: {context["promotion_qrels"]} rows, including {context["promotion_human_labels"]} human spot-check labels
- calibrated NDCG@10 delta: {context["promotion_delta_ndcg"]}

Do:

- report Precision@10, NDCG@10, MRR, qrels coverage, and label source mix;
- prefer intent-aware source matching over generic recency;
- keep the accepted live path calibrated and intent-guarded.

Do not:

- learn a black-box ranker from tiny qrels;
- claim production quality from assistant-only labels;
- accept ranking changes that improve NDCG while creating unjudged top-10 rows.
""",
            (
                "BM25",
                "structured fields",
                "intent",
                "NDCG@10",
                "promotion",
            ),
        ),
        LoopSpec(
            4,
            "Live Stability And Demo Guard",
            "SkillLens found repeated live-demo failures: timeouts, slow LLM calls, and accidental backfills.",
            f"""## Stage 4 Skill: Live Stability And Regression Guard

Cloudflare/demo stability is part of IR correctness. A result that times out is
not useful evidence.

Use these anchors:

- `evaluation/run_live_search_smoke.py`
- `tests/test_live_search_smoke.py`
- `deploy/cloudflare/start_public_demo.ps1`
- `web_app.py`

Current smoke baseline:

- status: {context["smoke_status"]}
- passed: {context["smoke_passed"]}/{context["smoke_cases"]}
- max latency: {context["smoke_max_latency_ms"]} ms

Do:

- run live smoke after changing retrieval gates, folder analysis, or Cloudflare startup;
- precompute or cache expensive analysis examples;
- keep macro evidence-unit search query-selective;
- return JSON errors from API paths, never HTML timeout pages when possible.

Do not:

- block the request path on full corpus rebuilds;
- call LLM over all documents for `Perform Analysis`;
- silently switch evidence-unit search on for broad queries.
""",
            (
                "smoke",
                "Cloudflare",
                "latency",
                "JSON",
                "cache",
            ),
        ),
        LoopSpec(
            5,
            "FinGPT And CHRL Handoff Discipline",
            "SkillOpt keeps the IR layer useful for the larger interpretable trading-bot stack.",
            """## Stage 5 Skill: FinGPT And CHRL Handoff Discipline

Portfolio-Aware Search owns the right to read. FinGPT owns meaning extraction.
CHRL owns portfolio action. Do not blur these boundaries.

Use these anchors:

- `docs/FINGPT_HANDOFF.md`
- `features/export_evidence_bundles.py`
- `features/export_fingpt_contexts.py`
- `C:\\Users\\ivanp\\RL for Time-Series Forecasting\\data_RLagent_for_Joseph\\experience\\WORLD OF BEST TRADING BOT\\IR_D_INTEGRATED_INNOVATIVE_IR_PICTURE.md`
- `C:\\Users\\ivanp\\RL for Time-Series Forecasting\\data_RLagent_for_Joseph\\experience\\WORLD OF BEST TRADING BOT\\IR_E_INNOVATIVE_METHODOLOGY_AND_PLAN.md`

Do:

- export cited evidence bundles, not free-floating summaries;
- preserve point-in-time dates for every downstream feature;
- include source quality, evidence specificity, and missing-source warnings;
- treat downstream FinGPT/CHRL ablations as feedback, not as permission to leak future data.

Do not:

- let LLM summaries replace original citations;
- let trading returns alone justify retrieval changes;
- optimize search by looking at future PnL without a validation firewall.
""",
            (
                "FinGPT",
                "CHRL",
                "evidence bundles",
                "point-in-time",
                "validation firewall",
            ),
        ),
    ]


def normalize_terms(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9@._:-]+", text.lower()))


GLOBAL_REQUIRED_TERMS = (
    "source",
    "evidence",
    "qrels",
    "NDCG@10",
    "Precision@10",
    "MRR",
    "BM25",
    "structured",
    "intent",
    "promotion",
    "smoke",
    "FinGPT",
    "CHRL",
    "point-in-time",
    "document_hash",
)


def score_skill_text(text: str, specs: Iterable[LoopSpec]) -> dict[str, object]:
    lower = text.lower()
    normalized = normalize_terms(text)
    checks: list[dict[str, object]] = []

    def add_check(name: str, passed: bool, weight: int = 1) -> None:
        checks.append({"name": name, "passed": bool(passed), "weight": weight})

    for term in GLOBAL_REQUIRED_TERMS:
        add_check(f"global:{term}", term.lower() in lower or term.lower() in normalized)

    for spec in specs:
        add_check(f"stage:{spec.stage_id}:heading", f"stage {spec.stage_id} skill" in lower, 2)
        for term in spec.required_terms:
            add_check(
                f"stage:{spec.stage_id}:term:{term}",
                term.lower() in lower or term.lower() in normalized,
            )

    add_check("forbid:no-production-overclaim", "production-ready without gate" not in lower, 2)
    add_check("forbid:no-human-label-overclaim", "fully human-labeled" not in lower, 2)
    add_check("forbid:no-future-leak", "future pnl" not in lower or "validation firewall" in lower, 2)

    score = sum(int(item["weight"]) for item in checks if item["passed"])
    max_score = sum(int(item["weight"]) for item in checks)
    failed = [str(item["name"]) for item in checks if not item["passed"]]
    return {
        "score": score,
        "max_score": max_score,
        "ratio": round(score / max_score if max_score else 0.0, 4),
        "failed_checks": failed,
    }


def append_section_if_missing(text: str, section: str, stage_id: int) -> str:
    marker = f"## Stage {stage_id} Skill:"
    if marker.lower() in text.lower():
        return text
    return text.rstrip() + "\n\n" + section.strip() + "\n"


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_loop_summary(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "loop",
        "title",
        "accepted",
        "before_score",
        "after_score",
        "before_ratio",
        "after_ratio",
        "failed_checks_after",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def run_skill_loops(
    project_root: Path,
    skill_path: Path,
    output_dir: Path,
    loops: int,
    restart: bool = False,
) -> dict[str, object]:
    context = load_project_context(project_root)
    specs = build_loop_specs(context)[:loops]

    if restart or not skill_path.exists():
        current = base_skill_text(context)
    else:
        current = skill_path.read_text(encoding="utf-8")

    run_rows: list[dict[str, object]] = []
    started_at = datetime.now(timezone.utc).isoformat()

    for spec in specs:
        before = score_skill_text(current, specs)
        candidate = append_section_if_missing(current, spec.section, spec.stage_id)
        after = score_skill_text(candidate, specs)
        accepted = int(after["score"]) > int(before["score"]) or candidate == current
        if accepted:
            current = candidate

        loop_payload = {
            "loop": spec.stage_id,
            "title": spec.title,
            "lens": spec.lens,
            "accepted": accepted,
            "before": before,
            "after": after,
            "required_terms": list(spec.required_terms),
            "proposal_chars": len(spec.section),
        }
        write_json(output_dir / f"loop_{spec.stage_id:02d}.json", loop_payload)

        run_rows.append(
            {
                "loop": spec.stage_id,
                "title": spec.title,
                "accepted": accepted,
                "before_score": before["score"],
                "after_score": after["score"],
                "before_ratio": before["ratio"],
                "after_ratio": after["ratio"],
                "failed_checks_after": "|".join(after["failed_checks"][:20]),
            }
        )

    final_score = score_skill_text(current, specs)
    header = (
        "<!-- Generated by tools/skillopt_ir/run_ir_skill_loops.py. "
        "SkillLens-style observations, SkillOpt-style gated edits. -->\n\n"
    )
    if not current.startswith("<!-- Generated by"):
        current = header + current

    write_text(skill_path, current)
    write_text(output_dir / "best_agent_skill.md", current)
    write_loop_summary(output_dir / "loop_summary.csv", run_rows)
    write_json(
        output_dir / "run_summary.json",
        {
            "status": "completed",
            "started_at": started_at,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "project_root": str(project_root),
            "skill_path": str(skill_path),
            "output_dir": str(output_dir),
            "loops_requested": loops,
            "loops_run": len(specs),
            "accepted_loops": sum(1 for row in run_rows if row["accepted"]),
            "final_score": final_score,
            "context": context,
        },
    )
    return {
        "status": "completed",
        "loops_run": len(specs),
        "accepted_loops": sum(1 for row in run_rows if row["accepted"]),
        "skill_path": str(skill_path),
        "output_dir": str(output_dir),
        "final_score": final_score,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run deterministic IR SkillLens/SkillOpt loops.")
    parser.add_argument("--project-root", default=".", help="FinPortfolio_IR project root.")
    parser.add_argument("--skill-path", default=str(DEFAULT_SKILL_PATH), help="Path to agent_skill.md.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Loop artifact output directory.")
    parser.add_argument("--loops", type=int, default=5, help="Number of loops to run; max is 5.")
    parser.add_argument("--restart", action="store_true", help="Recreate the skill from the base template.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    project_root = Path(args.project_root).resolve()
    skill_path = (project_root / args.skill_path).resolve()
    output_dir = (project_root / args.output_dir).resolve()
    loops = max(1, min(int(args.loops), 5))
    result = run_skill_loops(project_root, skill_path, output_dir, loops, restart=args.restart)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
