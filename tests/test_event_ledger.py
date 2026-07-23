from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from finportfolio_ir.event_ledger import daily_feature_rows, events_from_document, parse_utc, validate_pit


class EventLedgerTests(unittest.TestCase):
    def test_sec_earnings_exhibit_becomes_pit_safe_event(self) -> None:
        record = {
            "doc_id": "sec_aapl_8k_ex991",
            "title": "Apple Inc. 8-K filing - Exhibit 99.1 Earnings Release",
            "body": "Apple reports quarterly results. Revenue was $90 billion and management expects services growth.",
            "source_type": "sec_filing_exhibit",
            "source_registry_id": "sec_edgar",
            "url": "https://www.sec.gov/Archives/test.htm",
            "published_at": "2023-02-02T21:30:00Z",
            "available_at": "2023-02-02T21:30:00Z",
            "matched_tickers": ["AAPL"],
            "sec_ticker": "AAPL",
            "sec_form": "8-K",
            "sec_section_code": "99.1",
            "event_tags": ["earnings_release_candidate"],
            "document_hash": "hash1",
            "sec": {"cik": "0000320193"},
        }

        events = events_from_document(record)

        self.assertTrue(any(event["event_type"] == "earnings" for event in events))
        self.assertTrue(any(event["event_type"] == "guidance" for event in events))
        self.assertTrue(all(event["point_in_time_valid_flag"] for event in events))
        earnings = next(event for event in events if event["event_type"] == "earnings")
        self.assertTrue(earnings["after_close"])
        self.assertGreater(parse_utc(earnings["decision_time_utc"]), parse_utc(earnings["available_at_utc"]))

    def test_pit_validator_rejects_after_close_same_day_decision(self) -> None:
        event = {
            "event_id": "bad",
            "available_at_utc": "2023-02-02T21:30:00Z",
            "retrieval_cutoff_utc": "2023-02-02T21:31:00Z",
            "decision_time_utc": "2023-02-02T21:31:00Z",
            "after_close": True,
        }

        pit = validate_pit([event])

        self.assertFalse(pit["point_in_time_valid"])
        self.assertEqual(pit["pit_violation_count"], 1)
        self.assertEqual(pit["sample_violations"][0]["reason"], "after_close_same_day_decision")

    def test_daily_features_aggregate_fixed_schema(self) -> None:
        event = {
            "event_id": "evt",
            "ticker": "AAPL",
            "event_type": "risk_stress",
            "decision_time_utc": "2023-02-03T14:30:00Z",
            "extracted_features": {
                "liquidity_stress": 0.7,
                "numeric_evidence_density": 0.4,
                "source_quality_score": 0.95,
            },
        }

        rows = daily_feature_rows([event])

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["ticker"], "AAPL")
        self.assertEqual(rows[0]["risk_stress_event_count"], 1)
        self.assertAlmostEqual(rows[0]["liquidity_stress_mean"], 0.7)
        self.assertIn("source_quality_score_max", rows[0])


if __name__ == "__main__":
    unittest.main()
