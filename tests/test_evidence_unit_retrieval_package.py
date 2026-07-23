from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from features.build_evidence_unit_retrieval_package import build_evidence_unit_retrieval_package
from finportfolio_ir.io_utils import read_jsonl, write_jsonl


class EvidenceUnitRetrievalPackageTests(unittest.TestCase):
    def test_package_builds_retrieval_outputs_with_unit_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_path = tmp_path / "documents.jsonl"
            output_dir = tmp_path / "package"
            write_jsonl(
                input_path,
                [
                    {
                        "doc_id": "sec_aapl_10q__part2_item_1a_risk_factors",
                        "parent_doc_id": "sec_aapl_10q",
                        "title": "Apple 10-Q - Item 1A Risk Factors",
                        "body": "Apple risk factors include supply chain disruption and consumer demand uncertainty.",
                        "source": "sec.gov",
                        "source_type": "sec_filing_section",
                        "source_registry_id": "sec_edgar",
                        "url": "https://www.sec.gov/Archives/test.htm",
                        "published_at": "2022-03-15T12:00:00Z",
                        "available_at": "2022-03-15T12:00:00Z",
                        "tickers_detected": ["AAPL"],
                        "sec_form": "10-Q",
                        "sec_section_id": "part2_item_1a_risk_factors",
                        "sec_section_title": "Risk Factors",
                        "sec_section_ordinal": 2,
                    },
                    {
                        "doc_id": "company_aapl_release",
                        "title": "Apple announces services and product update",
                        "body": (
                            "Apple announced a product launch with improved performance. "
                            "Revenue grew 8 percent year over year and gross margin expanded. "
                            "Management expects services demand to remain resilient next quarter. "
                            "The company also noted supply chain risk and consumer demand uncertainty."
                        ),
                        "source": "apple.com",
                        "source_type": "company_press_release",
                        "source_registry_id": "company_ir",
                        "url": "https://www.apple.com/newsroom/test",
                        "published_at": "2022-03-15T12:00:00Z",
                        "available_at": "2022-03-15T12:00:00Z",
                        "tickers_detected": ["AAPL"],
                    },
                ],
            )

            manifest = build_evidence_unit_retrieval_package(
                inputs=[str(input_path)],
                output_dir=output_dir,
                portfolio_path=ROOT / "configs" / "sample_portfolio.yaml",
                metadata_path=ROOT / "data" / "processed_documents" / "ticker_metadata.csv",
                config_path=ROOT / "configs" / "default.yaml",
                decision_datetime="2022-03-16T09:30:00-05:00",
                top_k=3,
                method="full_hybrid",
                company_max_chars=120,
                company_min_chars=40,
                include_unknown_documents=False,
            )

            self.assertEqual(manifest["status"], "completed")
            self.assertGreaterEqual(manifest["evidence_unit_count"], 2)
            self.assertGreater(manifest["retrieved_context_count"], 0)
            self.assertTrue((output_dir / "evidence_units.jsonl").exists())
            self.assertTrue((output_dir / "retrieved_contexts.jsonl").exists())
            self.assertTrue((output_dir / "evidence_bundles.jsonl").exists())
            self.assertTrue((output_dir / "run.csv").exists())

            contexts = read_jsonl(output_dir / "retrieved_contexts.jsonl")
            self.assertTrue(any(row.get("evidence_unit_type") for row in contexts))
            self.assertTrue(any(row.get("parent_doc_id") for row in contexts))

            bundles = read_jsonl(output_dir / "evidence_bundles.jsonl")
            portfolio_evidence = bundles[0]["portfolio_evidence"]
            self.assertTrue(any(row.get("evidence_unit_type") for row in portfolio_evidence))
            self.assertTrue(any(row.get("parent_doc_id") for row in portfolio_evidence))


if __name__ == "__main__":
    unittest.main()
