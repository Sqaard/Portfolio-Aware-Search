from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from indexing.entity_linking import link_entities_in_text, load_ticker_metadata


class EntityLinkingTests(unittest.TestCase):
    def test_aliases_map_to_expected_tickers(self):
        metadata = load_ticker_metadata(ROOT / "data" / "processed_documents" / "ticker_metadata.csv")
        linked = link_entities_in_text(
            "Azure growth and App Store regulation affected Microsoft and Apple.",
            metadata,
        )

        self.assertIn("MSFT", linked["tickers_detected"])
        self.assertIn("AAPL", linked["tickers_detected"])

    def test_market_does_not_match_plain_word_market(self):
        metadata = load_ticker_metadata(ROOT / "data" / "processed_documents" / "ticker_metadata.csv")
        linked = link_entities_in_text("The US market opened higher after Apple supplier news.", metadata)

        self.assertIn("AAPL", linked["tickers_detected"])
        self.assertNotIn("MARKET", linked["tickers_detected"])

    def test_product_only_match_requires_confirmation(self):
        metadata = load_ticker_metadata(ROOT / "data" / "processed_documents" / "ticker_metadata.csv")
        linked = link_entities_in_text("Azure growth accelerated across enterprise cloud budgets.", metadata)

        self.assertNotIn("MSFT", linked["tickers_detected"])


if __name__ == "__main__":
    unittest.main()
