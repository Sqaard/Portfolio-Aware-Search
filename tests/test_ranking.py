from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from finportfolio_ir.schema import FinancialDocument
from retrieval.hybrid_ranker import DiversificationConfig, RankerConfig, RankingWeights, rank_documents
from retrieval.portfolio_query_builder import PortfolioQuery
from retrieval.retrieve_for_portfolio import retrieval_records


class RankingTests(unittest.TestCase):
    def test_high_weight_holding_gets_higher_exposure_score(self):
        documents = [
            FinancialDocument.from_dict(
                {
                    "doc_id": "aapl_doc",
                    "title": "Apple update",
                    "body": "Apple supply chain update.",
                    "source": "test",
                    "url": "",
                    "published_at": "2022-03-15T12:00:00Z",
                    "available_at": "2022-03-15T12:00:00Z",
                    "tickers_detected": ["AAPL"],
                }
            ),
            FinancialDocument.from_dict(
                {
                    "doc_id": "unh_doc",
                    "title": "UnitedHealth update",
                    "body": "UnitedHealth healthcare update.",
                    "source": "test",
                    "url": "",
                    "published_at": "2022-03-15T12:00:00Z",
                    "available_at": "2022-03-15T12:00:00Z",
                    "tickers_detected": ["UNH"],
                }
            ),
        ]
        query = PortfolioQuery(
            portfolio_id="p",
            tickers=["AAPL", "UNH"],
            weighted_entities={"AAPL": 0.12, "UNH": 0.06},
            expanded_terms={},
            query_text="Apple UnitedHealth",
        )
        ranked = rank_documents(
            documents=documents,
            query=query,
            decision_datetime=datetime(2022, 3, 15, 14, 30, tzinfo=timezone.utc),
            sparse_scores={"aapl_doc": 1.0, "unh_doc": 1.0},
            config=RankerConfig(weights=RankingWeights(sparse=0.0, entity=0.0, portfolio_exposure=1.0, recency=0.0, event_importance=0.0)),
            top_k=2,
        )

        self.assertEqual(ranked[0]["doc_id"], "aapl_doc")
        self.assertGreater(ranked[0]["portfolio_exposure_score"], ranked[1]["portfolio_exposure_score"])

    def test_retrieval_records_have_required_export_fields(self):
        records = retrieval_records(
            documents_path=ROOT / "data" / "processed_documents" / "documents.jsonl",
            portfolio_path=ROOT / "configs" / "sample_portfolio.yaml",
            metadata_path=ROOT / "data" / "processed_documents" / "ticker_metadata.csv",
            decision_datetime_text="2022-03-15T09:30:00-05:00",
            config_path=ROOT / "configs" / "default.yaml",
            top_k=3,
            method="bm25_only",
        )
        required = {
            "portfolio_id",
            "decision_id",
            "method",
            "portfolio_snapshot_id",
            "decision_time",
            "retrieval_cutoff",
            "retrieval_query_lex",
            "retrieval_query_sem",
            "evidence_bundle_id",
            "rank",
            "doc_id",
            "published_at",
            "first_seen_at",
            "available_at",
            "ingested_at",
            "duplicate_cluster_id",
            "body_excerpt",
            "matched_tickers",
            "matched_holdings",
            "evidence_scope",
            "source_credibility",
            "sparse_score",
            "entity_score",
            "recency_score",
            "event_importance_score",
            "source_credibility_score",
            "final_score",
            "retrieval_reason_tags",
            "diversification_applied",
            "ranking_stage",
            "document_hash",
        }

        self.assertEqual(len(records), 3)
        self.assertTrue(required.issubset(records[0]))

    def test_diversified_ranking_limits_duplicate_clusters(self):
        documents = []
        for index in range(3):
            documents.append(
                FinancialDocument.from_dict(
                    {
                        "doc_id": f"aapl_dup_{index}",
                        "title": f"Apple duplicate {index}",
                        "body": "Apple iPhone guidance update.",
                        "source": "test",
                        "url": "",
                        "published_at": "2022-03-15T12:00:00Z",
                        "available_at": "2022-03-15T12:00:00Z",
                        "duplicate_cluster_id": "cluster_aapl",
                        "tickers_detected": ["AAPL"],
                    }
                )
            )
        documents.append(
            FinancialDocument.from_dict(
                {
                    "doc_id": "market_doc",
                    "title": "Fed inflation context",
                    "body": "Federal Reserve inflation and interest rates context.",
                    "source": "test",
                    "url": "",
                    "published_at": "2022-03-15T12:00:00Z",
                    "available_at": "2022-03-15T12:00:00Z",
                    "duplicate_cluster_id": "cluster_market",
                    "tickers_detected": [],
                    "event_tags": ["macro"],
                }
            )
        )
        query = PortfolioQuery(
            portfolio_id="p",
            tickers=["AAPL"],
            weighted_entities={"AAPL": 0.12},
            expanded_terms={},
            query_text="Apple Fed inflation",
        )
        ranked = rank_documents(
            documents=documents,
            query=query,
            decision_datetime=datetime(2022, 3, 15, 14, 30, tzinfo=timezone.utc),
            sparse_scores={document.doc_id: 1.0 for document in documents},
            config=RankerConfig(
                weights=RankingWeights(sparse=1.0),
                diversification=DiversificationConfig(
                    enabled=True,
                    max_per_duplicate_cluster=1,
                    min_market_evidence=1,
                ),
            ),
            top_k=2,
        )

        duplicate_clusters = [item["document"].duplicate_cluster_id for item in ranked]
        self.assertEqual(duplicate_clusters.count("cluster_aapl"), 1)
        self.assertIn("market", {item["evidence_scope"] for item in ranked})


if __name__ == "__main__":
    unittest.main()
