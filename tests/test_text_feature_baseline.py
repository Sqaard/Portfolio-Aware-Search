import csv
import json
import tempfile
from pathlib import Path
import unittest

from features.build_text_feature_baseline import build_text_feature_baseline, extract_doc_features
from features.merge_text_features_with_base_panel import merge_text_features


class TextFeatureBaselineTests(unittest.TestCase):
    def test_doc_feature_extraction_detects_risk_and_guidance(self):
        row = {
            "daily_context_id": "2022-01-03:stock:AAPL:1:doc1",
            "doc_id": "doc1",
            "decision_date": "2022-01-03",
            "decision_time": "2022-01-03T14:30:00Z",
            "available_at": "2022-01-02T14:00:00Z",
            "retrieval_layer": "stock",
            "target_ticker": "AAPL",
            "tic": "AAPL",
            "source_type": "sec_filing_exhibit",
            "source_reliability_tier": "official",
            "query_intent_primary": "earnings_guidance",
            "document_split": "test",
            "regime": "inflation_bear_market",
            "title": "Apple earnings release",
            "body_excerpt": "Management raised guidance but noted supply chain risk and margin pressure.",
            "event_tags": ["earnings_release_candidate"],
            "risk_terms": ["supply chain"],
            "final_score": 0.8,
            "decay_weight_30d": 0.9,
        }

        features = extract_doc_features(row)

        self.assertEqual(features["signal_earnings_guidance"], 1)
        self.assertEqual(features["signal_supply_chain"], 1)
        self.assertEqual(features["signal_margin_pressure"], 1)
        self.assertEqual(features["document_split"], "test")
        self.assertEqual(features["regime"], "inflation_bear_market")
        self.assertGreater(features["portfolio_action_relevance"], 0.5)

    def test_official_macro_yield_curve_uses_macro_rule_engine(self):
        row = {
            "daily_context_id": "2010-01-05:portfolio:PORTFOLIO:1:macro1",
            "doc_id": "official_macro_t10y2y_2010-01-04",
            "decision_date": "2010-01-05",
            "decision_time": "2010-01-05T14:30:00Z",
            "available_at": "2010-01-05T14:00:00Z",
            "retrieval_layer": "portfolio",
            "target_ticker": "PORTFOLIO",
            "source_type": "official_macro_release",
            "source_reliability_tier": "official",
            "query_intent_primary": "rates_policy",
            "title": "Official US macro release: 10-Year Minus 2-Year Treasury Spread on 2010-01-04",
            "body_excerpt": (
                "Official US macro observation. Series T10Y2Y: 10-Year Minus 2-Year Treasury Spread. "
                "Value: 2.76 percentage points. Relevant concepts: yield curve, recession risk, credit stress."
            ),
            "event_tags": ["official_macro", "credit", "yield_curve", "credit_stress"],
            "risk_terms": ["yield curve", "recession risk", "credit stress"],
            "final_score": 0.5,
            "decay_weight_30d": 0.9,
        }

        features = extract_doc_features(row)

        self.assertEqual(features["impact_direction"], "positive")
        self.assertEqual(features["signal_macro_rates"], 1)
        self.assertEqual(features["signal_credit"], 1)
        self.assertLess(features["risk_intensity"], 0.35)
        self.assertGreater(features["sentiment_proxy"], 0.0)
        self.assertEqual(features["macro_rule_series_id"], "T10Y2Y")

    def test_official_macro_high_vix_is_negative_risk(self):
        row = {
            "daily_context_id": "2020-03-17:portfolio:PORTFOLIO:1:macro2",
            "doc_id": "official_macro_vixcls_2020-03-16",
            "decision_date": "2020-03-17",
            "decision_time": "2020-03-17T13:30:00Z",
            "available_at": "2020-03-17T14:00:00Z",
            "retrieval_layer": "portfolio",
            "target_ticker": "PORTFOLIO",
            "source_type": "official_macro_release",
            "source_reliability_tier": "official",
            "query_intent_primary": "market_volatility",
            "title": "Official US macro release: CBOE VIX Index on 2020-03-16",
            "body_excerpt": "Official US macro observation. Series VIXCLS: CBOE VIX Index. Value: 82.69 index.",
            "event_tags": ["official_macro", "market_volatility"],
            "risk_terms": ["volatility", "risk appetite"],
            "final_score": 0.8,
            "decay_weight_30d": 1.0,
        }

        features = extract_doc_features(row)

        self.assertEqual(features["impact_direction"], "negative")
        self.assertEqual(features["signal_market_volatility"], 1)
        self.assertGreaterEqual(features["risk_intensity"], 0.8)
        self.assertLess(features["sentiment_proxy"], 0.0)

    def test_baseline_writes_doc_daily_and_teacher_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            contexts = tmp_path / "contexts.jsonl"
            rows = [
                {
                    "daily_context_id": "2022-01-03:portfolio:PORTFOLIO:1:macro1",
                    "doc_id": "macro1",
                    "decision_date": "2022-01-03",
                    "decision_time": "2022-01-03T14:30:00Z",
                    "available_at": "2022-01-02T14:00:00Z",
                    "retrieval_layer": "portfolio",
                    "target_ticker": "PORTFOLIO",
                    "source_type": "official_macro_release",
                    "source_reliability_tier": "official",
                    "query_intent_primary": "rates_policy",
                    "document_split": "train",
                    "regime": "recovery",
                    "title": "Treasury yields rise",
                    "body_excerpt": "Federal Reserve policy and real yields increased.",
                    "event_tags": ["official_macro", "rates"],
                    "risk_terms": ["rates"],
                    "final_score": 0.7,
                    "decay_weight_30d": 0.8,
                },
                {
                    "daily_context_id": "2022-01-03:stock:AAPL:1:doc1",
                    "doc_id": "doc1",
                    "decision_date": "2022-01-03",
                    "decision_time": "2022-01-03T14:30:00Z",
                    "available_at": "2022-01-02T14:00:00Z",
                    "retrieval_layer": "stock",
                    "target_ticker": "AAPL",
                    "tic": "AAPL",
                    "source_type": "sec_filing_section",
                    "source_reliability_tier": "official",
                    "query_intent_primary": "company_risk",
                    "document_split": "train",
                    "regime": "recovery",
                    "title": "AAPL risk factors",
                    "body_excerpt": "Risks include litigation and supply chain disruption.",
                    "event_tags": ["risk_factors"],
                    "risk_terms": ["litigation"],
                    "final_score": 0.6,
                    "decay_weight_30d": 0.75,
                },
            ]
            contexts.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

            diagnostics = build_text_feature_baseline(contexts_path=contexts, output_dir=tmp_path / "out", teacher_size=10)

            self.assertEqual(diagnostics["doc_feature_rows"], 2)
            self.assertEqual(diagnostics["daily_stock_rows"], 1)
            self.assertEqual(diagnostics["daily_portfolio_rows"], 1)
            stock_csv = tmp_path / "out" / "daily_stock_text_features_codex_rule.csv"
            with stock_csv.open(encoding="utf-8", newline="") as handle:
                stock_rows = list(csv.DictReader(handle))
            self.assertEqual(stock_rows[0]["tic"], "AAPL")
            teacher = (tmp_path / "out" / "codex_teacher_seed.jsonl").read_text(encoding="utf-8")
            self.assertIn("teacher_rationale", teacher)
            teacher_rows = [json.loads(line) for line in teacher.splitlines() if line.strip()]
            self.assertIn("document_split", teacher_rows[0])
            self.assertIn("regime", teacher_rows[0])
            self.assertEqual({row["document_split"] for row in teacher_rows}, {"train"})

    def test_merge_text_features_left_joins_base_panel(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            base = tmp_path / "base.csv"
            stock = tmp_path / "stock.csv"
            portfolio = tmp_path / "portfolio.csv"
            base.write_text("date,tic,price\n2022-01-03,AAPL,10\n2022-01-03,MSFT,20\n", encoding="utf-8")
            stock.write_text(
                "date,tic,stock_text_doc_count,stock_text_avg_risk_intensity\n2022-01-03,AAPL,2,0.4\n",
                encoding="utf-8",
            )
            portfolio.write_text(
                "date,portfolio_signal_mna_count,portfolio_signal_mna_flag,portfolio_text_doc_count,portfolio_text_avg_risk_intensity\n2022-01-03,0,0,5,0.2\n",
                encoding="utf-8",
            )

            manifest = merge_text_features(
                base_panel=base,
                stock_features=stock,
                portfolio_features=portfolio,
                output=tmp_path / "merged.csv",
                manifest_output=tmp_path / "manifest.json",
                train_end="2022-02-01",
            )

            self.assertEqual(manifest["base_rows"], 2)
            self.assertEqual(manifest["stock_matched_rows"], 1)
            with (tmp_path / "merged.csv").open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows[0]["stock_text_has_evidence"], "1")
            self.assertEqual(rows[1]["stock_text_has_evidence"], "0")
            self.assertEqual(rows[1]["portfolio_text_has_evidence"], "1")
            self.assertNotIn("portfolio_signal_mna_count", rows[0])
            self.assertIn("portfolio_signal_mna_count", manifest["dropped_constant_text_columns"])


if __name__ == "__main__":
    unittest.main()
