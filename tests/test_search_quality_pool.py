from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(ROOT))

from evaluation.build_search_quality_pool import (  # noqa: E402
    build_search_quality_pool,
    collect_document_results,
    load_search_quality_queries,
    write_run,
)


class SearchQualityPoolTests(unittest.TestCase):
    def test_collect_document_results_includes_group_leader_and_visible_children(self):
        rows = collect_document_results(
            [
                {
                    "result_kind": "group",
                    "doc_id": "leader",
                    "title": "Latest filing",
                    "group_key": "g1",
                    "group_title": "Filing group",
                    "group_count": 2,
                    "group_children": [{"doc_id": "child", "title": "Older filing"}],
                }
            ],
            folder_key="sec_filings",
            folder_title="SEC filings",
        )

        self.assertEqual([row["doc_id"] for row in rows], ["leader", "child"])
        self.assertEqual(rows[1]["group_key"], "g1")
        self.assertEqual(rows[1]["_surface_kind"], "group_child")
        self.assertEqual(rows[1]["_folder_key"], "sec_filings")

    def test_build_search_quality_pool_opens_folders_and_deduplicates_docs(self):
        def fake_surface(_service, _query, *, limit=10, folder_key=""):
            if folder_key == "sec_filings":
                return {
                    "foldered": False,
                    "results": [
                        {
                            "result_kind": "group",
                            "doc_id": "doc_latest",
                            "title": "Apple Inc. 10-Q filing filed 2023-02-03",
                            "source": "sec.gov",
                            "site_name": "sec.gov",
                            "source_type": "sec_filing_section",
                            "available_at": "2023-02-03T16:00:00Z",
                            "published_at": "2023-02-03T16:00:00Z",
                            "score": 8.0,
                            "signal_strength": 2.0,
                            "matched_tickers": ["AAPL"],
                            "event_tags": ["company_risk"],
                            "group_key": "g_aapl_10q",
                            "group_title": "Apple Inc. 10-Q",
                            "group_count": 2,
                            "group_children": [
                                {
                                    "doc_id": "doc_prior",
                                    "title": "Apple Inc. 10-Q filing filed 2022-10-28",
                                    "source_type": "sec_filing_section",
                                    "available_at": "2022-10-28T16:00:00Z",
                                    "score": 7.0,
                                    "matched_tickers": ["AAPL"],
                                }
                            ],
                        },
                        {
                            "result_kind": "document",
                            "doc_id": "doc_latest",
                            "title": "Duplicate leader from a second path",
                            "score": 1.0,
                        },
                    ],
                }
            return {
                "foldered": True,
                "results": [
                    {
                        "result_kind": "folder",
                        "folder_key": "sec_filings",
                        "folder_title": "SEC filings",
                        "rank": 1,
                    }
                ],
            }

        with patch("evaluation.build_search_quality_pool.web_search_surface_results", side_effect=fake_surface):
            rows = build_search_quality_pool(
                object(),
                [
                    {
                        "query_id": "q1",
                        "query": "Apple filings",
                        "intent": "company_filings",
                        "expected_ticker": "AAPL",
                        "source_scope": "sec_filings",
                        "description": "test",
                    }
                ],
                qrels={"q1": {"doc_latest": 3}},
            )

        self.assertEqual([row["doc_id"] for row in rows], ["doc_latest", "doc_prior"])
        self.assertEqual(rows[0]["rank"], 1)
        self.assertEqual(rows[0]["folder_key"], "sec_filings")
        self.assertEqual(rows[0]["existing_relevance"], 3)
        self.assertEqual(rows[0]["document_path"], "/documents/doc_latest")
        self.assertEqual(rows[1]["surface_kind"], "group_child")

    def test_query_loader_requires_unique_ids(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "queries.csv"
            path.write_text(
                "\n".join(
                    [
                        "query_id,query,intent",
                        "q1,Apple filings,company_filings",
                        "q1,JPM filings,company_filings",
                    ]
                ),
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                load_search_quality_queries(path)

    def test_write_run_exports_metric_compatible_rows(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "run.csv"
            write_run(
                path,
                [
                    {"query_id": "q1", "doc_id": "d1", "rank": 1, "score": 2.5},
                    {"query_id": "q1", "doc_id": "d2", "rank": 2, "score": 1.5},
                ],
                method="web_search_current",
            )

            text = path.read_text(encoding="utf-8")

        self.assertIn("query_id,doc_id,rank,score,method", text)
        self.assertIn("q1,d1,1,2.5,web_search_current", text)


if __name__ == "__main__":
    unittest.main()
