import tempfile
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evaluation.evaluate_ir_metrics import evaluate, load_qrels, load_run
from evaluation.evaluate_ir_metrics import summarize_by_method
from evaluation.evaluate_retrieval_diagnostics import evaluate_query_diagnostics
from evaluation.build_annotation_pool import build_pool_records
from evaluation.export_qrels_from_pool import export_qrels_rows
from evaluation.run_ablation_suite import load_query_requests
from evaluation.validate_qrels import validate_qrels_file


class EvaluationTests(unittest.TestCase):
    def test_run_loader_keeps_methods_separate(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            run_path = Path(tmpdir) / "run.csv"
            run_path.write_text(
                "\n".join(
                    [
                        "query_id,doc_id,rank,score,method",
                        "q1,d1,1,0.9,bm25_only",
                        "q1,d2,1,0.8,full_hybrid",
                    ]
                ),
                encoding="utf-8",
            )
            qrels_path = Path(tmpdir) / "qrels.csv"
            qrels_path.write_text(
                "\n".join(
                    [
                        "query_id,doc_id,relevance",
                        "q1,d1,3",
                        "q1,d2,0",
                    ]
                ),
                encoding="utf-8",
            )

            metrics = evaluate(load_qrels(qrels_path), load_run(run_path))
            summary = summarize_by_method(metrics)

        by_method = {row["method"]: row for row in metrics}
        self.assertEqual(set(by_method), {"bm25_only", "full_hybrid"})
        self.assertEqual(by_method["bm25_only"]["mrr"], 1.0)
        self.assertEqual(by_method["full_hybrid"]["mrr"], 0.0)
        self.assertEqual(len(summary), 2)

    def test_query_request_loader_requires_batch_columns(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            query_path = Path(tmpdir) / "queries.csv"
            query_path.write_text(
                "\n".join(
                    [
                        "query_id,portfolio,decision_datetime",
                        "q1,configs/sample_portfolio.yaml,2022-03-15T09:30:00-05:00",
                    ]
                ),
                encoding="utf-8",
            )

            requests = load_query_requests(query_path)

        self.assertEqual(requests[0]["query_id"], "q1")
        self.assertEqual(requests[0]["portfolio"], "configs/sample_portfolio.yaml")

    def test_annotation_pool_deduplicates_docs_across_methods(self):
        rows = build_pool_records(
            [
                {
                    "query_id": "q1",
                    "doc_id": "d1",
                    "method": "bm25_only",
                    "rank": 2,
                    "final_score": 0.7,
                    "title": "Doc",
                    "matched_tickers": ["AAPL"],
                },
                {
                    "query_id": "q1",
                    "doc_id": "d1",
                    "method": "full_hybrid",
                    "rank": 1,
                    "final_score": 0.9,
                    "title": "Doc",
                    "matched_tickers": ["AAPL"],
                },
            ],
            qrels={"q1": {"d1": 2}},
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["best_rank"], 1)
        self.assertEqual(rows[0]["methods"], "bm25_only|full_hybrid")
        self.assertEqual(rows[0]["existing_relevance"], 2)
        self.assertEqual(rows[0]["review_priority"], "already_labeled")

    def test_qrels_validator_flags_invalid_labels(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            qrels_path = Path(tmpdir) / "qrels.csv"
            qrels_path.write_text(
                "\n".join(
                    [
                        "query_id,doc_id,relevance",
                        "q1,d1,2",
                        "q1,d1,1",
                        "q1,d2,5",
                    ]
                ),
                encoding="utf-8",
            )

            issues = validate_qrels_file(qrels_path)

        issue_types = {issue["issue_type"] for issue in issues}
        self.assertIn("duplicate_qrel", issue_types)
        self.assertIn("invalid_relevance", issue_types)

    def test_qrels_validator_can_limit_run_coverage_to_top_k(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            qrels_path = Path(tmpdir) / "qrels.csv"
            qrels_path.write_text(
                "\n".join(
                    [
                        "query_id,doc_id,relevance",
                        "q1,d1,3",
                    ]
                ),
                encoding="utf-8",
            )
            run_path = Path(tmpdir) / "run.csv"
            run_path.write_text(
                "\n".join(
                    [
                        "query_id,doc_id,rank,score,method",
                        "q1,d1,1,2.0,candidate",
                        "q1,d2,2,1.0,candidate",
                    ]
                ),
                encoding="utf-8",
            )

            top_one_issues = validate_qrels_file(qrels_path, run_path, top_k=1)
            full_issues = validate_qrels_file(qrels_path, run_path)

        self.assertEqual(top_one_issues, [])
        self.assertEqual(full_issues[0]["issue_type"], "unlabeled_run_doc")

    def test_export_qrels_from_pool_uses_reviewed_labels_first(self):
        qrels, issues = export_qrels_rows(
            [
                {
                    "query_id": "q1",
                    "doc_id": "d1",
                    "existing_relevance": "0",
                    "relevance": "3",
                    "label_source": "human_v1",
                    "annotator": "reviewer",
                    "notes": "critical",
                },
                {
                    "query_id": "q1",
                    "doc_id": "d2",
                    "existing_relevance": "1",
                    "relevance": "",
                    "label_source": "",
                    "annotator": "",
                    "notes": "",
                },
            ],
            fallback_existing=True,
            default_label_source="bootstrap",
        )

        self.assertEqual(len(issues), 0)
        self.assertEqual(qrels[0]["relevance"], "3")
        self.assertEqual(qrels[0]["label_source"], "human_v1")
        self.assertEqual(qrels[1]["relevance"], "1")
        self.assertEqual(qrels[1]["label_source"], "bootstrap")

    def test_retrieval_diagnostics_compute_causality_duplicates_and_coverage(self):
        diagnostics = evaluate_query_diagnostics(
            [
                {
                    "available_at": "2022-03-15T12:00:00Z",
                    "retrieval_cutoff": "2022-03-15T14:30:00Z",
                    "duplicate_cluster_id": "cluster_a",
                    "matched_holdings": ["AAPL"],
                    "portfolio_holdings": ["AAPL", "MSFT"],
                },
                {
                    "available_at": "2022-03-15T15:00:00Z",
                    "retrieval_cutoff": "2022-03-15T14:30:00Z",
                    "duplicate_cluster_id": "cluster_a",
                    "matched_holdings": ["MSFT"],
                    "portfolio_holdings": ["AAPL", "MSFT"],
                },
            ],
            k=2,
        )

        self.assertEqual(diagnostics["causal_validity_at_k"], 0.5)
        self.assertEqual(diagnostics["duplicate_rate_at_k"], 0.5)
        self.assertEqual(diagnostics["portfolio_coverage_at_k"], 1.0)


if __name__ == "__main__":
    unittest.main()
