from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from features.export_evidence_bundles import build_evidence_bundles


class EvidenceBundleTests(unittest.TestCase):
    def test_build_evidence_bundles_groups_by_query_and_method(self):
        records = [
            {
                "query_id": "q1",
                "method": "full_hybrid_diversified",
                "portfolio_id": "p1",
                "decision_time": "2022-03-15T14:30:00Z",
                "retrieval_cutoff": "2022-03-15T14:30:00Z",
                "rank": 1,
                "doc_id": "d1",
                "title": "Apple guidance",
                "duplicate_cluster_id": "c1",
                "matched_tickers": ["AAPL"],
                "matched_holdings": ["AAPL"],
                "evidence_scope": "stock",
                "final_score": 0.9,
            },
            {
                "query_id": "q1",
                "method": "full_hybrid_diversified",
                "portfolio_id": "p1",
                "decision_time": "2022-03-15T14:30:00Z",
                "retrieval_cutoff": "2022-03-15T14:30:00Z",
                "rank": 2,
                "doc_id": "d2",
                "title": "Fed rates",
                "duplicate_cluster_id": "c2",
                "matched_tickers": [],
                "matched_holdings": [],
                "evidence_scope": "market",
                "final_score": 0.6,
            },
        ]

        bundles = build_evidence_bundles(records)

        self.assertEqual(len(bundles), 1)
        self.assertEqual(bundles[0]["diagnostics"]["document_count"], 2)
        self.assertEqual(bundles[0]["diagnostics"]["covered_holdings"], ["AAPL"])
        self.assertEqual(len(bundles[0]["stock_evidence"]), 1)
        self.assertEqual(len(bundles[0]["market_evidence"]), 1)


if __name__ == "__main__":
    unittest.main()
