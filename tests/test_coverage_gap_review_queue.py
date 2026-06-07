from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evaluation.build_coverage_gap_review_queue import build_coverage_gap_queue


class CoverageGapReviewQueueTests(unittest.TestCase):
    def test_adds_unjudged_top_candidate_and_baseline_docs(self):
        existing = [
            {
                "review_id": "review_0001",
                "query_id": "q1",
                "doc_id": "already_reviewed",
                "rank_candidate": "1",
                "bootstrap_relevance": "3",
                "reason": "top_candidate",
            }
        ]
        candidate_pool = [
            {"query_id": "q1", "doc_id": "already_reviewed", "rank": "1", "query": "MSFT margins", "title": "Reviewed"},
            {"query_id": "q1", "doc_id": "candidate_gap", "rank": "2", "query": "MSFT margins", "title": "Candidate gap"},
        ]
        baseline_pool = [
            {"query_id": "q1", "doc_id": "baseline_gap", "rank": "1", "query": "MSFT margins", "title": "Baseline gap"},
        ]
        judged_qrels = [{"query_id": "q1", "doc_id": "already_reviewed", "relevance": "3"}]
        bootstrap_qrels = [
            {"query_id": "q1", "doc_id": "candidate_gap", "relevance": "2"},
            {"query_id": "q1", "doc_id": "baseline_gap", "relevance": "1"},
        ]

        queue = build_coverage_gap_queue(
            existing_queue=existing,
            baseline_pool=baseline_pool,
            candidate_pool=candidate_pool,
            judged_qrels=judged_qrels,
            bootstrap_qrels=bootstrap_qrels,
            top_k=10,
            limit=10,
        )

        by_doc = {row["doc_id"]: row for row in queue}
        self.assertEqual(set(by_doc), {"already_reviewed", "candidate_gap", "baseline_gap"})
        self.assertIn("coverage_gap_candidate_top10", by_doc["candidate_gap"]["reason"])
        self.assertIn("coverage_gap_baseline_top10", by_doc["baseline_gap"]["reason"])
        self.assertEqual(by_doc["candidate_gap"]["bootstrap_relevance"], "2")
        self.assertEqual(by_doc["baseline_gap"]["bootstrap_relevance"], "1")
        self.assertEqual([row["review_id"] for row in queue], ["review_0001", "review_0002", "review_0003"])

    def test_respects_limit_after_existing_rows(self):
        queue = build_coverage_gap_queue(
            existing_queue=[{"query_id": "q1", "doc_id": "existing"}],
            baseline_pool=[{"query_id": "q1", "doc_id": "baseline_gap", "rank": "1"}],
            candidate_pool=[{"query_id": "q1", "doc_id": "candidate_gap", "rank": "1"}],
            judged_qrels=[],
            bootstrap_qrels=[],
            top_k=10,
            limit=2,
        )

        self.assertEqual(len(queue), 2)
        self.assertEqual(queue[0]["doc_id"], "existing")


if __name__ == "__main__":
    unittest.main()
