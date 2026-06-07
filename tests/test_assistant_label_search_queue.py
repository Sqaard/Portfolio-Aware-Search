from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evaluation.assistant_label_search_queue import label_row, label_rows


class AssistantLabelSearchQueueTests(unittest.TestCase):
    def test_exact_company_filing_is_highly_relevant(self):
        relevance, reason = label_row(
            {
                "query_id": "web_apple_filings",
                "query": "Apple filings",
                "intent": "company_filings",
                "expected_ticker": "AAPL",
                "source_scope": "sec_filings",
                "source_type": "sec_filing_section",
                "matched_tickers": "AAPL",
                "title": "Apple Inc. 10-Q filing filed 2023-02-03 - Item 1A Risk Factors",
                "event_tags": "AAPL|10-Q|Risk Factors",
            }
        )

        self.assertEqual(relevance, 3)
        self.assertIn("SEC filing", reason)

    def test_wrong_company_risk_result_is_irrelevant(self):
        relevance, reason = label_row(
            {
                "query_id": "web_apple_risk",
                "query": "Apple risk factors",
                "intent": "company_risk",
                "expected_ticker": "AAPL",
                "source_type": "sec_filing_exhibit",
                "matched_tickers": "CSCO",
                "title": "CISCO SYSTEMS, INC. 8-K filing filed 2023-02-15 - Exhibit 99.1",
            }
        )

        self.assertEqual(relevance, 0)
        self.assertIn("wrong company", reason)

    def test_product_press_release_is_weak_for_earnings_guidance(self):
        relevance, reason = label_row(
            {
                "query_id": "web_apple_guidance",
                "query": "Apple earnings guidance",
                "intent": "company_events",
                "expected_ticker": "AAPL",
                "source_type": "company_press_release",
                "folder_key": "company_ir",
                "matched_tickers": "AAPL",
                "event_tags": "AAPL|Company Official",
                "title": "Apple announces the new iPhone SE: a powerful smartphone in an iconic design - Apple",
            }
        )

        self.assertEqual(relevance, 1)
        self.assertIn("weak earnings/guidance", reason)

    def test_direct_macro_series_is_highly_relevant(self):
        relevance, reason = label_row(
            {
                "query_id": "web_vix",
                "query": "VIX market volatility",
                "intent": "macro_volatility",
                "expected_ticker": "MARKET",
                "source_type": "official_macro_release",
                "matched_tickers": "MARKET",
                "event_tags": "Official Macro|Market Volatility",
                "title": "Official US macro release: CBOE VIX Index on 2023-02-10",
            }
        )

        self.assertEqual(relevance, 3)
        self.assertIn("direct official macro", reason)

    def test_label_rows_preserves_existing_labels_without_overwrite(self):
        rows = [{"human_relevance": "2", "reviewer_notes": "manual", "intent": "macro_rates"}]

        labeled = label_rows(rows, overwrite=False)

        self.assertEqual(labeled[0]["human_relevance"], "2")
        self.assertEqual(labeled[0]["reviewer_notes"], "manual")


if __name__ == "__main__":
    unittest.main()
