from datetime import date
import tempfile
from pathlib import Path
import unittest

from features.build_daily_retrieval_contexts import _age_bucket, _decay, infer_query_intent, is_portfolio_level_candidate, load_base_panel
from features.build_official_macro_documents import DEFAULT_SERIES, build_macro_record
from finportfolio_ir.schema import FinancialDocument


class DailyRetrievalTests(unittest.TestCase):
    def test_base_panel_loader_detects_dates_and_tickers(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "panel.csv"
            path.write_text("date,tic,value\n2022-01-03,AAPL,1\n2022-01-03,MSFT,2\n", encoding="utf-8")

            trading_dates, active_by_date, metadata = load_base_panel(path)

        self.assertEqual(trading_dates, [date(2022, 1, 3)])
        self.assertEqual(active_by_date["2022-01-03"], ["AAPL", "MSFT"])
        self.assertEqual(metadata["date_column"], "date")
        self.assertEqual(metadata["ticker_column"], "tic")

    def test_age_bucket_and_decay_are_deterministic(self):
        self.assertEqual(_age_bucket(0.5), "0_1d")
        self.assertEqual(_age_bucket(7.0), "2_7d")
        self.assertEqual(_age_bucket(30.0), "8_30d")
        self.assertAlmostEqual(_decay(7.0, 7), 0.367879, places=6)

    def test_macro_record_uses_conservative_available_at(self):
        spec = DEFAULT_SERIES[7]  # CPIAUCSL, monthly inflation, 18-day lag.
        record = build_macro_record(spec, date(2020, 1, 1), 258.0)

        self.assertEqual(record["available_at"], "2020-01-19T14:00:00Z")
        self.assertEqual(record["matched_tickers"], ["MARKET"])
        self.assertEqual(record["matched_holdings"], [])
        self.assertIn("inflation", record["risk_terms"])

    def test_query_intent_detects_macro_and_company_risk(self):
        macro = FinancialDocument.from_dict(
            build_macro_record(DEFAULT_SERIES[0], date(2020, 1, 2), 1.8)
        )
        risk = FinancialDocument.from_dict(
            {
                "doc_id": "aapl_risk",
                "title": "AAPL Risk Factors",
                "body": "Risk factors include supply chain and regulation.",
                "source": "SEC EDGAR",
                "source_type": "sec_filing_section",
                "url": "https://example.com",
                "published_at": "2020-01-02T14:00:00Z",
                "first_seen_at": "2020-01-02T14:00:00Z",
                "available_at": "2020-01-02T14:00:00Z",
                "ingested_at": "2020-01-02T14:00:00Z",
                "tickers_detected": ["AAPL"],
                "matched_tickers": ["AAPL"],
                "matched_holdings": ["AAPL"],
                "event_tags": ["risk_factors"],
                "risk_terms": ["regulation"],
            }
        )

        self.assertEqual(infer_query_intent(macro, "portfolio"), "rates_policy")
        self.assertEqual(infer_query_intent(risk, "stock"), "company_risk")

    def test_macro_document_has_no_stock_holding_match(self):
        macro = FinancialDocument.from_dict(
            build_macro_record(DEFAULT_SERIES[0], date(2020, 1, 2), 1.8)
        )

        self.assertNotIn("AAPL", macro.matched_holdings)
        self.assertNotIn("AAPL", macro.tickers_detected)
        self.assertTrue(is_portfolio_level_candidate(macro))


if __name__ == "__main__":
    unittest.main()
