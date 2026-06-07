from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from finportfolio_ir.query_intent import classify_query_intent
from web_app import FinPortfolioWebService


class QueryIntentTests(unittest.TestCase):
    def test_routes_filing_numeric_query_to_sec_and_structured_facts(self):
        intent = classify_query_intent("What was Apple's EPS in the latest 10-K?")

        self.assertEqual(intent.primary_intent, "filing_fact_lookup")
        self.assertIn("sec_filings", intent.source_routes)
        self.assertIn("structured_facts", intent.source_routes)
        self.assertIn("AAPL", intent.matched_tickers)
        self.assertTrue(intent.needs_structured_data)
        self.assertIn("earnings", intent.field_labels)

    def test_routes_macro_portfolio_impact_query(self):
        intent = classify_query_intent("How do Fed rates and credit spreads affect my portfolio?")

        self.assertEqual(intent.primary_intent, "portfolio_impact")
        self.assertIn("official_macro", intent.source_routes)
        self.assertIn("rates", intent.field_labels)
        self.assertIn("credit", intent.field_labels)
        self.assertIn("portfolio_context_language", intent.reason_tags)

    def test_routes_favorite_external_posts_without_trusting_source(self):
        intent = classify_query_intent("Show favorite blog posts and Twitter mood about Nvidia")

        self.assertEqual(intent.primary_intent, "news_sentiment_lookup")
        self.assertIn("favorite_websites", intent.source_routes)
        self.assertIn("external_web", intent.source_routes)
        self.assertTrue(intent.external_or_user_source)
        self.assertIn("NVDA", intent.matched_tickers)

    def test_search_api_exposes_query_intent_metadata(self):
        service = FinPortfolioWebService()
        payload = service.search_payload("Apple buyback impact on my portfolio")

        self.assertIn("query_intent", payload)
        self.assertEqual(payload["query_intent"]["primary_intent"], "portfolio_impact")
        self.assertIn("AAPL", payload["query_intent"]["matched_tickers"])


if __name__ == "__main__":
    unittest.main()
