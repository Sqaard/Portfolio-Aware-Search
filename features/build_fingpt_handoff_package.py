"""Build a reproducible FinPortfolio IR -> FinGPT handoff package."""

from __future__ import annotations

import argparse
import html
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from features.export_evidence_bundles import build_evidence_bundles
from features.export_fingpt_contexts import export_context_records
from features.validate_fingpt_handoff import validate_bundle_records, validate_context_records, write_report
from finportfolio_ir.io_utils import read_jsonl, write_jsonl


HANDOFF_SCHEMA_VERSION = "finportfolio_ir_to_fingpt_v1"


def _write_json(path: str | Path, payload: dict[str, object]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _fmt_count_map(values: object) -> str:
    if not isinstance(values, dict) or not values:
        return "<span class=\"muted\">none</span>"
    items = "".join(
        f"<li><code>{html.escape(str(key))}</code>: {html.escape(str(value))}</li>"
        for key, value in sorted(values.items())
    )
    return f"<ul>{items}</ul>"


def _sample_rows(records: list[dict[str, object]], limit: int = 8) -> str:
    rows = []
    for record in records[:limit]:
        reason_tags = ", ".join(str(tag) for tag in record.get("retrieval_reason_tags", []))
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(record.get('rank', '')))}</td>"
            f"<td><code>{html.escape(str(record.get('doc_id', '')))}</code></td>"
            f"<td>{html.escape(str(record.get('evidence_scope', '')))}</td>"
            f"<td>{html.escape(str(record.get('matched_tickers', [])))}</td>"
            f"<td>{html.escape(str(record.get('title', '')))}</td>"
            f"<td>{html.escape(str(record.get('available_at', '')))}</td>"
            f"<td>{html.escape(str(record.get('final_score', '')))}</td>"
            f"<td>{html.escape(reason_tags)}</td>"
            "</tr>"
        )
    return "\n".join(rows)


def build_handoff_report_html(
    *,
    manifest: dict[str, object],
    validation: dict[str, object],
    contexts: list[dict[str, object]],
) -> str:
    context_report = validation["contexts"]
    bundle_report = validation.get("bundles", {})
    status = str(context_report.get("status", "unknown"))
    status_class = "pass" if status == "passed" else "fail"
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>FinPortfolio IR -> FinGPT Handoff</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 32px; color: #1f2937; background: #f8fafc; }}
    h1 {{ margin-bottom: 4px; }}
    h2 {{ margin-top: 28px; }}
    code {{ background: #e8eef6; padding: 2px 5px; border-radius: 4px; }}
    .muted {{ color: #64748b; }}
    .status {{ display: inline-block; padding: 4px 8px; border-radius: 4px; font-weight: 700; }}
    .pass {{ background: #dcfce7; color: #166534; }}
    .fail {{ background: #fee2e2; color: #991b1b; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 12px; max-width: 900px; }}
    .metric {{ background: white; border: 1px solid #d7dee8; padding: 12px; }}
    .metric strong {{ display: block; font-size: 24px; margin-top: 4px; }}
    .metric code {{ white-space: nowrap; }}
    .table-wrap {{ overflow-x: auto; background: white; border: 1px solid #d7dee8; }}
    table {{ border-collapse: collapse; width: 100%; min-width: 1120px; }}
    th, td {{ border-bottom: 1px solid #e5eaf0; padding: 8px 10px; text-align: left; font-size: 13px; vertical-align: top; }}
    th {{ background: #e8eef6; }}
    ul {{ margin: 6px 0 0 18px; padding: 0; }}
  </style>
</head>
<body>
  <h1>FinPortfolio IR -> FinGPT Handoff</h1>
  <p class="muted">Schema: <code>{html.escape(str(manifest["schema_version"]))}</code>. Generated at {html.escape(str(manifest["created_at_utc"]))}.</p>
  <p>Status: <span class="status {status_class}">{html.escape(status.upper())}</span></p>

  <h2>Summary</h2>
  <div class="grid">
    <div class="metric">Rows<strong>{context_report.get("row_count", 0)}</strong></div>
    <div class="metric">Queries<strong>{context_report.get("query_count", 0)}</strong></div>
    <div class="metric">Unique Docs<strong>{context_report.get("unique_doc_count", 0)}</strong></div>
    <div class="metric">Hard Issues<strong>{context_report.get("hard_issue_count", 0)}</strong></div>
    <div class="metric">Bundles<strong>{bundle_report.get("bundle_count", 0) if isinstance(bundle_report, dict) else 0}</strong></div>
    <div class="metric">Duplicate Cluster Rows<strong>{context_report.get("duplicate_context_cluster_rows", 0)}</strong></div>
  </div>

  <h2>Distributions</h2>
  <div class="grid">
    <div class="metric">Methods{_fmt_count_map(context_report.get("method_counts", {}))}</div>
    <div class="metric">Evidence Scope{_fmt_count_map(context_report.get("scope_counts", {}))}</div>
    <div class="metric">Covered Holdings{_fmt_count_map(context_report.get("covered_holdings", {}))}</div>
  </div>

  <h2>Sample Context Rows</h2>
  <div class="table-wrap">
    <table>
      <thead>
        <tr><th>rank</th><th>doc_id</th><th>scope</th><th>tickers</th><th>title</th><th>available_at</th><th>score</th><th>reason_tags</th></tr>
      </thead>
      <tbody>{_sample_rows(contexts)}</tbody>
    </table>
  </div>
</body>
</html>
"""


def build_handoff_package(
    retrieval_records: list[dict[str, object]],
    output_dir: str | Path,
    source_path: str | Path,
) -> dict[str, object]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    contexts = export_context_records(retrieval_records)
    bundles = build_evidence_bundles(contexts)
    context_report = validate_context_records(contexts)
    bundle_report = validate_bundle_records(bundles, context_report)
    validation = {
        "contexts": context_report,
        "bundles": bundle_report,
    }

    contexts_path = output_dir / "retrieved_contexts.jsonl"
    bundles_path = output_dir / "evidence_bundles.jsonl"
    validation_path = output_dir / "handoff_validation.json"
    manifest_path = output_dir / "handoff_manifest.json"
    report_path = output_dir / "handoff_report.html"

    write_jsonl(contexts_path, contexts)
    write_jsonl(bundles_path, bundles)
    write_report(validation_path, validation)

    manifest: dict[str, object] = {
        "schema_version": HANDOFF_SCHEMA_VERSION,
        "created_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source_retrieval_path": str(Path(source_path)),
        "retrieved_contexts_path": str(contexts_path),
        "evidence_bundles_path": str(bundles_path),
        "validation_report_path": str(validation_path),
        "html_report_path": str(report_path),
        "context_rows": context_report["row_count"],
        "bundle_rows": bundle_report["bundle_count"],
        "status": "passed" if context_report["status"] == "passed" and bundle_report["status"] == "passed" else "failed",
        "recommended_fingpt_input": str(contexts_path),
    }
    _write_json(manifest_path, manifest)
    report_path.write_text(
        build_handoff_report_html(manifest=manifest, validation=validation, contexts=contexts),
        encoding="utf-8",
    )
    return manifest


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Build a FinPortfolio IR handoff package for FinGPT testing.")
    parser.add_argument("--retrieval", required=True, help="FinPortfolio IR retrieval JSONL.")
    parser.add_argument("--output-dir", required=True, help="Directory for handoff artifacts.")
    args = parser.parse_args(argv)

    manifest = build_handoff_package(
        retrieval_records=read_jsonl(args.retrieval),
        output_dir=args.output_dir,
        source_path=args.retrieval,
    )
    print(json.dumps(manifest, indent=2, ensure_ascii=False))
    return 0 if manifest["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
