from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from crawler.live_incremental_fetch import collect_live_company_ir_records, fetch_live_incremental
from deploy.process_live_llm_queue import process_queue
from indexing.build_live_documents import merge_documents
from finportfolio_ir.io_utils import read_jsonl, write_jsonl


class LiveIncrementalFetchTests(unittest.TestCase):
    def test_merge_documents_prefers_later_doc_id_record_and_skips_duplicate_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            historical = root / "historical.jsonl"
            live = root / "live.jsonl"
            write_jsonl(
                historical,
                [
                    {"doc_id": "a", "document_hash": "ha", "available_at": "2023-01-01T00:00:00Z", "title": "old a"},
                    {"doc_id": "b", "document_hash": "hb", "available_at": "2023-01-02T00:00:00Z", "title": "old b"},
                ],
            )
            write_jsonl(
                live,
                [
                    {"doc_id": "a", "document_hash": "ha2", "available_at": "2026-01-01T00:00:00Z", "title": "new a"},
                    {"doc_id": "c", "document_hash": "hb", "available_at": "2026-01-02T00:00:00Z", "title": "dup hash"},
                ],
            )

            merged = merge_documents([historical, live])

            self.assertEqual([row["doc_id"] for row in merged], ["b", "a"])
            self.assertEqual(merged[-1]["title"], "new a")

    def test_live_fetch_is_idempotent_and_queues_new_official_docs(self) -> None:
        today = datetime.now(timezone.utc).date().isoformat()
        sec_payload = {
            "filings": {
                "recent": {
                    "accessionNumber": ["0000320193-26-000001"],
                    "filingDate": [today],
                    "reportDate": [today],
                    "acceptanceDateTime": [f"{today}T16:00:00.000"],
                    "form": ["10-Q"],
                    "primaryDocument": ["aapl-20260530.htm"],
                }
            }
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            metadata = root / "metadata.csv"
            registry = root / "source_registry.csv"
            raw_output = root / "live_raw.jsonl"
            processed_output = root / "live_processed.jsonl"
            queue_output = root / "queue.jsonl"
            state_output = root / "state.json"
            metadata.write_text(
                "ticker,cik,official_name,company_name,common_name,sector\n"
                "AAPL,0000320193,Apple Inc.,Apple,Apple,Information Technology\n"
                "MARKET,,Market,Market,Market,Macro\n",
                encoding="utf-8",
            )
            registry.write_text(
                "source_registry_id,name,base_url,source_type,source_reliability_tier,robots_policy,content_license_note,source_credibility,preferred_for_v1,notes\n"
                "sec_edgar,SEC EDGAR,https://www.sec.gov/,sec_filing,official,Respect SEC fair access,Public SEC filing,0.95,true,\n"
                "treasury_us,U.S. Treasury via FRED,https://fred.stlouisfed.org/,official_macro_release,official,Public CSV,Public macro series,0.90,true,\n",
                encoding="utf-8",
            )

            with patch("crawler.live_incremental_fetch._request_json", return_value=sec_payload), patch(
                "crawler.live_incremental_fetch._request_text",
                return_value="<html><body>Apple quarterly report revenue risk factors liquidity.</body></html>",
            ), patch(
                "crawler.live_incremental_fetch._download_fred_rows",
                return_value=[{"DATE": today, "value": "4.25"}],
            ):
                first = fetch_live_incremental(
                    metadata_path=metadata,
                    source_registry_path=registry,
                    raw_output=raw_output,
                    processed_output=processed_output,
                    queue_output=queue_output,
                    state_output=state_output,
                    tickers=["AAPL"],
                    forms={"10-Q"},
                    lookback_days=7,
                    max_sec_docs=5,
                    macro_series=["DGS10"],
                    macro_lookback_days=7,
                    max_macro_observations=5,
                    company_sources_path=root / "company_sources.csv",
                    company_lookback_days=7,
                    max_company_sources=0,
                    max_company_docs=0,
                    max_company_pages_per_source=0,
                    max_company_candidates_per_source=1,
                    max_company_docs_per_source=1,
                    company_min_body_words=20,
                    company_timeout_seconds=3,
                    user_agent="test",
                    sleep_seconds=0.0,
                    body_chars=2000,
                )
                second = fetch_live_incremental(
                    metadata_path=metadata,
                    source_registry_path=registry,
                    raw_output=raw_output,
                    processed_output=processed_output,
                    queue_output=queue_output,
                    state_output=state_output,
                    tickers=["AAPL"],
                    forms={"10-Q"},
                    lookback_days=7,
                    max_sec_docs=5,
                    macro_series=["DGS10"],
                    macro_lookback_days=7,
                    max_macro_observations=5,
                    company_sources_path=root / "company_sources.csv",
                    company_lookback_days=7,
                    max_company_sources=0,
                    max_company_docs=0,
                    max_company_pages_per_source=0,
                    max_company_candidates_per_source=1,
                    max_company_docs_per_source=1,
                    company_min_body_words=20,
                    company_timeout_seconds=3,
                    user_agent="test",
                    sleep_seconds=0.0,
                    body_chars=2000,
                )

            self.assertEqual(first["processed_appended"], 2)
            self.assertEqual(first["queued_for_llm"], 2)
            self.assertEqual(second["processed_appended"], 0)
            self.assertEqual(len(read_jsonl(processed_output)), 2)
            self.assertEqual(len(read_jsonl(queue_output)), 2)

    def test_company_ir_live_adapter_filters_to_recent_unseen_documents(self) -> None:
        today = datetime.now(timezone.utc).date()
        old_day = today.replace(year=today.year - 1)
        fresh_doc = {
            "doc_id": "company_aapl_fresh",
            "title": "Apple fresh investor update",
            "body": "Apple investor update with revenue margin demand and guidance details.",
            "source": "company_official_aapl",
            "source_type": "company_press_release",
            "url": "https://www.apple.com/newsroom/fresh/",
            "published_at": f"{today.isoformat()}T16:00:00Z",
            "first_seen_at": f"{today.isoformat()}T16:00:00Z",
            "available_at": f"{today.isoformat()}T16:00:00Z",
            "ingested_at": f"{today.isoformat()}T16:00:00Z",
            "document_hash": "fresh_hash",
        }
        old_doc = {
            **fresh_doc,
            "doc_id": "company_aapl_old",
            "available_at": f"{old_day.isoformat()}T16:00:00Z",
            "document_hash": "old_hash",
        }
        with tempfile.TemporaryDirectory() as tmp:
            sources = Path(tmp) / "company_sources.csv"
            metadata = Path(tmp) / "metadata.csv"
            sources.write_text("ticker,company,source_type,url,needs_js,crawler_grade\n", encoding="utf-8")
            metadata.write_text("ticker,cik,official_name,sector\nAAPL,0000320193,Apple Inc.,Technology\n", encoding="utf-8")
            with patch(
                "crawler.live_incremental_fetch.read_sources_csv",
                return_value=[
                    {
                        "ticker": "AAPL",
                        "company": "Apple Inc.",
                        "source_type": "company_press_release_archive",
                        "url": "https://www.apple.com/newsroom/archive/",
                        "needs_js": "no",
                        "crawler_grade": "crawler_ready",
                    }
                ],
            ), patch(
                "crawler.live_incremental_fetch.discover_documents_from_sources",
                return_value=([fresh_doc, old_doc, {**fresh_doc, "doc_id": "company_aapl_seen"}], [], [], []),
            ):
                docs, errors = collect_live_company_ir_records(
                    company_sources_path=sources,
                    metadata_path=metadata,
                    tickers=["AAPL"],
                    lookback_days=30,
                    max_sources=5,
                    max_docs=10,
                    max_pages_per_source=1,
                    max_candidates_per_source=5,
                    max_docs_per_source=2,
                    min_body_words=20,
                    user_agent="test",
                    sleep_seconds=0.0,
                    timeout_seconds=3,
                    seen_doc_ids={"company_aapl_seen"},
                )

        self.assertEqual([doc["doc_id"] for doc in docs], ["company_aapl_fresh"])
        self.assertEqual(docs[0]["retrieval_layer"], "live_company_ir_incremental")
        self.assertEqual(errors, [])

    def test_llm_queue_worker_dry_run_marks_documents_complete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            queue = root / "queue.jsonl"
            docs = root / "docs.jsonl"
            output = root / "summaries.jsonl"
            write_jsonl(
                docs,
                [
                    {
                        "doc_id": "doc1",
                        "title": "Apple official update",
                        "body": "Revenue increased and margin pressure was discussed.",
                        "source_type": "company_press_release",
                        "available_at": "2026-05-30T12:00:00Z",
                    }
                ],
            )
            write_jsonl(
                queue,
                [
                    {
                        "doc_id": "doc1",
                        "status": "pending",
                        "priority": 80,
                        "source_type": "company_press_release",
                        "available_at": "2026-05-30T12:00:00Z",
                    }
                ],
            )

            summary = process_queue(queue_path=queue, documents_path=docs, output_path=output, limit=5, dry_run=True)

            self.assertEqual(summary["processed"], 1)
            self.assertEqual(read_jsonl(queue)[0]["status"], "complete")
            self.assertEqual(read_jsonl(output)[0]["status"], "complete")


if __name__ == "__main__":
    unittest.main()
