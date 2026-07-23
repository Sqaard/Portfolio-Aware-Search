import tempfile
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evaluation.evaluate_ir_metrics import evaluate, load_qrels, load_run
from evaluation.evaluate_ir_metrics import summarize_by_method
from evaluation.evaluate_evidence_unit_promotion_gate import build_acceptance as build_evidence_unit_acceptance
from evaluation.evaluate_retrieval_diagnostics import evaluate_query_diagnostics
from evaluation.assistant_label_comparison_pool import label_pool_row
from evaluation.assistant_review_evidence_unit_queue import label_rows as assistant_review_evidence_unit_rows
from evaluation.apply_evidence_unit_human_spotcheck import apply_spotcheck_labels as apply_evidence_unit_spotcheck_labels
from evaluation.build_annotation_pool import build_pool_records
from evaluation.build_evidence_unit_review_queue import build_review_queue as build_evidence_unit_review_queue
from evaluation.build_evidence_unit_human_spotcheck import build_spotcheck_rows as build_evidence_unit_spotcheck_rows
from evaluation.calibrate_evidence_unit_reranker import calibrate_evidence_unit_records, evidence_unit_calibration_features
from evaluation.compare_document_vs_evidence_units import compare_document_vs_evidence_units
from evaluation.export_qrels_from_evidence_unit_review_queue import export_qrels_rows as export_evidence_unit_review_qrels_rows
from evaluation.export_qrels_from_pool import export_qrels_rows
from evaluation.fill_evidence_unit_spotcheck_labels import apply_label_assignments, parse_label_assignments
from evaluation.run_ablation_suite import load_query_requests
from evaluation.validate_qrels import validate_qrels_file
from finportfolio_ir.io_utils import read_jsonl, write_jsonl


class EvaluationTests(unittest.TestCase):
    def test_assistant_comparison_labeler_grades_by_query_kind_and_freshness(self):
        apple_fresh_earnings = {
            "query_id": "sample_portfolio_001_2022-03-15",
            "doc_id": "sec_aapl_8k_000032019321000104__exhibit_99_1",
            "title": "Apple Inc. 8-K filing filed 2021-10-28 - Exhibit 99.1 Earnings Release / Investor Material",
            "body_excerpt": "Apple Reports Fourth Quarter Results revenue up 29 percent.",
        }
        apple_stale_business = {
            "query_id": "sample_portfolio_tech_rates_2022-03-15",
            "doc_id": "sec_aapl_10k_000119312514383437__item_1_business",
            "title": "Apple Inc. 10-K filing filed 2014-10-27 - Item 1 Business",
            "body_excerpt": "Company background and products.",
        }
        jpm_fresh_risk = {
            "query_id": "sample_portfolio_banks_macro_2022-03-15",
            "doc_id": "sec_jpm_10k_000001961722000272__item_1a_risk_factors",
            "title": "JPMORGAN CHASE & CO 10-K filing filed 2022-02-22 - Item 1A Risk Factors",
            "body_excerpt": "Item 1A Risk Factors credit and liquidity risk.",
        }

        self.assertEqual(label_pool_row(apple_fresh_earnings)[0], 3)
        self.assertLessEqual(label_pool_row(apple_stale_business)[0], 1)
        self.assertEqual(label_pool_row(jpm_fresh_risk)[0], 3)

    def test_assistant_comparison_labeler_uses_decision_date_for_freshness(self):
        apple_2019_earnings_for_2020_decision = {
            "query_id": "heldout_tech_rates_2020-11-03",
            "decision_time": "2020-11-03T14:30:00Z",
            "doc_id": "sec_aapl_8k_000032019319000002__exhibit_99_112",
            "title": "Apple Inc. 8-K filing filed 2019-01-02 - Exhibit 99.112 Earnings Release / Investor Material",
            "body_excerpt": "Apple revising guidance revenue approximately $84 billion.",
        }

        relevance, notes = label_pool_row(apple_2019_earnings_for_2020_decision)

        self.assertEqual(relevance, 3)
        self.assertIn("freshness:fresh", notes)

    def test_evidence_unit_calibration_promotes_fresh_risk_over_old_business(self):
        old_business = {
            "query_id": "q1",
            "doc_id": "sec_jpm_10k_2016__item_1_business",
            "title": "JPMORGAN CHASE & CO 10-K filing filed 2016-02-23 - Item 1 Business",
            "published_at": "2016-02-23T12:00:00Z",
            "evidence_unit_type": "sec_section",
            "evidence_unit_claim_type": "filing_section",
            "final_score": 0.72,
        }
        fresh_risk = {
            "query_id": "q1",
            "doc_id": "sec_jpm_10k_2022__item_1a_risk_factors",
            "title": "JPMORGAN CHASE & CO 10-K filing filed 2022-02-22 - Item 1A Risk Factors",
            "published_at": "2022-02-22T12:00:00Z",
            "evidence_unit_type": "sec_section",
            "evidence_unit_claim_type": "risk_factors",
            "final_score": 0.68,
        }

        features = evidence_unit_calibration_features(fresh_risk)
        calibrated = calibrate_evidence_unit_records([old_business, fresh_risk], top_k=2)

        self.assertEqual(features["fresh_2021_plus"], 1.0)
        self.assertEqual(features["risk_factor"], 1.0)
        self.assertEqual(calibrated[0]["doc_id"], "sec_jpm_10k_2022__item_1a_risk_factors")
        self.assertGreater(calibrated[0]["calibration_score_delta"], 0)

    def test_evidence_unit_review_queue_prioritizes_calibrated_promotions(self):
        document_rows = [
            {
                "query_id": "q1",
                "doc_id": "risk_doc",
                "rank": 3,
                "title": "Apple 10-K Item 1A Risk Factors",
                "body_excerpt": "Risk factor evidence.",
            }
        ]
        raw_rows = [
            {
                "query_id": "q1",
                "doc_id": "risk_doc",
                "rank": 3,
                "evidence_unit_id": "risk_doc",
                "evidence_unit_claim_type": "risk_factors",
                "title": "Apple 10-K Item 1A Risk Factors",
                "body_excerpt": "Risk factor evidence.",
            }
        ]
        calibrated_rows = [
            {
                "query_id": "q1",
                "doc_id": "risk_doc",
                "rank": 1,
                "evidence_unit_id": "risk_doc",
                "evidence_unit_claim_type": "risk_factors",
                "title": "Apple 10-K Item 1A Risk Factors",
                "body_excerpt": "Risk factor evidence.",
                "calibration_score_delta": 0.15,
                "evidence_calibration_tags": ["risk_factor"],
            }
        ]
        qrels_rows = [
            {
                "query_id": "q1",
                "doc_id": "risk_doc",
                "relevance": "3",
                "label_source": "assistant_document_vs_evidence_v1",
            }
        ]

        rows = build_evidence_unit_review_queue(
            document_rows=document_rows,
            raw_evidence_rows=raw_rows,
            calibrated_rows=calibrated_rows,
            qrels_rows=qrels_rows,
            top_k=10,
            limit=10,
        )

        self.assertEqual(rows[0]["doc_id"], "risk_doc")
        self.assertIn("promoted_by_calibration", rows[0]["reason"])
        self.assertIn("assistant_label", rows[0]["reason"])
        self.assertEqual(rows[0]["raw_to_calibrated_delta"], "2")

    def test_assistant_review_evidence_unit_queue_keeps_provenance(self):
        rows = assistant_review_evidence_unit_rows(
            [
                {
                    "review_id": "eu_review_0001",
                    "query_id": "heldout_banks_macro_2020-11-03",
                    "doc_id": "sec_jpm_10k_2020__item_1a_risk_factors",
                    "title": "JPMORGAN CHASE & CO 10-K filing filed 2020-02-25 - Item 1A Risk Factors",
                    "evidence_unit_claim_type": "risk_factors",
                    "published_at": "2020-02-25T16:00:00Z",
                    "decision_time": "2020-11-03T14:30:00Z",
                    "existing_relevance": "2",
                    "reason": "calibrated_top10",
                }
            ],
            overwrite=True,
        )

        self.assertEqual(rows[0]["suggested_human_relevance"], "3")
        self.assertIn("assistant_evidence_unit_review_v1", rows[0]["human_notes"])
        self.assertIn("previous_assistant_label:2", rows[0]["human_notes"])

    def test_export_evidence_unit_review_qrels_uses_suggested_labels(self):
        qrels, issues = export_evidence_unit_review_qrels_rows(
            [
                {
                    "review_id": "eu_review_0001",
                    "query_id": "q1",
                    "doc_id": "d1",
                    "suggested_human_relevance": "2",
                    "reason": "calibrated_top10",
                    "existing_relevance": "1",
                    "label_source": "assistant_old",
                    "human_notes": "assistant review note",
                }
            ],
            label_source="assistant_evidence_unit_review_v1",
            annotator="codex_assistant",
        )

        self.assertEqual(issues, [])
        self.assertEqual(qrels[0]["relevance"], "2")
        self.assertEqual(qrels[0]["label_source"], "assistant_evidence_unit_review_v1")
        self.assertIn("previous_relevance:1", qrels[0]["notes"])

    def test_evidence_unit_spotcheck_prioritizes_promoted_borderline_rows(self):
        rows = [
            {
                "query_id": "q1",
                "doc_id": "d_promoted",
                "evidence_unit_id": "d_promoted",
                "priority": "120",
                "reason": "borderline_label|calibrated_top10|promoted_by_calibration",
                "document_rank": "9",
                "raw_evidence_rank": "10",
                "calibrated_rank": "2",
                "raw_to_calibrated_delta": "8",
                "document_to_calibrated_delta": "7",
                "existing_relevance": "1",
                "suggested_human_relevance": "2",
                "evidence_unit_claim_type": "risk_factors",
                "title": "Apple 10-K Item 1A Risk Factors",
                "body_excerpt": "Risk factor evidence.",
            },
            {
                "query_id": "q1",
                "doc_id": "d_plain",
                "evidence_unit_id": "d_plain",
                "priority": "40",
                "reason": "coverage",
                "document_rank": "4",
                "raw_evidence_rank": "4",
                "calibrated_rank": "4",
                "existing_relevance": "3",
                "suggested_human_relevance": "3",
                "title": "Plain relevant row",
            },
            {
                "query_id": "q2",
                "doc_id": "d_coverage",
                "evidence_unit_id": "d_coverage",
                "priority": "30",
                "reason": "coverage",
                "document_rank": "1",
                "raw_evidence_rank": "1",
                "calibrated_rank": "1",
                "existing_relevance": "3",
                "suggested_human_relevance": "3",
                "title": "Coverage row",
            },
        ]

        spotcheck = build_evidence_unit_spotcheck_rows(rows, limit=2, max_per_query=1)

        self.assertEqual([row["doc_id"] for row in spotcheck], ["d_promoted", "d_coverage"])
        self.assertEqual(spotcheck[0]["spotcheck_id"], "eu_spot_0001")
        self.assertEqual(spotcheck[0]["human_relevance"], "")
        self.assertIn("assistant_changed_label", spotcheck[0]["reason"])
        self.assertIn("promoted_by_calibration", spotcheck[0]["reason"])

    def test_apply_evidence_unit_spotcheck_overrides_only_human_rows(self):
        review_rows = [
            {
                "review_id": "eu_review_0001",
                "query_id": "q1",
                "doc_id": "d1",
                "suggested_human_relevance": "1",
                "existing_relevance": "2",
                "reason": "borderline_label",
            },
            {
                "review_id": "eu_review_0002",
                "query_id": "q1",
                "doc_id": "d2",
                "suggested_human_relevance": "3",
                "existing_relevance": "3",
                "reason": "calibrated_top10",
            },
        ]
        spotcheck_rows = [
            {
                "spotcheck_id": "eu_spot_0001",
                "query_id": "q1",
                "doc_id": "d1",
                "human_relevance": "3",
                "human_notes": "human says this is the right evidence",
            }
        ]

        merged, qrels, issues = apply_evidence_unit_spotcheck_labels(review_rows, spotcheck_rows)

        self.assertEqual(issues, [])
        self.assertEqual([row["relevance"] for row in qrels], ["3", "3"])
        self.assertEqual(qrels[0]["label_source"], "human_evidence_unit_spotcheck_v1")
        self.assertEqual(qrels[0]["annotator"], "user_chat")
        self.assertEqual(qrels[1]["label_source"], "assistant_evidence_unit_review_v1")
        self.assertEqual(merged[0]["final_label_source"], "human_evidence_unit_spotcheck_v1")
        self.assertIn("source:assistant_fallback", merged[1]["final_notes"])

    def test_fill_evidence_unit_spotcheck_labels_accepts_row_numbers_and_ids(self):
        assignments, parse_issues = parse_label_assignments("1=3, eu_spot_0002=0, bad")
        rows, apply_issues = apply_label_assignments(
            [
                {"spotcheck_id": "eu_spot_0001", "human_relevance": "", "human_notes": ""},
                {"spotcheck_id": "eu_spot_0002", "human_relevance": "", "human_notes": ""},
            ],
            assignments,
            reviewer_notes="chat review",
        )

        self.assertEqual(assignments, {"1": "3", "eu_spot_0002": "0"})
        self.assertEqual(parse_issues[0]["issue_type"], "parse_error")
        self.assertEqual(apply_issues, [])
        self.assertEqual(rows[0]["human_relevance"], "3")
        self.assertEqual(rows[1]["human_relevance"], "0")
        self.assertIn("chat review", rows[0]["human_notes"])

    def test_evidence_unit_promotion_gate_blocks_without_human_labels(self):
        acceptance = build_evidence_unit_acceptance(
            summary_rows=[
                {"method": "document__full_hybrid", "precision_at_10": 1.0, "ndcg_at_10": 0.84, "mrr": 1.0},
                {
                    "method": "evidence_unit_calibrated_source_document__full_hybrid",
                    "precision_at_10": 1.0,
                    "ndcg_at_10": 1.0,
                    "mrr": 1.0,
                },
            ],
            coverage_summary_rows=[
                {
                    "method": "evidence_unit_calibrated_source_document__full_hybrid",
                    "mean_judged_rate_at_10": 1.0,
                }
            ],
            qrels_rows=[{"query_id": "q1", "doc_id": "d1", "relevance": "3", "label_source": "assistant_evidence_unit_review_v1"}],
            min_human_labels=1,
            min_ndcg_delta=0.02,
        )

        self.assertEqual(acceptance[0]["decision"], "block_promotion")
        self.assertIn("pending_human_spotcheck_labels", acceptance[0]["reason"])
        self.assertGreater(acceptance[0]["delta_ndcg_at_10"], 0)

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

    def test_compare_document_vs_evidence_units_evaluates_collapsed_unit_run(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            documents_path = tmp_path / "documents.jsonl"
            qrels_path = tmp_path / "qrels.csv"
            output_dir = tmp_path / "comparison"
            write_jsonl(
                documents_path,
                [
                    {
                        "doc_id": "company_aapl_release",
                        "title": "Apple revenue and guidance update",
                        "body": (
                            "Apple revenue grew 8 percent year over year. "
                            "Management expects services demand to remain resilient. "
                            "Gross margin expanded, but supply chain risk remains."
                        ),
                        "source": "apple.com",
                        "source_type": "company_press_release",
                        "source_registry_id": "company_ir",
                        "url": "https://www.apple.com/newsroom/test",
                        "published_at": "2022-03-15T12:00:00Z",
                        "available_at": "2022-03-15T12:00:00Z",
                        "tickers_detected": ["AAPL"],
                    },
                    {
                        "doc_id": "company_msft_release",
                        "title": "Microsoft cloud update",
                        "body": "Microsoft discussed cloud demand and enterprise software spending.",
                        "source": "microsoft.com",
                        "source_type": "company_press_release",
                        "source_registry_id": "company_ir",
                        "url": "https://www.microsoft.com/test",
                        "published_at": "2022-03-15T12:00:00Z",
                        "available_at": "2022-03-15T12:00:00Z",
                        "tickers_detected": ["MSFT"],
                    },
                ],
            )
            qrels_path.write_text(
                "\n".join(
                    [
                        "query_id,doc_id,relevance",
                        "q1,company_aapl_release,3",
                        "q1,company_msft_release,0",
                    ]
                ),
                encoding="utf-8",
            )

            manifest = compare_document_vs_evidence_units(
                documents=[str(documents_path)],
                output_dir=output_dir,
                metadata_path=ROOT / "data" / "processed_documents" / "ticker_metadata.csv",
                config_path=ROOT / "configs" / "default.yaml",
                top_k=2,
                methods=["bm25_only"],
                evidence_eval_grains=["source_document"],
                portfolio_path=str(ROOT / "configs" / "sample_portfolio.yaml"),
                decision_datetime="2022-03-16T09:30:00-05:00",
                query_id="q1",
                qrels_path=str(qrels_path),
                company_max_chars=80,
                company_min_chars=30,
            )

            self.assertEqual(manifest["status"], "completed")
            self.assertGreaterEqual(manifest["evidence_unit_count"], 2)
            self.assertEqual(manifest["min_mean_judged_rate_at_10"], 1.0)
            self.assertEqual(manifest["coverage_warning"], "")
            self.assertGreater(manifest["annotation_pool_count"], 0)
            delta_rows = read_jsonl(output_dir / "evidence_unit_eval_retrieved_all.jsonl")
            self.assertTrue(any(row["doc_id"] == "company_aapl_release" for row in delta_rows))
            metrics_summary = (output_dir / "comparison_metrics_by_method.csv").read_text(encoding="utf-8")
            self.assertIn("document__bm25_only", metrics_summary)
            self.assertIn("evidence_unit_source_document__bm25_only", metrics_summary)
            comparison_delta = (output_dir / "comparison_delta_by_method.csv").read_text(encoding="utf-8")
            self.assertIn("source_document", comparison_delta)
            coverage_summary = (output_dir / "qrels_coverage_summary.csv").read_text(encoding="utf-8")
            self.assertIn("mean_judged_rate_at_10", coverage_summary)
            annotation_pool = (output_dir / "comparison_annotation_pool.csv").read_text(encoding="utf-8")
            self.assertIn("company_aapl_release", annotation_pool)


if __name__ == "__main__":
    unittest.main()
