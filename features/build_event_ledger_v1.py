"""Build the point-in-time event ledger v1.

Inputs can include existing normalized document JSONL files and live SEC APIs.
The output is an event ledger, daily feature panel, coverage/PIT reports, and
source cards. This is pre-PPO data preparation only.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from finportfolio_ir.event_ledger import (  # noqa: E402
    EVENT_FEATURE_COLUMNS,
    SOURCE_CARDS,
    coverage_rows,
    daily_feature_rows,
    dedupe_events,
    events_from_companyfacts,
    events_from_document,
    events_from_sec_submissions,
    read_ticker_metadata,
    summarize_events,
    validate_pit,
    write_csv,
)
from finportfolio_ir.io_utils import write_jsonl  # noqa: E402


DEFAULT_DOCUMENTS = [
    ROOT / "data" / "processed_documents" / "sec_macro_company_ir_ppo_2010_2023_documents.jsonl",
]
DEFAULT_METADATA = ROOT / "data" / "processed_documents" / "ticker_metadata.csv"
DEFAULT_OUTPUT_DIR = ROOT / "data" / "event_ledger_v1"


def iter_jsonl(paths: Iterable[Path]) -> Iterable[dict[str, Any]]:
    for path in paths:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"Invalid JSONL at {path}:{line_number}: {exc}") from exc


def parse_date(value: str) -> date:
    return date.fromisoformat(value)


def stderr_progress(stage: str, total: int):
    def _progress(ticker: str, status: str, position: int) -> None:
        print(
            json.dumps(
                {"stage": stage, "ticker": ticker, "status": status, "position": position, "total": total},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )

    return _progress


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--documents", nargs="*", type=Path, default=DEFAULT_DOCUMENTS)
    parser.add_argument("--existing-events", nargs="*", type=Path, default=[], help="Existing event JSONL files to merge before new ingestion.")
    parser.add_argument("--skip-documents", action="store_true", help="Skip document scanning and only merge existing/live event sources.")
    parser.add_argument("--metadata", type=Path, default=DEFAULT_METADATA)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--start-date", default="2010-01-01")
    parser.add_argument("--end-date", default=date.today().isoformat())
    parser.add_argument("--tickers", nargs="*", default=[])
    parser.add_argument("--filter-output-to-tickers", action="store_true", help="Filter final output to --tickers. By default tickers only limit live fetch sources.")
    parser.add_argument("--fetch-sec-submissions", action="store_true")
    parser.add_argument("--fetch-companyfacts", action="store_true")
    parser.add_argument("--force-sec-refresh", action="store_true")
    parser.add_argument("--user-agent", default="FinPortfolioIR/0.1 research contact: local@example.com")
    parser.add_argument("--max-documents", type=int, default=0, help="Debug limit; 0 means full input.")
    parser.add_argument("--max-facts-per-tag", type=int, default=500)
    return parser.parse_args(argv)


def write_report(path: Path, manifest: dict[str, Any], summary: dict[str, Any], pit: dict[str, Any]) -> None:
    lines = [
        "# Event Ledger V1 Report",
        "",
        "This is a point-in-time READ/FEATURE layer artifact. It does not promote features into PPO state.",
        "",
        "## Contract",
        "",
        f"- Right governed: `{manifest['right_governed']}`",
        "- PIT invariant: `available_at_utc <= retrieval_cutoff_utc <= decision_time_utc`",
        "- LLM status: not used in this artifact; LLM extraction must run only after retrieval.",
        "- Promotion status: `not_ppo_ready` until coverage, IC, source controls, temporal nulls, and placebo gates pass.",
        "",
        "## Scope",
        "",
        f"- Start date: `{manifest['start_date']}`",
        f"- End date: `{manifest['end_date']}`",
        f"- Input documents scanned: `{manifest['input_documents_scanned']}`",
        f"- Existing event rows merged: `{manifest['existing_events_loaded']}`",
        f"- Event rows: `{summary['event_count']}`",
        f"- Tickers: `{summary['ticker_count']}`",
        f"- PIT valid: `{pit['point_in_time_valid']}`",
        f"- PIT violations: `{pit['pit_violation_count']}`",
        f"- Live SEC submissions: `{manifest['fetch_sec_submissions']}`",
        f"- SEC companyfacts: `{manifest['fetch_companyfacts']}`",
        "",
        "## Event Types",
        "",
        "| event_type | count |",
        "| --- | ---: |",
    ]
    for key, value in summary["event_type_counts"].items():
        lines.append(f"| `{key}` | {value} |")
    lines.extend(["", "## Year Coverage", "", "| year | count |", "| --- | ---: |"])
    for key, value in summary.get("year_counts", {}).items():
        lines.append(f"| `{key}` | {value} |")
    lines.extend(["", "## Sources", "", "| source | count |", "| --- | ---: |"])
    for key, value in summary["source_counts"].items():
        lines.append(f"| `{key}` | {value} |")
    lines.extend(
        [
            "",
            "## Daily Feature Schema",
            "",
            f"- Fixed event feature columns: `{len(EVENT_FEATURE_COLUMNS)}`",
            "- Aggregation grain: `ticker` x `decision_date`.",
            "- Daily rows contain event counts, source coverage, and summed/mean fixed event features.",
            "- Use as candidate retrieval/feature layer only; do not feed directly into PPO without firewall tests.",
            "",
            "Key columns:",
            "",
        ]
    )
    for column in EVENT_FEATURE_COLUMNS:
        lines.append(f"- `{column}`")
    lines.extend(
        [
            "",
            "## Artifacts",
            "",
            f"- Events JSONL: `{manifest['outputs']['events_jsonl']}`",
            f"- Daily features CSV: `{manifest['outputs']['daily_features_csv']}`",
            f"- Coverage CSV: `{manifest['outputs']['coverage_csv']}`",
            f"- PIT validation JSON: `{manifest['outputs']['pit_validation_json']}`",
            f"- Source cards CSV: `{manifest['outputs']['source_cards_csv']}`",
            f"- Manifest JSON: `{manifest['outputs']['manifest_json']}`",
            "",
            "## Firewall Status",
            "",
            "- Coverage/PIT preparation is complete for this build.",
            "- Cross-sectional alpha, macro/sector controls, temporal nulls, and capacity-fair PPO twin are not run here.",
            "- Any CHRL package must be built only after those cheap tests pass.",
            "",
            "## Known Gaps",
            "",
            "- Analyst estimate/revision feeds are not included; they need a licensed/vendor connector.",
            "- SEC companyfacts enrichment is optional and can be built as a separate batch; if `fetch_companyfacts=False`, the main ledger remains filing/event-first.",
            "- SEC live submissions include broad filing metadata, including ownership and registration forms; downstream tests should control source/form family before making alpha claims.",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    start_date = parse_date(args.start_date)
    end_date = parse_date(args.end_date)
    tickers = [ticker.upper() for ticker in args.tickers] if args.tickers else []
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    events: list[dict[str, Any]] = []
    existing_loaded = 0
    for existing_path in args.existing_events:
        for event in iter_jsonl([existing_path]):
            events.append(event)
            existing_loaded += 1

    scanned = 0
    if not args.skip_documents:
        for record in iter_jsonl(args.documents):
            scanned += 1
            events.extend(events_from_document(record))
            if args.max_documents and scanned >= args.max_documents:
                break
    print(
        json.dumps(
            {
                "stage": "loaded_inputs",
                "existing_events_loaded": existing_loaded,
                "documents_scanned": scanned,
                "events_so_far": len(events),
            },
            ensure_ascii=False,
        ),
        file=sys.stderr,
    )

    metadata = read_ticker_metadata(args.metadata)
    live_total = len(tickers) if tickers else len(metadata)
    if args.fetch_sec_submissions:
        print(json.dumps({"stage": "fetch_sec_submissions", "tickers": tickers or "all"}, ensure_ascii=False), file=sys.stderr)
        events.extend(
            events_from_sec_submissions(
                metadata,
                cache_dir=output_dir / "raw_cache" / "sec_submissions",
                start_date=start_date,
                end_date=end_date,
                tickers=tickers or None,
                force=args.force_sec_refresh,
                user_agent=args.user_agent,
                progress=stderr_progress("fetch_sec_submissions", live_total),
            )
        )
    if args.fetch_companyfacts:
        print(json.dumps({"stage": "fetch_companyfacts", "tickers": tickers or "all"}, ensure_ascii=False), file=sys.stderr)
        events.extend(
            events_from_companyfacts(
                metadata,
                cache_dir=output_dir / "raw_cache" / "sec_companyfacts",
                start_date=start_date,
                end_date=end_date,
                tickers=tickers or None,
                force=args.force_sec_refresh,
                user_agent=args.user_agent,
                max_facts_per_tag=args.max_facts_per_tag,
                progress=stderr_progress("fetch_companyfacts", live_total),
            )
        )

    events = [
        event
        for event in dedupe_events(events)
        if (not args.filter_output_to_tickers or not tickers or str(event.get("ticker", "")).upper() in tickers)
    ]
    summary = summarize_events(events)
    pit = validate_pit(events)
    coverage = coverage_rows(events)
    daily_features = daily_feature_rows(events)

    events_jsonl = output_dir / "events.jsonl"
    daily_csv = output_dir / "daily_event_features.csv"
    coverage_csv = output_dir / "coverage_by_ticker_year.csv"
    pit_json = output_dir / "pit_validation.json"
    source_cards_csv = output_dir / "source_cards_v1.csv"
    manifest_json = output_dir / "manifest.json"
    report_md = output_dir / "event_ledger_v1_report.md"

    write_jsonl(events_jsonl, events)
    write_csv(daily_csv, daily_features)
    write_csv(coverage_csv, coverage)
    write_csv(source_cards_csv, SOURCE_CARDS)
    pit_json.write_text(json.dumps(pit, ensure_ascii=False, indent=2), encoding="utf-8")

    manifest = {
        "status": "completed",
        "created_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "right_governed": "READ_RETRIEVE_FEATURE",
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "input_documents": [str(path) for path in args.documents],
        "existing_events": [str(path) for path in args.existing_events],
        "existing_events_loaded": existing_loaded,
        "skip_documents": bool(args.skip_documents),
        "input_documents_scanned": scanned,
        "metadata": str(args.metadata),
        "fetch_sec_submissions": bool(args.fetch_sec_submissions),
        "fetch_companyfacts": bool(args.fetch_companyfacts),
        "tickers": tickers or "all_metadata_tickers",
        "filter_output_to_tickers": bool(args.filter_output_to_tickers),
        "summary": summary,
        "pit_validation": pit,
        "outputs": {
            "events_jsonl": str(events_jsonl),
            "daily_features_csv": str(daily_csv),
            "coverage_csv": str(coverage_csv),
            "pit_validation_json": str(pit_json),
            "source_cards_csv": str(source_cards_csv),
            "manifest_json": str(manifest_json),
            "report_md": str(report_md),
        },
        "promotion_status": "not_ppo_ready",
        "next_firewall_tests": [
            "coverage drift by sector/year",
            "cross-sectional IC 1d/5d/21d/63d",
            "macro/sector/source controls",
            "temporal nulls and ticker/date permutations",
            "obs-dim matched noise placebo",
        ],
        "known_gaps": [
            "analyst estimate/revision vendor lane is not included",
            "SEC companyfacts is optional structured enrichment and may be built separately",
            "live SEC submissions include broad filing families; source/form controls are required downstream",
        ],
    }
    manifest_json.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    write_report(report_md, manifest, summary, pit)
    print(json.dumps({"summary": summary, "pit_validation": pit, "outputs": manifest["outputs"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
