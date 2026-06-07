from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evaluation.build_search_review_queue import build_review_queue


class SearchReviewQueueTests(unittest.TestCase):
    def test_review_queue_keeps_query_coverage_and_doc_ids(self):
        baseline_pool = [
            {"query_id": "q1", "doc_id": "a", "rank": "1"},
            {"query_id": "q2", "doc_id": "c", "rank": "1"},
        ]
        candidate_pool = [
            {"query_id": "q1", "doc_id": "a", "rank": "1", "query": "Apple filings", "title": "A"},
            {"query_id": "q1", "doc_id": "b", "rank": "2", "query": "Apple filings", "title": "B"},
            {"query_id": "q2", "doc_id": "c", "rank": "1", "query": "VIX", "title": "C"},
            {"query_id": "q2", "doc_id": "d", "rank": "2", "query": "VIX", "title": "D"},
        ]
        qrels = [
            {"query_id": "q1", "doc_id": "a", "relevance": "3"},
            {"query_id": "q1", "doc_id": "b", "relevance": "1"},
            {"query_id": "q2", "doc_id": "c", "relevance": "3"},
            {"query_id": "q2", "doc_id": "d", "relevance": "1"},
        ]
        metrics = [{"query_id": "q1", "ndcg_at_10": "0.5"}, {"query_id": "q2", "ndcg_at_10": "1.0"}]

        queue = build_review_queue(
            baseline_pool=baseline_pool,
            candidate_pool=candidate_pool,
            qrels=qrels,
            candidate_metrics=metrics,
            limit=4,
            min_per_query=1,
        )

        self.assertEqual(len(queue), 4)
        self.assertTrue({row["query_id"] for row in queue}.issuperset({"q1", "q2"}))
        self.assertTrue(all(row["doc_id"] for row in queue))
        self.assertEqual(queue[0]["review_id"], "review_0001")

    def test_review_queue_forces_top_candidate_coverage_before_priority_sampling(self):
        baseline_pool = [
            {"query_id": "q1", "doc_id": "baseline_borderline", "rank": "1"},
        ]
        candidate_pool = [
            {"query_id": "q1", "doc_id": "candidate_top", "rank": "1", "query": "Microsoft cloud margins", "title": "MSFT 10-Q"},
            {"query_id": "q1", "doc_id": "candidate_second", "rank": "2", "query": "Microsoft cloud margins", "title": "MSFT 8-K"},
            {"query_id": "q1", "doc_id": "baseline_borderline", "rank": "20", "query": "Microsoft cloud margins", "title": "MSFT IR"},
        ]
        qrels = [
            {"query_id": "q1", "doc_id": "candidate_top", "relevance": "3"},
            {"query_id": "q1", "doc_id": "candidate_second", "relevance": "3"},
            {"query_id": "q1", "doc_id": "baseline_borderline", "relevance": "1"},
        ]

        queue = build_review_queue(
            baseline_pool=baseline_pool,
            candidate_pool=candidate_pool,
            qrels=qrels,
            candidate_metrics=[{"query_id": "q1", "ndcg_at_10": "0.0"}],
            limit=2,
            min_per_query=0,
            candidate_top_k_per_query=2,
            baseline_top_k_per_query=2,
        )

        self.assertEqual([row["doc_id"] for row in queue], ["candidate_top", "candidate_second"])


if __name__ == "__main__":
    unittest.main()
