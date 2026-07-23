"""One-command live IR refresh: fetch, merge, and rebuild SQLite FTS index."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from crawler.live_incremental_fetch import (  # noqa: E402
    DEFAULT_METADATA,
    DEFAULT_COMPANY_SOURCES,
    DEFAULT_PROCESSED_OUTPUT,
    DEFAULT_QUEUE_OUTPUT,
    DEFAULT_RAW_OUTPUT,
    DEFAULT_SOURCE_REGISTRY,
    DEFAULT_STATE_OUTPUT,
    DEFAULT_USER_AGENT,
    fetch_live_incremental,
    parse_csv_values,
)
from finportfolio_ir.live_ir_status import build_live_ir_status  # noqa: E402
from indexing.build_live_documents import DEFAULT_HISTORICAL, DEFAULT_LIVE, DEFAULT_OUTPUT, build_live_documents  # noqa: E402
from indexing.build_search_index import build_search_index  # noqa: E402
from deploy.process_live_llm_queue import DEFAULT_OUTPUT as DEFAULT_LLM_SUMMARIES, process_queue  # noqa: E402


DEFAULT_LIVE_INDEX = ROOT / "data" / "live_ir" / "finportfolio_search_live.sqlite"
DEFAULT_REFRESH_MANIFEST = ROOT / "data" / "live_ir" / "live_refresh_manifest.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refresh live official documents and rebuild the live search index.")
    parser.add_argument("--metadata", default=str(DEFAULT_METADATA))
    parser.add_argument("--source-registry", default=str(DEFAULT_SOURCE_REGISTRY))
    parser.add_argument("--tickers", default="", help="Comma-separated tickers. Empty means all tickers in metadata.")
    parser.add_argument("--forms", default="10-K,10-Q,8-K")
    parser.add_argument("--lookback-days", type=int, default=14)
    parser.add_argument("--max-sec-docs", type=int, default=80)
    parser.add_argument("--macro-series", default="")
    parser.add_argument("--macro-lookback-days", type=int, default=14)
    parser.add_argument("--max-macro-observations", type=int, default=120)
    parser.add_argument("--company-sources", default=str(DEFAULT_COMPANY_SOURCES), help="Official company source registry CSV.")
    parser.add_argument("--company-lookback-days", type=int, default=30)
    parser.add_argument("--max-company-sources", type=int, default=10)
    parser.add_argument("--max-company-docs", type=int, default=20)
    parser.add_argument("--max-company-pages-per-source", type=int, default=1)
    parser.add_argument("--max-company-candidates-per-source", type=int, default=12)
    parser.add_argument("--max-company-docs-per-source", type=int, default=2)
    parser.add_argument("--company-min-body-words", type=int, default=80)
    parser.add_argument("--company-timeout-seconds", type=int, default=10)
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT)
    parser.add_argument("--sleep-seconds", type=float, default=0.15)
    parser.add_argument("--body-chars", type=int, default=16000)
    parser.add_argument("--raw-output", default=str(DEFAULT_RAW_OUTPUT))
    parser.add_argument("--processed-output", default=str(DEFAULT_PROCESSED_OUTPUT))
    parser.add_argument("--queue-output", default=str(DEFAULT_QUEUE_OUTPUT))
    parser.add_argument("--state-output", default=str(DEFAULT_STATE_OUTPUT))
    parser.add_argument("--historical", default=str(DEFAULT_HISTORICAL))
    parser.add_argument("--merged-output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--index-output", default=str(DEFAULT_LIVE_INDEX))
    parser.add_argument("--manifest-output", default=str(DEFAULT_REFRESH_MANIFEST))
    parser.add_argument("--process-llm-queue", action="store_true", help="After indexing, process a small LLM enrichment batch.")
    parser.add_argument("--llm-limit", type=int, default=5)
    parser.add_argument("--llm-output", default=str(DEFAULT_LLM_SUMMARIES))
    parser.add_argument("--llm-dry-run", action="store_true")
    args = parser.parse_args(argv)

    fetch_summary = fetch_live_incremental(
        metadata_path=Path(args.metadata),
        source_registry_path=Path(args.source_registry),
        raw_output=Path(args.raw_output),
        processed_output=Path(args.processed_output),
        queue_output=Path(args.queue_output),
        state_output=Path(args.state_output),
        tickers=[ticker.upper() for ticker in parse_csv_values(args.tickers)],
        forms={form.upper() for form in parse_csv_values(args.forms)},
        lookback_days=args.lookback_days,
        max_sec_docs=args.max_sec_docs,
        macro_series=[series.upper() for series in parse_csv_values(args.macro_series)],
        macro_lookback_days=args.macro_lookback_days,
        max_macro_observations=args.max_macro_observations,
        company_sources_path=Path(args.company_sources),
        company_lookback_days=args.company_lookback_days,
        max_company_sources=args.max_company_sources,
        max_company_docs=args.max_company_docs,
        max_company_pages_per_source=args.max_company_pages_per_source,
        max_company_candidates_per_source=args.max_company_candidates_per_source,
        max_company_docs_per_source=args.max_company_docs_per_source,
        company_min_body_words=args.company_min_body_words,
        company_timeout_seconds=args.company_timeout_seconds,
        user_agent=args.user_agent,
        sleep_seconds=args.sleep_seconds,
        body_chars=args.body_chars,
    )
    merge_summary = build_live_documents(
        historical_path=Path(args.historical),
        live_path=Path(args.processed_output),
        output_path=Path(args.merged_output),
    )
    index_summary = build_search_index(
        documents_path=Path(args.merged_output),
        output_path=Path(args.index_output),
    )
    llm_summary = {
        "status": "skipped",
        "reason": "index_first_mode",
        "hint": "Run deploy/process_live_llm_queue.py later, or pass --process-llm-queue.",
    }
    if args.process_llm_queue:
        llm_summary = process_queue(
            queue_path=Path(args.queue_output),
            documents_path=Path(args.merged_output),
            output_path=Path(args.llm_output),
            limit=args.llm_limit,
            dry_run=args.llm_dry_run,
        )
    live_status = build_live_ir_status(
        live_dir=Path(args.processed_output).parent,
        processed_path=Path(args.processed_output),
        merged_path=Path(args.merged_output),
        queue_path=Path(args.queue_output),
        manifest_path=Path(args.manifest_output),
        index_path=Path(args.index_output),
    )
    summary = {
        "status": "completed",
        "fetch": fetch_summary,
        "merge": merge_summary,
        "index": index_summary,
        "llm_enrichment": llm_summary,
        "live_status": live_status,
        "run_server_hint": (
            "$env:FINPORTFOLIO_LIVE_IR='1'; "
            f"python web_app.py --documents-path {args.merged_output} --search-index-path {args.index_output}"
        ),
    }
    manifest_path = Path(args.manifest_output)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
