"""Run the FinGPT Feature Engine smoke test on a FinPortfolio IR handoff."""

from __future__ import annotations

import argparse
import csv
import html
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from finportfolio_ir.io_utils import local_project_path, read_jsonl


DEFAULT_FINGPT_PROJECT = ROOT.parent / "Supportive_project_FinGPT_as_feature_engine"


def _read_csv(path: str | Path) -> list[dict[str, str]]:
    path = local_project_path(path)
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _read_json(path: str | Path) -> dict[str, Any]:
    path = local_project_path(path)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _count_jsonl(path: str | Path) -> int:
    path = local_project_path(path)
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


def _as_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    if value is None:
        return []
    text = str(value).strip()
    if not text:
        return []
    if text.startswith("[") and text.endswith("]"):
        try:
            parsed = json.loads(text)
            return _as_list(parsed)
        except json.JSONDecodeError:
            pass
    return [part.strip() for part in text.replace(",", ";").replace("|", ";").split(";") if part.strip()]


def _unique(values: list[str]) -> list[str]:
    return sorted({value for value in values if value})


def _counter_from_rows(rows: list[dict[str, str]], column: str) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for row in rows:
        for value in _as_list(row.get(column, "")):
            counter[value] += 1
    return dict(sorted(counter.items()))


def _write_json(path: str | Path, payload: dict[str, Any]) -> None:
    output = local_project_path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _run_command(command: list[str], cwd: Path) -> dict[str, object]:
    completed = subprocess.run(
        command,
        cwd=str(cwd),
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    result = {
        "command": command,
        "cwd": str(cwd),
        "returncode": completed.returncode,
        "stdout": (completed.stdout or "").strip(),
        "stderr": (completed.stderr or "").strip(),
    }
    if completed.returncode != 0:
        raise RuntimeError(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def output_paths(smoke_dir: str | Path) -> dict[str, Path]:
    smoke_dir = Path(smoke_dir)
    return {
        "leakage_report": smoke_dir / "leakage_report.json",
        "doc_prompts": smoke_dir / "doc_prompts.jsonl",
        "stock_prompts": smoke_dir / "stock_prompts.jsonl",
        "portfolio_prompts": smoke_dir / "portfolio_prompts.jsonl",
        "doc_extractions": smoke_dir / "doc_extractions.csv",
        "daily_stock_text_features": smoke_dir / "daily_stock_text_features.csv",
        "daily_portfolio_text_features": smoke_dir / "daily_portfolio_text_features.csv",
        "legacy_stock_features": smoke_dir / "legacy_stock_features.csv",
        "legacy_portfolio_features": smoke_dir / "legacy_portfolio_features.csv",
        "feature_provenance": smoke_dir / "feature_provenance.csv",
        "command_log": smoke_dir / "smoke_commands.json",
        "summary": smoke_dir / "smoke_summary.json",
        "html_report": smoke_dir / "smoke_report.html",
    }


def run_fingpt_smoke_commands(
    *,
    python_exe: str,
    fingpt_project: str | Path,
    contexts_path: str | Path,
    smoke_dir: str | Path,
) -> list[dict[str, object]]:
    fingpt_project = Path(fingpt_project).resolve()
    contexts_path = Path(contexts_path).resolve()
    smoke_dir = Path(smoke_dir).resolve()
    smoke_dir.mkdir(parents=True, exist_ok=True)
    paths = output_paths(smoke_dir)

    commands = [
        [
            python_exe,
            "scripts/validate_leakage.py",
            "--retrieved-contexts",
            str(contexts_path),
            "--output-report",
            str(paths["leakage_report"]),
        ],
        [
            python_exe,
            "scripts/build_fingpt_inputs.py",
            "--retrieved-contexts",
            str(contexts_path),
            "--doc-output",
            str(paths["doc_prompts"]),
            "--stock-output",
            str(paths["stock_prompts"]),
            "--portfolio-output",
            str(paths["portfolio_prompts"]),
        ],
        [
            python_exe,
            "scripts/run_fingpt_feature_extraction.py",
            "--retrieved-contexts",
            str(contexts_path),
            "--doc-output",
            str(paths["doc_extractions"]),
            "--daily-stock-output",
            str(paths["daily_stock_text_features"]),
            "--daily-portfolio-output",
            str(paths["daily_portfolio_text_features"]),
            "--stock-output",
            str(paths["legacy_stock_features"]),
            "--portfolio-output",
            str(paths["legacy_portfolio_features"]),
            "--provenance-output",
            str(paths["feature_provenance"]),
        ],
    ]
    results = [_run_command(command, cwd=fingpt_project) for command in commands]
    _write_json(paths["command_log"], {"commands": results})
    return results


def build_smoke_summary(
    *,
    contexts_path: str | Path,
    smoke_dir: str | Path,
) -> dict[str, Any]:
    paths = output_paths(smoke_dir)
    contexts = read_jsonl(contexts_path)
    doc_extractions = _read_csv(paths["doc_extractions"])
    daily_stock = _read_csv(paths["daily_stock_text_features"])
    daily_portfolio = _read_csv(paths["daily_portfolio_text_features"])
    legacy_stock = _read_csv(paths["legacy_stock_features"])
    legacy_portfolio = _read_csv(paths["legacy_portfolio_features"])
    provenance = _read_csv(paths["feature_provenance"])
    leakage_report = _read_json(paths["leakage_report"])

    context_doc_ids = {str(record.get("doc_id", "")) for record in contexts}
    extraction_doc_ids = {row.get("doc_id", "") for row in doc_extractions}
    provenance_doc_ids = {row.get("doc_id", "") for row in provenance}
    context_holdings = _unique(
        [
            ticker.upper()
            for record in contexts
            for ticker in _as_list(record.get("matched_holdings", []))
            if ticker.upper() != "MARKET"
        ]
    )
    stock_tickers = _unique([row.get("tic", "").upper() for row in daily_stock])
    context_scopes = Counter(str(record.get("evidence_scope", "unknown") or "unknown") for record in contexts)
    context_reason_tags = Counter(
        tag for record in contexts for tag in _as_list(record.get("retrieval_reason_tags", []))
    )
    duplicate_clusters = _unique([str(record.get("duplicate_cluster_id", "") or record.get("doc_id", "")) for record in contexts])

    doc_prompt_path = local_project_path(paths["doc_prompts"])
    doc_prompt_text = doc_prompt_path.read_text(encoding="utf-8") if doc_prompt_path.exists() else ""
    prompt_metadata_checks = {
        "has_evidence_scope": "evidence_scope=" in doc_prompt_text,
        "has_retrieval_reason_tags": "retrieval_reason_tags=" in doc_prompt_text,
        "has_duplicate_cluster_id": "duplicate_cluster_id=" in doc_prompt_text,
        "has_matched_holdings": "matched_holdings=" in doc_prompt_text,
    }

    missing_doc_extractions = sorted(context_doc_ids.difference(extraction_doc_ids))
    missing_provenance_docs = sorted(context_doc_ids.difference(provenance_doc_ids))
    coverage = {
        "context_doc_ids": len(context_doc_ids),
        "doc_extraction_doc_ids": len(extraction_doc_ids),
        "provenance_doc_ids": len(provenance_doc_ids),
        "missing_doc_extractions": missing_doc_extractions,
        "missing_provenance_docs": missing_provenance_docs,
        "context_holdings": context_holdings,
        "daily_stock_tickers": stock_tickers,
        "holding_coverage_rate": (
            len(set(context_holdings).intersection(stock_tickers)) / len(context_holdings)
            if context_holdings
            else 0.0
        ),
    }

    output_counts = {
        "contexts": len(contexts),
        "doc_prompts": _count_jsonl(paths["doc_prompts"]),
        "stock_prompts": _count_jsonl(paths["stock_prompts"]),
        "portfolio_prompts": _count_jsonl(paths["portfolio_prompts"]),
        "doc_extractions": len(doc_extractions),
        "daily_stock_text_features": len(daily_stock),
        "daily_portfolio_text_features": len(daily_portfolio),
        "legacy_stock_features": len(legacy_stock),
        "legacy_portfolio_features": len(legacy_portfolio),
        "feature_provenance": len(provenance),
    }

    hard_issues: list[str] = []
    if int(leakage_report.get("finportfolio_ir_leakage_rows", 0) or 0) != 0:
        hard_issues.append("FinGPT leakage validation reported violations.")
    if output_counts["doc_extractions"] != output_counts["contexts"]:
        hard_issues.append("Document extraction row count does not match context row count.")
    if missing_doc_extractions:
        hard_issues.append("Some context docs are missing from doc extractions.")
    if missing_provenance_docs:
        hard_issues.append("Some context docs are missing from provenance.")
    if output_counts["daily_stock_text_features"] == 0:
        hard_issues.append("No daily stock text features were produced.")
    if output_counts["daily_portfolio_text_features"] == 0:
        hard_issues.append("No daily portfolio text features were produced.")
    if not all(prompt_metadata_checks.values()):
        hard_issues.append("Doc prompts do not preserve all required IR metadata fields.")

    summary = {
        "status": "passed" if not hard_issues else "failed",
        "hard_issues": hard_issues,
        "output_counts": output_counts,
        "leakage_report": leakage_report,
        "coverage": coverage,
        "context_scope_counts": dict(sorted(context_scopes.items())),
        "context_reason_tag_counts": dict(sorted(context_reason_tags.items())),
        "duplicate_cluster_count": len(duplicate_clusters),
        "doc_parse_status_counts": _counter_from_rows(doc_extractions, "parse_status"),
        "doc_event_type_counts": _counter_from_rows(doc_extractions, "event_type"),
        "daily_stock_event_type_counts": _counter_from_rows(daily_stock, "dominant_event_type"),
        "daily_stock_scope_counts": _counter_from_rows(daily_stock, "evidence_scopes"),
        "daily_portfolio_scope_counts": _counter_from_rows(daily_portfolio, "evidence_scopes"),
        "prompt_metadata_checks": prompt_metadata_checks,
        "paths": {name: str(path) for name, path in paths.items()},
    }
    _write_json(paths["summary"], summary)
    local_project_path(paths["html_report"]).write_text(build_smoke_report_html(summary), encoding="utf-8")
    return summary


def _fmt_map(values: dict[str, Any]) -> str:
    if not values:
        return "<span class=\"muted\">none</span>"
    items = "".join(
        f"<li><code>{html.escape(str(key))}</code>: {html.escape(str(value))}</li>"
        for key, value in sorted(values.items())
    )
    return f"<ul>{items}</ul>"


def _fmt_bool(value: object) -> str:
    return "yes" if bool(value) else "no"


def build_smoke_report_html(summary: dict[str, Any]) -> str:
    status = str(summary.get("status", "unknown"))
    status_class = "pass" if status == "passed" else "fail"
    counts = summary.get("output_counts", {})
    coverage = summary.get("coverage", {})
    checks = summary.get("prompt_metadata_checks", {})
    rows = "".join(
        f"<tr><td>{html.escape(str(name))}</td><td>{html.escape(str(value))}</td></tr>"
        for name, value in counts.items()
    )
    check_rows = "".join(
        f"<tr><td>{html.escape(str(name))}</td><td>{_fmt_bool(value)}</td></tr>"
        for name, value in checks.items()
    )
    issues = summary.get("hard_issues", [])
    issue_html = "<span class=\"muted\">none</span>" if not issues else _fmt_map({str(i + 1): issue for i, issue in enumerate(issues)})
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>FinGPT Handoff Smoke Report</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 32px; color: #1f2937; background: #f8fafc; }}
    h1 {{ margin-bottom: 4px; }}
    h2 {{ margin-top: 28px; }}
    code {{ background: #e8eef6; padding: 2px 5px; border-radius: 4px; }}
    .muted {{ color: #64748b; }}
    .status {{ display: inline-block; padding: 4px 8px; border-radius: 4px; font-weight: 700; }}
    .pass {{ background: #dcfce7; color: #166534; }}
    .fail {{ background: #fee2e2; color: #991b1b; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 12px; max-width: 980px; }}
    .metric {{ background: white; border: 1px solid #d7dee8; padding: 12px; }}
    .metric strong {{ display: block; font-size: 24px; margin-top: 4px; }}
    table {{ border-collapse: collapse; width: 100%; background: white; border: 1px solid #d7dee8; }}
    th, td {{ border-bottom: 1px solid #e5eaf0; padding: 8px 10px; text-align: left; font-size: 13px; }}
    th {{ background: #e8eef6; }}
    ul {{ margin: 6px 0 0 18px; padding: 0; }}
  </style>
</head>
<body>
  <h1>FinGPT Handoff Smoke Report</h1>
  <p>Status: <span class="status {status_class}">{html.escape(status.upper())}</span></p>

  <h2>Core Checks</h2>
  <div class="grid">
    <div class="metric">Contexts<strong>{counts.get("contexts", 0)}</strong></div>
    <div class="metric">Doc Extractions<strong>{counts.get("doc_extractions", 0)}</strong></div>
    <div class="metric">Daily Stock Rows<strong>{counts.get("daily_stock_text_features", 0)}</strong></div>
    <div class="metric">Daily Portfolio Rows<strong>{counts.get("daily_portfolio_text_features", 0)}</strong></div>
    <div class="metric">Provenance Rows<strong>{counts.get("feature_provenance", 0)}</strong></div>
    <div class="metric">Holding Coverage<strong>{float(coverage.get("holding_coverage_rate", 0.0)):.3f}</strong></div>
  </div>

  <h2>Issues</h2>
  <div class="metric">{issue_html}</div>

  <h2>Output Counts</h2>
  <table><tbody>{rows}</tbody></table>

  <h2>Coverage</h2>
  <div class="grid">
    <div class="metric"><div>Context Holdings</div>{_fmt_map({ticker: 1 for ticker in coverage.get("context_holdings", [])})}</div>
    <div class="metric"><div>Daily Stock Tickers</div>{_fmt_map({ticker: 1 for ticker in coverage.get("daily_stock_tickers", [])})}</div>
    <div class="metric"><div>Missing Doc Extractions</div>{_fmt_map({doc_id: 1 for doc_id in coverage.get("missing_doc_extractions", [])})}</div>
    <div class="metric"><div>Missing Provenance Docs</div>{_fmt_map({doc_id: 1 for doc_id in coverage.get("missing_provenance_docs", [])})}</div>
  </div>

  <h2>IR Metadata Preservation</h2>
  <table><tbody>{check_rows}</tbody></table>

  <h2>Distributions</h2>
  <div class="grid">
    <div class="metric">Context Scopes{_fmt_map(summary.get("context_scope_counts", {}))}</div>
    <div class="metric">Doc Parse Status{_fmt_map(summary.get("doc_parse_status_counts", {}))}</div>
    <div class="metric">Doc Event Types{_fmt_map(summary.get("doc_event_type_counts", {}))}</div>
    <div class="metric">Daily Stock Scopes{_fmt_map(summary.get("daily_stock_scope_counts", {}))}</div>
  </div>
</body>
</html>
"""


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run FinGPT Feature Engine smoke test for an IR handoff package.")
    parser.add_argument("--handoff-dir", default="data/exports/fingpt_handoff_sample")
    parser.add_argument("--fingpt-project", default=str(DEFAULT_FINGPT_PROJECT))
    parser.add_argument("--python-exe", default=sys.executable)
    parser.add_argument("--smoke-dir", default="", help="Optional output directory. Defaults to <handoff-dir>/fingpt_smoke.")
    parser.add_argument("--skip-run", action="store_true", help="Only summarize existing smoke outputs.")
    args = parser.parse_args(argv)

    handoff_dir = Path(args.handoff_dir)
    contexts_path = handoff_dir / "retrieved_contexts.jsonl"
    smoke_dir = Path(args.smoke_dir) if args.smoke_dir else handoff_dir / "fingpt_smoke"
    if not contexts_path.exists():
        raise SystemExit(f"Missing handoff contexts: {contexts_path}")

    if not args.skip_run:
        run_fingpt_smoke_commands(
            python_exe=args.python_exe,
            fingpt_project=args.fingpt_project,
            contexts_path=contexts_path,
            smoke_dir=smoke_dir,
        )
    summary = build_smoke_summary(contexts_path=contexts_path, smoke_dir=smoke_dir)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0 if summary["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
