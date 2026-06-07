from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from finportfolio_ir.portfolio_summary import summarize_portfolio
from finportfolio_ir.us_macro_rules import build_macro_portfolio_translation, build_us_macro_dashboard


class USMacroDashboardTests(unittest.TestCase):
    def test_positive_macro_snapshot_is_risk_on(self):
        dashboard = build_us_macro_dashboard(
            {
                "real_10y_yield": 0.4,
                "investment_grade_credit_spread": 0.9,
                "payrolls_3m_avg": 220000,
                "unemployment_3m_change": 0.0,
                "retail_sales_yoy": 3.0,
                "sp500_earnings_revision_3m": 0.04,
                "dxy_yoy": -2.0,
                "vix": 14.0,
            }
        )

        self.assertEqual(dashboard["market_regime"], "risk_on")
        self.assertEqual(len(dashboard["what_matters_cards"]), 3)
        self.assertTrue(all(card["collapsed"] for card in dashboard["what_matters_cards"]))

    def test_negative_macro_snapshot_is_risk_off(self):
        dashboard = build_us_macro_dashboard(
            {
                "real_10y_yield": 2.4,
                "investment_grade_credit_spread": 2.0,
                "payrolls_3m_avg": 40000,
                "unemployment_3m_change": 0.5,
                "retail_sales_yoy": -1.0,
                "sp500_earnings_revision_3m": -0.04,
                "dxy_yoy": 8.0,
                "vix": 29.0,
            }
        )

        self.assertEqual(dashboard["market_regime"], "risk_off")
        self.assertTrue(all(card["tone"] == "negative" for card in dashboard["what_matters_cards"]))

    def test_portfolio_summary_and_translation_are_deterministic(self):
        summary = summarize_portfolio(
            [
                {"ticker": "AAPL", "purchase_price": 100, "quantity": 10},
                {"ticker": "JPM", "purchase_price": 50, "quantity": 10},
                {"ticker": "CVX", "purchase_price": 100, "quantity": 2},
            ]
        )
        translation = build_macro_portfolio_translation(
            {
                "real_10y_yield": 2.2,
                "investment_grade_credit_spread": 1.9,
                "payrolls_3m_avg": 60000,
                "unemployment_3m_change": 0.4,
            },
            summary["sector_weights"],
        )

        self.assertEqual(summary["positions"], 3)
        self.assertEqual(summary["dominant_block"], "Information Technology")
        self.assertEqual(len(translation["cards"]), 3)
        self.assertIn(translation["cards"][0]["tone"], {"neutral", "negative"})


if __name__ == "__main__":
    unittest.main()
