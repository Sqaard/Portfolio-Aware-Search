from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evaluation.evaluate_qrels_coverage import evaluate_qrels_coverage, summarize_coverage


class QrelsCoverageTests(unittest.TestCase):
    def test_reports_unjudged_top_ranked_documents(self):
        qrels = {"q1": {"d1": 3, "d3": 1}}
        runs = {
            ("candidate", "q1"): [
                {"doc_id": "d1", "rank": 1, "score": 3.0},
                {"doc_id": "d2", "rank": 2, "score": 2.0},
                {"doc_id": "d3", "rank": 3, "score": 1.0},
            ]
        }

        rows = evaluate_qrels_coverage(qrels, runs)
        summary = summarize_coverage(rows)

        self.assertEqual(rows[0]["judged_at_5"], 2)
        self.assertEqual(rows[0]["unjudged_at_5"], 1)
        self.assertIn("d2", rows[0]["unjudged_doc_ids_at_10"])
        self.assertEqual(summary[0]["queries_below_100pct_at_10"], 1)

    def test_coverage_rate_uses_retrieved_count_when_run_shorter_than_k(self):
        qrels = {"q1": {"d1": 3, "d2": 2}}
        runs = {
            ("candidate", "q1"): [
                {"doc_id": "d1", "rank": 1, "score": 3.0},
                {"doc_id": "d2", "rank": 2, "score": 2.0},
            ]
        }

        rows = evaluate_qrels_coverage(qrels, runs)

        self.assertEqual(rows[0]["judged_at_10"], 2)
        self.assertEqual(rows[0]["unjudged_at_10"], 0)
        self.assertEqual(rows[0]["judged_rate_at_10"], 1.0)


if __name__ == "__main__":
    unittest.main()
