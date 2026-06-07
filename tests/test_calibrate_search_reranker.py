import unittest

from evaluation.calibrate_search_reranker import (
    build_items,
    field_match_features,
    fit_weights,
    query_metrics,
    section_intent_features,
)


class SearchRerankerCalibrationTests(unittest.TestCase):
    def test_field_match_marks_earnings_release_over_product_press(self):
        earnings = {
            "title": "Apple Inc. 8-K filing filed 2019-01-02 - Exhibit 99.1 Earnings Release",
            "source_type": "sec_filing_exhibit",
            "event_tags": "AAPL|Earnings Guidance",
            "excerpt": "",
            "matched_tickers": "AAPL",
        }
        product = {
            "title": "Apple announces the new iPhone SE",
            "source_type": "company_press_release",
            "event_tags": "AAPL|Company Official",
            "excerpt": "",
            "matched_tickers": "AAPL",
        }

        earnings_exact, earnings_bad = field_match_features("Apple earnings guidance", earnings)
        product_exact, product_bad = field_match_features("Apple earnings guidance", product)

        self.assertGreater(earnings_exact, product_exact)
        self.assertGreater(product_bad, earnings_bad)

    def test_build_items_penalizes_wrong_company_for_entity_query(self):
        queries = {
            "q1": {
                "query_id": "q1",
                "query": "JPMorgan credit risk",
                "expected_ticker": "JPM",
                "source_scope": "sec_filings",
            }
        }
        pool = [
            {
                "query_id": "q1",
                "doc_id": "jpm",
                "rank": "1",
                "score": "10",
                "signal_strength": "2",
                "matched_tickers": "JPM",
                "source_type": "sec_filing_section",
                "folder_key": "sec_filings",
                "title": "JPMORGAN CHASE 10-K Item 1A Risk Factors credit risk",
                "event_tags": "JPM|Company Risk|Credit",
                "risk_terms": "",
                "excerpt": "",
            },
            {
                "query_id": "q1",
                "doc_id": "crm",
                "rank": "2",
                "score": "10",
                "signal_strength": "2",
                "matched_tickers": "CRM",
                "source_type": "sec_filing_section",
                "folder_key": "sec_filings",
                "title": "Salesforce 10-K Item 1A Risk Factors",
                "event_tags": "CRM|Company Risk",
                "risk_terms": "",
                "excerpt": "",
            },
        ]

        items = build_items(pool, queries, {"q1": {"jpm": 3, "crm": 0}})
        by_doc = {item["doc_id"]: item for item in items}

        self.assertEqual(by_doc["jpm"]["features"]["expected_ticker_match"], 1.0)
        self.assertEqual(by_doc["crm"]["features"]["wrong_company"], 1.0)

    def test_section_intent_features_distinguish_sec_wrappers(self):
        earnings_exhibit = {
            "title": "Apple Inc. 8-K filing filed 2019-10-30 - Exhibit 99.1 Earnings Release",
            "source_type": "sec_filing_exhibit",
            "event_tags": "AAPL|Earnings Guidance",
            "excerpt": "",
            "matched_tickers": "AAPL",
        }
        wrapper = {
            "title": "Apple Inc. 8-K filing filed 2019-10-30 - Item 9.01 Financial Statements and Exhibits",
            "source_type": "sec_filing_section",
            "event_tags": "AAPL|8-K|Financial Statements",
            "excerpt": "Exhibit 99.1 Press release.",
            "matched_tickers": "AAPL",
        }
        risk = {
            "title": "Apple Inc. 10-K filing filed 2022-10-28 - Item 1A Risk Factors",
            "source_type": "sec_filing_section",
            "event_tags": "AAPL|Company Risk",
            "excerpt": "",
            "matched_tickers": "AAPL",
        }

        earnings_features = section_intent_features("Apple earnings guidance", earnings_exhibit)
        wrapper_features = section_intent_features("Apple earnings guidance", wrapper)
        risk_features = section_intent_features("Apple risk factors", risk)

        self.assertEqual(earnings_features["earnings_release_match"], 1.0)
        self.assertEqual(wrapper_features["item_901_wrapper"], 1.0)
        self.assertEqual(risk_features["risk_factor_section_match"], 1.0)

    def test_fit_weights_can_learn_simple_rank_preference(self):
        queries = {
            "q1": {"query_id": "q1", "query": "Apple earnings guidance", "expected_ticker": "AAPL", "source_scope": "sec_filings"},
            "q2": {"query_id": "q2", "query": "JPMorgan credit risk", "expected_ticker": "JPM", "source_scope": "sec_filings"},
        }
        pool = [
            {
                "query_id": "q1",
                "doc_id": "a_good",
                "rank": "2",
                "score": "8",
                "signal_strength": "1",
                "matched_tickers": "AAPL",
                "source_type": "sec_filing_exhibit",
                "folder_key": "sec_filings",
                "title": "Apple 8-K Exhibit 99.1 Earnings Release",
                "event_tags": "AAPL|Earnings Guidance",
                "risk_terms": "",
                "excerpt": "",
            },
            {
                "query_id": "q1",
                "doc_id": "a_bad",
                "rank": "1",
                "score": "8",
                "signal_strength": "1",
                "matched_tickers": "AAPL",
                "source_type": "company_press_release",
                "folder_key": "company_ir",
                "title": "Apple announces iPhone launch",
                "event_tags": "AAPL|Company Official",
                "risk_terms": "",
                "excerpt": "",
            },
            {
                "query_id": "q2",
                "doc_id": "j_good",
                "rank": "2",
                "score": "8",
                "signal_strength": "1",
                "matched_tickers": "JPM",
                "source_type": "sec_filing_section",
                "folder_key": "sec_filings",
                "title": "JPM 10-K Item 1A Risk Factors credit risk",
                "event_tags": "JPM|Company Risk|Credit",
                "risk_terms": "",
                "excerpt": "",
            },
            {
                "query_id": "q2",
                "doc_id": "j_bad",
                "rank": "1",
                "score": "8",
                "signal_strength": "1",
                "matched_tickers": "CRM",
                "source_type": "sec_filing_section",
                "folder_key": "sec_filings",
                "title": "Salesforce risk factors",
                "event_tags": "CRM|Company Risk",
                "risk_terms": "",
                "excerpt": "",
            },
        ]
        qrels = {"q1": {"a_good": 3, "a_bad": 0}, "q2": {"j_good": 3, "j_bad": 0}}
        items = build_items(pool, queries, qrels)
        by_query = {"q1": [item for item in items if item["query_id"] == "q1"], "q2": [item for item in items if item["query_id"] == "q2"]}

        weights = fit_weights(by_query, qrels, ["q1", "q2"], max_passes=3)
        rows = query_metrics(by_query, qrels, weights, ["q1", "q2"])

        self.assertEqual([row["mrr"] for row in rows], [1.0, 1.0])


if __name__ == "__main__":
    unittest.main()
