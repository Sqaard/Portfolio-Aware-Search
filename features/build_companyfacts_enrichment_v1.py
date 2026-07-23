"""Build a checkpointed SEC companyfacts enrichment layer.

This is a deep/offline READ/FEATURE enrichment. It fetches structured SEC XBRL
companyfacts by ticker, writes one checkpoint per ticker, and can be safely
resumed after network failures or SEC endpoint stalls. It does not promote any
feature into PPO state.
"""

from __future__ import annotations

import argparse
import csv
import json
import multiprocessing as mp
import shutil
import sys
import time
import traceback
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from finportfolio_ir.event_ledger import (  # noqa: E402
    coverage_rows,
    daily_feature_rows,
    dedupe_events,
    events_from_companyfacts,
    read_ticker_metadata,
    summarize_events,
    validate_pit,
    write_csv,
)
from finportfolio_ir.io_utils import read_jsonl, write_jsonl  # noqa: E402


DEFAULT_METADATA = ROOT / "data" / "processed_documents" / "dow30_ticker_metadata.csv"
DEFAULT_BASE_EVENTS = ROOT / "data" / "event_ledger_v1" / "events.jsonl"
DEFAULT_OUTPUT_DIR = ROOT / "data" / "event_ledger_companyfacts_v1"
TERMINAL_OK_STATUSES = {"success", "success_empty", "skipped_missing_cik"}


def parse_date(value: str) -> date:
    return date.fromisoformat(value)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_status_csv(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    rows = list(rows)
    fieldnames = [
        "ticker",
        "status",
        "attempts",
        "event_count",
        "elapsed_seconds",
        "checkpoint_path",
        "error",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def companyfacts_worker(
    *,
    ticker: str,
    metadata_row: dict[str, str],
    cache_dir: str,
    checkpoint_path: str,
    status_path: str,
    start_date: str,
    end_date: str,
    user_agent: str,
    max_facts_per_tag: int,
    request_timeout: int,
    request_retries: int,
) -> None:
    started = time.monotonic()
    status = {
        "ticker": ticker,
        "status": "failed",
        "attempts": 1,
        "event_count": 0,
        "elapsed_seconds": 0.0,
        "checkpoint_path": checkpoint_path,
        "error": "",
        "created_at_utc": utc_now(),
    }
    try:
        cik = str(metadata_row.get("cik", "")).strip()
        if not cik or not cik.strip("0"):
            status["status"] = "skipped_missing_cik"
            write_json(Path(status_path), status)
            write_jsonl(Path(checkpoint_path), [])
            return
        cik_padded = cik.zfill(10)
        cache_path = Path(cache_dir) / f"CIK{cik_padded}.json"
        events = events_from_companyfacts(
            {ticker: metadata_row},
            cache_dir=Path(cache_dir),
            start_date=parse_date(start_date),
            end_date=parse_date(end_date),
            tickers=[ticker],
            force=False,
            user_agent=user_agent,
            max_facts_per_tag=max_facts_per_tag,
            request_timeout=request_timeout,
            request_retries=request_retries,
            raise_on_fetch_error=True,
        )
        write_jsonl(Path(checkpoint_path), events)
        if not cache_path.exists():
            status["status"] = "fetch_failed"
            status["error"] = f"SEC companyfacts cache file was not created: {cache_path}"
        elif events:
            status["status"] = "success"
        else:
            status["status"] = "success_empty"
        status["event_count"] = len(events)
    except Exception:  # pragma: no cover - child process defensive path
        status["error"] = traceback.format_exc(limit=8)
    finally:
        status["elapsed_seconds"] = round(time.monotonic() - started, 3)
        write_json(Path(status_path), status)


def run_ticker_with_timeout(
    *,
    ticker: str,
    metadata_row: dict[str, str],
    output_dir: Path,
    start_date: date,
    end_date: date,
    user_agent: str,
    max_facts_per_tag: int,
    request_timeout: int,
    request_retries: int,
    timeout_seconds: float,
) -> dict[str, Any]:
    checkpoint_path = output_dir / "checkpoints" / f"{ticker}.events.jsonl"
    status_path = output_dir / "status" / f"{ticker}.json"
    cache_dir = output_dir / "raw_cache" / "sec_companyfacts"
    process = mp.Process(
        target=companyfacts_worker,
        kwargs={
            "ticker": ticker,
            "metadata_row": metadata_row,
            "cache_dir": str(cache_dir),
            "checkpoint_path": str(checkpoint_path),
            "status_path": str(status_path),
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "user_agent": user_agent,
            "max_facts_per_tag": max_facts_per_tag,
            "request_timeout": request_timeout,
            "request_retries": request_retries,
        },
    )
    started = time.monotonic()
    process.start()
    process.join(timeout_seconds)
    if process.is_alive():
        process.terminate()
        process.join(5)
        status = {
            "ticker": ticker,
            "status": "timed_out",
            "attempts": 1,
            "event_count": 0,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "checkpoint_path": str(checkpoint_path),
            "error": f"ticker exceeded timeout_seconds={timeout_seconds}",
            "created_at_utc": utc_now(),
        }
        write_json(status_path, status)
        return status
    if status_path.exists():
        return read_json(status_path)
    status = {
        "ticker": ticker,
        "status": "failed",
        "attempts": 1,
        "event_count": 0,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "checkpoint_path": str(checkpoint_path),
        "error": f"worker exited with code {process.exitcode} and wrote no status",
        "created_at_utc": utc_now(),
    }
    write_json(status_path, status)
    return status


def load_success_events(status_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for row in status_rows:
        if row.get("status") != "success":
            continue
        checkpoint = Path(str(row.get("checkpoint_path", "")))
        if checkpoint.exists():
            events.extend(read_jsonl(checkpoint))
    return events


def write_report(path: Path, manifest: dict[str, Any]) -> None:
    summary = manifest["companyfacts_summary"]
    status_counts = manifest["status_counts"]
    lines = [
        "# SEC Companyfacts Enrichment V1",
        "",
        "Checkpointed structured XBRL enrichment for the FinIR event ledger.",
        "",
        "## Contract",
        "",
        "- Right governed: `READ_RETRIEVE_FEATURE`",
        "- Source: `sec_companyfacts` / SEC XBRL API",
        "- PIT invariant: `available_at_utc <= retrieval_cutoff_utc <= decision_time_utc`",
        "- Promotion status: `not_ppo_ready`; this is a structured evidence layer, not alpha proof.",
        "",
        "## Result",
        "",
        f"- Tickers requested: `{manifest['ticker_count']}`",
        f"- Companyfacts event rows: `{summary['event_count']}`",
        f"- PIT violations: `{manifest['companyfacts_pit_validation']['pit_violation_count']}`",
        f"- Merged output written: `{manifest['merged_output_written']}`",
        "",
        "## Status Counts",
        "",
        "| status | count |",
        "| --- | ---: |",
    ]
    for key, value in status_counts.items():
        lines.append(f"| `{key}` | {value} |")
    lines.extend(
        [
            "",
            "## Outputs",
            "",
            f"- Companyfacts only: `{manifest['outputs']['companyfacts_events_jsonl']}`",
            f"- Daily companyfacts features: `{manifest['outputs']['companyfacts_daily_features_csv']}`",
            f"- Status CSV: `{manifest['outputs']['status_csv']}`",
            f"- Retry queue: `{manifest['outputs']['retry_queue_csv']}`",
            f"- Manifest: `{manifest['outputs']['manifest_json']}`",
        ]
    )
    if manifest["outputs"].get("merged_events_jsonl"):
        lines.append(f"- Base + companyfacts events: `{manifest['outputs']['merged_events_jsonl']}`")
        lines.append(f"- Base + companyfacts daily features: `{manifest['outputs']['merged_daily_features_csv']}`")
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- This batch is resume-safe: successful ticker checkpoints are reused unless `--restart` is passed.",
            "- Timed-out or failed tickers are listed in the retry queue and can be retried without deleting successful checkpoints.",
            "- SEC companyfacts is numerically rich but filing-date based; use it as structured corroboration, not immediate event timing.",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata", type=Path, default=DEFAULT_METADATA)
    parser.add_argument("--base-events", type=Path, default=DEFAULT_BASE_EVENTS)
    parser.add_argument("--no-merge-base", action="store_true", help="Only write companyfacts outputs, not base+companyfacts merged outputs.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--start-date", default="2010-01-01")
    parser.add_argument("--end-date", default=date.today().isoformat())
    parser.add_argument("--tickers", nargs="*", default=[])
    parser.add_argument("--limit", type=int, default=0, help="Debug limit over selected tickers; 0 means all selected tickers.")
    parser.add_argument("--max-facts-per-tag", type=int, default=250)
    parser.add_argument("--ticker-timeout-seconds", type=float, default=90.0)
    parser.add_argument("--request-timeout-seconds", type=int, default=25)
    parser.add_argument("--request-retries", type=int, default=4)
    parser.add_argument("--max-retries", type=int, default=1)
    parser.add_argument("--retry-sleep-seconds", type=float, default=3.0)
    parser.add_argument("--user-agent", default="FinPortfolioIR/0.1 research contact: local@example.com")
    parser.add_argument("--restart", action="store_true", help="Remove this enrichment output directory before running.")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def safe_restart(output_dir: Path) -> None:
    if not output_dir.exists():
        return
    resolved = output_dir.resolve()
    root = ROOT.resolve()
    if not str(resolved).startswith(str(root)) or resolved.name != "event_ledger_companyfacts_v1":
        raise RuntimeError(f"Refusing to remove unexpected path: {resolved}")
    shutil.rmtree(resolved)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    start_date = parse_date(args.start_date)
    end_date = parse_date(args.end_date)
    if args.restart:
        safe_restart(args.output_dir)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    metadata = read_ticker_metadata(args.metadata)
    selected = [ticker.upper() for ticker in args.tickers] if args.tickers else list(metadata)
    selected = [ticker for ticker in selected if ticker in metadata]
    if args.limit:
        selected = selected[: args.limit]
    if args.dry_run:
        print(json.dumps({"status": "dry_run", "tickers": selected, "output_dir": str(args.output_dir)}, ensure_ascii=False, indent=2))
        return 0

    status_rows: list[dict[str, Any]] = []
    for index, ticker in enumerate(selected, start=1):
        status_path = args.output_dir / "status" / f"{ticker}.json"
        checkpoint_path = args.output_dir / "checkpoints" / f"{ticker}.events.jsonl"
        if status_path.exists() and checkpoint_path.exists() and read_json(status_path).get("status") in TERMINAL_OK_STATUSES:
            status = read_json(status_path)
            status["attempts"] = int(status.get("attempts") or 1)
            print(json.dumps({"stage": "companyfacts", "ticker": ticker, "position": index, "total": len(selected), "status": f"resume_{status.get('status')}"}, ensure_ascii=False), file=sys.stderr)
            status_rows.append(status)
            continue
        last_status: dict[str, Any] | None = None
        for attempt in range(1, max(1, args.max_retries) + 1):
            print(json.dumps({"stage": "companyfacts", "ticker": ticker, "position": index, "total": len(selected), "attempt": attempt}, ensure_ascii=False), file=sys.stderr)
            status = run_ticker_with_timeout(
                ticker=ticker,
                metadata_row=metadata[ticker],
                output_dir=args.output_dir,
                start_date=start_date,
                end_date=end_date,
                user_agent=args.user_agent,
                max_facts_per_tag=args.max_facts_per_tag,
                request_timeout=args.request_timeout_seconds,
                request_retries=args.request_retries,
                timeout_seconds=args.ticker_timeout_seconds,
            )
            status["attempts"] = attempt
            write_json(status_path, status)
            last_status = status
            print(json.dumps({"stage": "companyfacts", "ticker": ticker, "status": status.get("status"), "events": status.get("event_count", 0)}, ensure_ascii=False), file=sys.stderr)
            if status.get("status") in TERMINAL_OK_STATUSES:
                break
            if attempt < args.max_retries and args.retry_sleep_seconds > 0:
                time.sleep(args.retry_sleep_seconds)
        status_rows.append(last_status or read_json(status_path))

    status_rows.sort(key=lambda row: str(row.get("ticker", "")))
    write_status_csv(args.output_dir / "companyfacts_status.csv", status_rows)
    retry_rows = [row for row in status_rows if row.get("status") not in TERMINAL_OK_STATUSES]
    write_status_csv(args.output_dir / "companyfacts_retry_queue.csv", retry_rows)

    companyfacts_events = dedupe_events(load_success_events(status_rows))
    companyfacts_pit = validate_pit(companyfacts_events)
    companyfacts_summary = summarize_events(companyfacts_events)
    companyfacts_daily = daily_feature_rows(companyfacts_events)
    companyfacts_coverage = coverage_rows(companyfacts_events)

    companyfacts_events_path = args.output_dir / "companyfacts_events.jsonl"
    companyfacts_daily_path = args.output_dir / "daily_companyfacts_features.csv"
    companyfacts_coverage_path = args.output_dir / "coverage_companyfacts_by_ticker_year.csv"
    companyfacts_pit_path = args.output_dir / "companyfacts_pit_validation.json"
    write_jsonl(companyfacts_events_path, companyfacts_events)
    write_csv(companyfacts_daily_path, companyfacts_daily)
    write_csv(companyfacts_coverage_path, companyfacts_coverage)
    write_json(companyfacts_pit_path, companyfacts_pit)

    merged_events_path = ""
    merged_daily_path = ""
    merged_pit: dict[str, Any] | None = None
    merged_summary: dict[str, Any] | None = None
    if not args.no_merge_base and args.base_events.exists():
        base_events = read_jsonl(args.base_events)
        merged_events = dedupe_events([*base_events, *companyfacts_events])
        merged_events_path = str(args.output_dir / "events_plus_companyfacts.jsonl")
        merged_daily_path = str(args.output_dir / "daily_event_features_plus_companyfacts.csv")
        write_jsonl(merged_events_path, merged_events)
        write_csv(Path(merged_daily_path), daily_feature_rows(merged_events))
        merged_pit = validate_pit(merged_events)
        merged_summary = summarize_events(merged_events)

    status_counts: dict[str, int] = {}
    for row in status_rows:
        key = str(row.get("status", "unknown"))
        status_counts[key] = status_counts.get(key, 0) + 1

    manifest = {
        "status": "completed",
        "created_at_utc": utc_now(),
        "right_governed": "READ_RETRIEVE_FEATURE",
        "source": "sec_companyfacts",
        "metadata": str(args.metadata),
        "base_events": "" if args.no_merge_base else str(args.base_events),
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "ticker_count": len(selected),
        "tickers": selected,
        "max_facts_per_tag": args.max_facts_per_tag,
        "ticker_timeout_seconds": args.ticker_timeout_seconds,
        "request_timeout_seconds": args.request_timeout_seconds,
        "request_retries": args.request_retries,
        "max_retries": args.max_retries,
        "status_counts": status_counts,
        "companyfacts_summary": companyfacts_summary,
        "companyfacts_pit_validation": companyfacts_pit,
        "merged_output_written": bool(merged_events_path),
        "merged_summary": merged_summary,
        "merged_pit_validation": merged_pit,
        "outputs": {
            "companyfacts_events_jsonl": str(companyfacts_events_path),
            "companyfacts_daily_features_csv": str(companyfacts_daily_path),
            "companyfacts_coverage_csv": str(companyfacts_coverage_path),
            "companyfacts_pit_validation_json": str(companyfacts_pit_path),
            "status_csv": str(args.output_dir / "companyfacts_status.csv"),
            "retry_queue_csv": str(args.output_dir / "companyfacts_retry_queue.csv"),
            "manifest_json": str(args.output_dir / "manifest.json"),
            "report_md": str(args.output_dir / "companyfacts_report.md"),
            "merged_events_jsonl": merged_events_path,
            "merged_daily_features_csv": merged_daily_path,
        },
        "promotion_status": "not_ppo_ready",
        "next_firewall_tests": [
            "coverage drift by ticker/year",
            "fact-tag availability and restatement audit",
            "source/form controls versus SEC filing text events",
            "phase-free IC tests before any PPO handoff",
        ],
    }
    write_json(args.output_dir / "manifest.json", manifest)
    write_report(args.output_dir / "companyfacts_report.md", manifest)
    print(json.dumps({"manifest": manifest["outputs"]["manifest_json"], "summary": companyfacts_summary, "pit": companyfacts_pit, "status_counts": status_counts}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    mp.freeze_support()
    raise SystemExit(main())
