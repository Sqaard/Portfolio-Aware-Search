from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from features.build_fingpt_handoff_package import build_handoff_package
from features.validate_fingpt_handoff import validate_context_records


def _context_record(**overrides):
    record = {
        "query_id": "q1",
        "method": "full_hybrid_diversified",
        "portfolio_id": "p1",
        "decision_date": "2022-03-15",
        "decision_time": "2022-03-15T14:30:00Z",
        "retrieval_cutoff": "2022-03-15T14:30:00Z",
        "rank": 1,
        "doc_id": "d1",
        "source": "sample",
        "source_type": "sample",
        "published_at": "2022-03-15T12:00:00Z",
        "available_at": "2022-03-15T12:01:00Z",
        "title": "Apple guidance",
        "body_excerpt": "Apple guidance and revenue context.",
        "url": "",
        "matched_tickers": ["AAPL"],
        "matched_holdings": ["AAPL"],
        "portfolio_weight_sum": 0.12,
        "sparse_score": 0.8,
        "dense_score": 0.0,
        "entity_score": 1.0,
        "portfolio_exposure_score": 1.0,
        "recency_score": 0.9,
        "event_importance_score": 0.5,
        "source_credibility_score": 0.5,
        "final_score": 0.84,
        "duplicate_cluster_id": "c1",
        "event_tags": ["guidance"],
        "risk_terms": [],
        "evidence_scope": "stock",
        "retrieval_reason_tags": ["stock_scope", "exact_ticker"],
        "document_hash": "hash1",
    }
    record.update(overrides)
    return record


class FinGPTHandoffTests(unittest.TestCase):
    def test_handoff_validator_accepts_causal_context(self):
        report = validate_context_records([_context_record()])

        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["row_count"], 1)
        self.assertEqual(report["hard_issue_count"], 0)

    def test_handoff_validator_rejects_future_context(self):
        report = validate_context_records(
            [_context_record(available_at="2022-03-15T15:00:00Z")]
        )

        self.assertEqual(report["status"], "failed")
        self.assertIn("available_after_decision", {issue["issue_type"] for issue in report["issues"]})

    def test_handoff_package_writes_expected_artifacts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest = build_handoff_package(
                retrieval_records=[_context_record()],
                output_dir=Path(tmpdir) / "handoff",
                source_path="retrieved.jsonl",
            )

            output_dir = Path(tmpdir) / "handoff"
            self.assertEqual(manifest["status"], "passed")
            self.assertTrue((output_dir / "retrieved_contexts.jsonl").exists())
            self.assertTrue((output_dir / "evidence_bundles.jsonl").exists())
            self.assertTrue((output_dir / "handoff_validation.json").exists())
            self.assertTrue((output_dir / "handoff_report.html").exists())


if __name__ == "__main__":
    unittest.main()
