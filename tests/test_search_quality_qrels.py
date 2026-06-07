from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evaluation.build_search_quality_qrels import build_qrels, grade_pool_row


def pool_row(**overrides):
    row = {
        "query_id": "web_apple_filings",
        "query": "Apple filings",
        "intent": "company_filings",
        "expected_ticker": "AAPL",
        "source_scope": "sec_filings",
        "folder_key": "sec_filings",
        "folder_title": "SEC filings",
        "group_title": "",
        "doc_id": "doc_1",
        "title": "Apple Inc. 10-Q filing filed 2023-02-03 - Item 1A Risk Factors",
        "source_type": "sec_filing_section",
        "matched_tickers": "AAPL",
        "matched_holdings": "",
        "event_tags": "AAPL|10-Q|Risk Factors",
        "risk_terms": "risk",
        "excerpt": "Item 1A. Risk Factors",
    }
    row.update(overrides)
    return row


class SearchQualityQrelsTests(unittest.TestCase):
    def test_company_filing_gets_high_bootstrap_relevance(self):
        qrel = grade_pool_row(pool_row())

        self.assertEqual(qrel["relevance"], "3")
        self.assertIn("source:sec_filings", qrel["notes"])
        self.assertIn("entity:AAPL", qrel["notes"])

    def test_press_release_does_not_satisfy_filings_source_intent(self):
        qrel = grade_pool_row(
            pool_row(
                folder_key="company_ir",
                source_type="company_press_release",
                title="Apple announces iPhone SE",
                event_tags="AAPL|Product Launch",
                excerpt="PRESS RELEASE Apple announces iPhone SE.",
            )
        )

        self.assertLessEqual(int(qrel["relevance"]), 1)
        self.assertIn("penalty:wrong_source", qrel["notes"])

    def test_macro_query_matches_macro_release(self):
        qrel = grade_pool_row(
            pool_row(
                query_id="web_vix",
                query="VIX market volatility",
                intent="macro_volatility",
                expected_ticker="MARKET",
                source_scope="macro",
                folder_key="",
                source_type="official_macro_release",
                title="Official US macro release: CBOE VIX Index on 2022-12-12",
                matched_tickers="MARKET",
                event_tags="Official Macro|Market Volatility|Risk Appetite",
                excerpt="CBOE VIX Index market volatility.",
            )
        )

        self.assertEqual(qrel["relevance"], "3")

    def test_build_qrels_deduplicates_query_doc_pairs(self):
        rows = [pool_row(doc_id="doc_1"), pool_row(doc_id="doc_1"), pool_row(doc_id="doc_2")]

        qrels = build_qrels(rows)

        self.assertEqual([row["doc_id"] for row in qrels], ["doc_1", "doc_2"])


if __name__ == "__main__":
    unittest.main()
