from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from finportfolio_ir.io_utils import write_jsonl
from finportfolio_ir.query_intent import classify_query_intent
from retrieval.evidence_unit_gate import evidence_unit_gate_for_intent, evidence_unit_gate_for_query
from retrieval.evidence_unit_promotion import EvidenceUnitPromotionStatus, load_evidence_unit_promotion_status
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
        self.assertIn("evidence_unit_gate", payload)

    def test_evidence_unit_gate_allows_specific_risk_factor_queries(self):
        decision = evidence_unit_gate_for_query("Apple risk factors in the latest 10-K")

        self.assertTrue(decision.enabled)
        self.assertEqual(decision.mode, "evidence_unit_calibrated_source_type_v1")
        self.assertIn("sec_section:risk_factors", decision.preferred_unit_types)
        self.assertEqual(decision.promotion_status, "accepted")
        self.assertEqual(decision.min_label_status, "mixed_qrels_promotion_gate_accepted")

    def test_evidence_unit_promotion_status_reads_acceptance_artifact(self):
        status = load_evidence_unit_promotion_status()

        self.assertTrue(status.accepted)
        self.assertEqual(status.decision, "accept_guarded_promotion")
        self.assertGreaterEqual(status.human_label_count, 10)
        self.assertGreater(status.delta_ndcg_at_10, 0)

    def test_evidence_unit_gate_blocks_specific_query_without_accepted_promotion(self):
        blocked = EvidenceUnitPromotionStatus(
            accepted=False,
            decision="block_promotion",
            reason="pending_human_spotcheck_labels",
            human_label_count=0,
            min_human_labels=10,
            delta_ndcg_at_10=0.15,
            delta_precision_at_10=0.0,
            calibrated_judged_rate_at_10=1.0,
            artifact_path="missing.csv",
        )
        intent = classify_query_intent("Apple risk factors in the latest 10-K")
        decision = evidence_unit_gate_for_intent(intent, promotion_status=blocked)

        self.assertFalse(decision.enabled)
        self.assertEqual(decision.mode, "document")
        self.assertIn("promotion_gate_not_accepted", decision.reason_tags)
        self.assertEqual(decision.promotion_status, "block_promotion")

    def test_evidence_unit_gate_blocks_broad_company_overview(self):
        decision = evidence_unit_gate_for_query("Apple company overview and product history")

        self.assertFalse(decision.enabled)
        self.assertEqual(decision.mode, "document")

    def test_search_payload_uses_evidence_units_for_guarded_risk_query(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            docs_path = Path(tmpdir) / "documents.jsonl"
            write_jsonl(
                docs_path,
                [
                    {
                        "doc_id": "sec_aapl_10k_2020__item_1_business",
                        "parent_doc_id": "sec_aapl_10k_2020",
                        "title": "Apple Inc. 10-K filing filed 2020-10-30 - Item 1 Business",
                        "body": "Apple designs smartphones and services.",
                        "source": "SEC EDGAR",
                        "source_type": "sec_filing_section",
                        "sec_section_id": "item_1_business",
                        "published_at": "2020-10-30T16:00:00Z",
                        "available_at": "2020-10-30T16:00:00Z",
                        "matched_tickers": ["AAPL"],
                        "event_tags": ["filing", "10-k"],
                        "source_credibility": 0.95,
                    },
                    {
                        "doc_id": "sec_aapl_10k_2020__item_1a_risk_factors",
                        "parent_doc_id": "sec_aapl_10k_2020",
                        "title": "Apple Inc. 10-K filing filed 2020-10-30 - Item 1A Risk Factors",
                        "body": "Item 1A Risk Factors. Apple faces supply chain, legal, regulatory, and market risks.",
                        "source": "SEC EDGAR",
                        "source_type": "sec_filing_section",
                        "sec_section_id": "item_1a_risk_factors",
                        "published_at": "2020-10-30T16:00:00Z",
                        "available_at": "2020-10-30T16:00:00Z",
                        "matched_tickers": ["AAPL"],
                        "event_tags": ["filing", "10-k", "risk_factors"],
                        "source_credibility": 0.95,
                    },
                ],
            )
            service = FinPortfolioWebService(documents_path=docs_path)

            payload = service.search_payload("Apple risk factors in the latest 10-K")

        self.assertTrue(payload["evidence_unit_gate"]["enabled"])
        self.assertTrue(payload["evidence_unit_gate"]["active"])
        self.assertEqual(payload["results"][0]["search_grain"], "evidence_unit")
        self.assertEqual(payload["results"][0]["evidence_unit_claim_type"], "risk_factors")

    def test_search_payload_keeps_document_grain_for_broad_query(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            docs_path = Path(tmpdir) / "documents.jsonl"
            write_jsonl(
                docs_path,
                [
                    {
                        "doc_id": "company_aapl_overview",
                        "title": "Apple company overview and product history",
                        "body": "Apple designs iPhone, Mac, iPad, wearables, and services.",
                        "source": "apple.com",
                        "source_type": "company_press_release",
                        "published_at": "2020-10-30T16:00:00Z",
                        "available_at": "2020-10-30T16:00:00Z",
                        "matched_tickers": ["AAPL"],
                        "event_tags": ["company_overview"],
                        "source_credibility": 0.8,
                    }
                ],
            )
            service = FinPortfolioWebService(documents_path=docs_path)

            payload = service.search_payload("Apple company overview and product history")

        self.assertFalse(payload["evidence_unit_gate"]["enabled"])
        self.assertFalse(payload["evidence_unit_gate"]["active"])
        self.assertEqual(payload["results"][0]["search_grain"], "document")


if __name__ == "__main__":
    unittest.main()
