import unittest

from evaluation.build_human_spotcheck_queue import build_spotcheck_queue, score_row


class HumanSpotcheckQueueTests(unittest.TestCase):
    def test_score_row_prioritizes_top_ranked_wrong_company(self):
        row = {
            "query_id": "q1",
            "query": "Apple risk factors",
            "expected_ticker": "AAPL",
            "source_scope": "sec_filings",
            "source_type": "sec_filing_exhibit",
            "matched_tickers": "CSCO",
        }

        priority, reasons = score_row(
            row,
            rank_primary=1,
            rank_comparison=20,
            relevance=0,
            label_source="assistant_review_v5",
            human_queries={"q1"},
            weak_query_scores={"q1": 0.7},
        )

        self.assertGreater(priority, 150)
        self.assertIn("wrong_company_risk", reasons)
        self.assertIn("weak_query", reasons)
        self.assertIn("rank_disagreement", reasons)

    def test_build_queue_excludes_existing_human_spotcheck_rows(self):
        review_rows = [
            {
                "query_id": "q1",
                "query": "Apple risk factors",
                "doc_id": "human_done",
                "expected_ticker": "AAPL",
                "source_scope": "sec_filings",
                "source_type": "sec_filing_section",
                "matched_tickers": "AAPL",
                "label_source": "human_spotcheck_v1",
                "annotator": "user_chat",
            },
            {
                "query_id": "q1",
                "query": "Apple risk factors",
                "doc_id": "candidate",
                "expected_ticker": "AAPL",
                "source_scope": "sec_filings",
                "source_type": "sec_filing_exhibit",
                "matched_tickers": "CSCO",
                "title": "Cisco 8-K",
                "event_tags": "",
                "excerpt": "",
                "document_path": "/documents/candidate",
            },
        ]
        primary_pool = [
            {"query_id": "q1", "doc_id": "human_done", "rank": "1"},
            {"query_id": "q1", "doc_id": "candidate", "rank": "2"},
        ]
        comparison_pool = [
            {"query_id": "q1", "doc_id": "human_done", "rank": "1"},
            {"query_id": "q1", "doc_id": "candidate", "rank": "20"},
        ]
        qrels = [
            {"query_id": "q1", "doc_id": "human_done", "relevance": "3", "label_source": "human_spotcheck_v1"},
            {"query_id": "q1", "doc_id": "candidate", "relevance": "0", "label_source": "assistant_review_v5"},
        ]

        rows = build_spotcheck_queue(
            review_rows=review_rows,
            primary_pool=primary_pool,
            comparison_pool=comparison_pool,
            qrels_rows=qrels,
            limit=10,
            max_per_query=10,
        )

        self.assertEqual([row["doc_id"] for row in rows], ["candidate"])
        self.assertEqual(rows[0]["spotcheck_id"], "spot_0001")


if __name__ == "__main__":
    unittest.main()
