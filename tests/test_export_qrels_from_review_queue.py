from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evaluation.export_qrels_from_review_queue import export_review_queue_qrels_rows


class ExportQrelsFromReviewQueueTests(unittest.TestCase):
    def test_exports_human_relevance_with_review_context(self):
        rows = [
            {
                "review_id": "review_0001",
                "query_id": "web_apple_filings",
                "doc_id": "sec_aapl_10q",
                "human_relevance": "3",
                "reason": "top_candidate|weak_query",
                "reviewer_notes": "Good 10-Q match.",
            }
        ]

        qrels, issues = export_review_queue_qrels_rows(
            rows,
            default_label_source="human_search_v1",
            annotator="reviewer_a",
        )

        self.assertEqual(issues, [])
        self.assertEqual(qrels[0]["relevance"], "3")
        self.assertEqual(qrels[0]["label_source"], "human_search_v1")
        self.assertEqual(qrels[0]["annotator"], "reviewer_a")
        self.assertIn("review_id:review_0001", qrels[0]["notes"])
        self.assertIn("Good 10-Q match.", qrels[0]["notes"])

    def test_missing_human_relevance_is_issue_without_fallback(self):
        rows = [
            {
                "review_id": "review_0002",
                "query_id": "web_apple_filings",
                "doc_id": "apple_press_release",
                "bootstrap_relevance": "1",
                "human_relevance": "",
            }
        ]

        qrels, issues = export_review_queue_qrels_rows(rows)

        self.assertEqual(qrels, [])
        self.assertEqual(issues[0]["issue_type"], "missing_human_relevance")

    def test_fallback_bootstrap_is_explicit_development_path(self):
        rows = [
            {
                "review_id": "review_0003",
                "query_id": "web_macro",
                "doc_id": "fred_cpi",
                "bootstrap_relevance": "2",
                "human_relevance": "",
            }
        ]

        qrels, issues = export_review_queue_qrels_rows(
            rows,
            fallback_bootstrap=True,
            default_label_source="bootstrap_review_queue_dryrun",
        )

        self.assertEqual(issues, [])
        self.assertEqual(qrels[0]["relevance"], "2")
        self.assertEqual(qrels[0]["label_source"], "bootstrap_review_queue_dryrun")


if __name__ == "__main__":
    unittest.main()
