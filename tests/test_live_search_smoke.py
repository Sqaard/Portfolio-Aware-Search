import unittest

from evaluation.run_live_search_smoke import evaluate_smoke_case, run_smoke_cases, summarize_smoke_rows


class LiveSearchSmokeTests(unittest.TestCase):
    def test_evaluate_smoke_case_passes_guarded_evidence_unit_payload(self):
        row = evaluate_smoke_case(
            {
                "case_id": "risk",
                "query": "Apple risk factors",
                "expect_gate_enabled": "true",
                "expect_gate_active": "true",
                "expect_top_grain": "evidence_unit",
                "expect_any_unit_signatures": "sec_section:risk_factors",
                "expect_promotion_status": "accepted",
                "max_latency_ms": "1000",
            },
            {
                "count": 1,
                "raw_count": 1,
                "evidence_unit_gate": {
                    "enabled": True,
                    "active": True,
                    "active_result_count": 1,
                    "mode": "evidence_unit_calibrated_source_type_v1",
                    "promotion_status": "accepted",
                    "feature_flag_enabled": True,
                },
                "results": [
                    {
                        "doc_id": "d1",
                        "title": "Apple 10-K Item 1A Risk Factors",
                        "search_grain": "evidence_unit",
                        "evidence_unit_type": "sec_section",
                        "evidence_unit_claim_type": "risk_factors",
                        "source_type": "sec_filing_section",
                    }
                ],
            },
            120.0,
        )

        self.assertTrue(row["passed"])
        self.assertEqual(row["failures"], "")
        self.assertEqual(row["top_evidence_unit_claim_type"], "risk_factors")

    def test_evaluate_smoke_case_fails_when_promotion_is_blocked(self):
        row = evaluate_smoke_case(
            {
                "case_id": "risk",
                "query": "Apple risk factors",
                "expect_gate_enabled": "true",
                "expect_gate_active": "true",
                "expect_top_grain": "evidence_unit",
                "expect_any_unit_signatures": "sec_section:risk_factors",
                "expect_promotion_status": "accepted",
            },
            {
                "count": 1,
                "raw_count": 1,
                "evidence_unit_gate": {
                    "enabled": False,
                    "active": False,
                    "promotion_status": "block_promotion",
                },
                "results": [{"doc_id": "d1", "search_grain": "document"}],
            },
            50.0,
        )

        self.assertFalse(row["passed"])
        self.assertIn("promotion_status_expected_accepted", row["failures"])
        self.assertIn("gate_enabled_expected_True", row["failures"])

    def test_evaluate_smoke_case_checks_folder_children(self):
        row = evaluate_smoke_case(
            {
                "case_id": "foldered",
                "query": "Apple risk factors",
                "expect_gate_enabled": "true",
                "expect_gate_active": "true",
                "expect_top_grain": "evidence_unit",
                "expect_any_unit_signatures": "sec_section:risk_factors",
                "expect_promotion_status": "accepted",
            },
            {
                "count": 1,
                "raw_count": 10,
                "evidence_unit_gate": {
                    "enabled": True,
                    "active": True,
                    "active_result_count": 10,
                    "promotion_status": "accepted",
                },
                "results": [
                    {
                        "result_kind": "folder",
                        "folder_key": "sec_filings",
                        "folder_children": [
                            {
                                "doc_id": "risk",
                                "title": "Apple Item 1A",
                                "search_grain": "evidence_unit",
                                "evidence_unit_type": "sec_section",
                                "evidence_unit_claim_type": "risk_factors",
                            }
                        ],
                    }
                ],
            },
            80.0,
        )

        self.assertTrue(row["passed"])
        self.assertEqual(row["top_doc_id"], "risk")

    def test_run_smoke_cases_summarizes_failures(self):
        cases = [
            {"case_id": "ok", "query": "ok", "expect_gate_enabled": "false"},
            {"case_id": "bad", "query": "bad", "expect_gate_enabled": "true"},
        ]

        def fetch(query):
            return {
                "count": 1,
                "raw_count": 1,
                "evidence_unit_gate": {
                    "enabled": query == "ok",
                    "active": False,
                    "promotion_status": "accepted",
                },
                "results": [{"doc_id": query, "search_grain": "document"}],
            }

        rows, summary = run_smoke_cases(cases, fetch)

        self.assertEqual(len(rows), 2)
        self.assertEqual(summary["status"], "failed")
        self.assertEqual(summary["failed_count"], 2)

    def test_summarize_smoke_rows_counts_promotion_status(self):
        summary = summarize_smoke_rows(
            [
                {"passed": True, "latency_ms": 10.0, "promotion_status": "accepted"},
                {"passed": False, "latency_ms": 20.0, "promotion_status": "blocked"},
            ]
        )

        self.assertEqual(summary["status"], "failed")
        self.assertEqual(summary["promotion_status_counts"], "accepted:1|blocked:1")


if __name__ == "__main__":
    unittest.main()
