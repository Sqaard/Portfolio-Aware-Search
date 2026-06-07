import unittest

from features.run_mistral_teacher_seed_comparison import compare_prediction, normalize_prediction


class MistralTeacherSeedComparisonTests(unittest.TestCase):
    def test_normalize_prediction_clips_values_and_signals(self):
        parsed = {
            "impact_direction": "Positive",
            "risk_intensity": 1.5,
            "uncertainty_intensity": -1,
            "sentiment_proxy": 2,
            "portfolio_action_relevance": 0.7,
            "active_signals": ["signal_earnings_guidance", "bad_signal", "signal_credit"],
            "confidence": "High",
        }

        normalized = normalize_prediction(parsed)

        self.assertEqual(normalized["impact_direction"], "positive")
        self.assertEqual(normalized["risk_intensity"], 1.0)
        self.assertEqual(normalized["uncertainty_intensity"], 0.0)
        self.assertEqual(normalized["sentiment_proxy"], 1.0)
        self.assertEqual(normalized["active_signals"], ["signal_credit", "signal_earnings_guidance"])

    def test_compare_prediction_reports_signal_disagreements(self):
        teacher = {
            "teacher_id": "t1",
            "doc_id": "d1",
            "document_split": "train",
            "regime": "post_gfc_recovery",
            "labels": {
                "impact_direction": "mixed",
                "risk_intensity": 0.6,
                "uncertainty_intensity": 0.4,
                "sentiment_proxy": -0.2,
                "portfolio_action_relevance": 0.8,
                "active_signals": ["signal_company_risk", "signal_credit"],
            },
        }
        prediction = {
            "mistral_labels": {
                "impact_direction": "negative",
                "risk_intensity": 0.5,
                "uncertainty_intensity": 0.6,
                "sentiment_proxy": -0.1,
                "portfolio_action_relevance": 0.7,
                "active_signals": ["signal_credit", "signal_legal_regulatory"],
            },
            "error": "",
        }

        row = compare_prediction(teacher, prediction)

        self.assertEqual(row["impact_direction_match"], 0)
        self.assertEqual(row["signal_tp"], 1)
        self.assertEqual(row["signal_fp"], 1)
        self.assertEqual(row["signal_fn"], 1)
        self.assertEqual(row["missing_signals"], "signal_company_risk")
        self.assertEqual(row["extra_signals"], "signal_legal_regulatory")


if __name__ == "__main__":
    unittest.main()
