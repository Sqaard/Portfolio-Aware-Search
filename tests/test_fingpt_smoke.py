from pathlib import Path
import json
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from finportfolio_ir.io_utils import write_jsonl
from features.run_fingpt_handoff_smoke import build_smoke_summary


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class FinGPTSmokeTests(unittest.TestCase):
    def test_smoke_summary_passes_when_outputs_cover_contexts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            contexts = root / "retrieved_contexts.jsonl"
            smoke = root / "fingpt_smoke"
            write_jsonl(
                contexts,
                [
                    {
                        "doc_id": "d1",
                        "matched_holdings": ["AAPL"],
                        "evidence_scope": "stock",
                        "retrieval_reason_tags": ["exact_ticker"],
                        "duplicate_cluster_id": "c1",
                    }
                ],
            )
            _write(smoke / "leakage_report.json", json.dumps({"finportfolio_ir_leakage_rows": 0}))
            _write(
                smoke / "doc_prompts.jsonl",
                '{"user_prompt":"evidence_scope=stock; retrieval_reason_tags=exact_ticker; duplicate_cluster_id=c1; matched_holdings=AAPL"}\n',
            )
            _write(smoke / "stock_prompts.jsonl", "{}\n")
            _write(smoke / "portfolio_prompts.jsonl", "{}\n")
            _write(
                smoke / "doc_extractions.csv",
                "doc_id,parse_status,evidence_scope,retrieval_reason_tags,duplicate_cluster_id,matched_holdings\n"
                "d1,ok,stock,exact_ticker,c1,AAPL\n",
            )
            _write(
                smoke / "daily_stock_text_features.csv",
                "tic,dominant_event_type,evidence_scopes\nAAPL,supply_chain,stock\n",
            )
            _write(
                smoke / "daily_portfolio_text_features.csv",
                "portfolio_id,evidence_scopes\np1,stock\n",
            )
            _write(smoke / "legacy_stock_features.csv", "tic\nAAPL\n")
            _write(smoke / "legacy_portfolio_features.csv", "portfolio_id\np1\n")
            _write(smoke / "feature_provenance.csv", "doc_id\n d1\n".replace(" d1", "d1"))

            summary = build_smoke_summary(contexts_path=contexts, smoke_dir=smoke)

        self.assertEqual(summary["status"], "passed")
        self.assertEqual(summary["coverage"]["holding_coverage_rate"], 1.0)
        self.assertTrue(all(summary["prompt_metadata_checks"].values()))

    def test_smoke_summary_fails_when_doc_extraction_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            contexts = root / "retrieved_contexts.jsonl"
            smoke = root / "fingpt_smoke"
            write_jsonl(contexts, [{"doc_id": "d1", "matched_holdings": ["AAPL"]}])
            _write(smoke / "leakage_report.json", json.dumps({"finportfolio_ir_leakage_rows": 0}))
            _write(smoke / "doc_prompts.jsonl", "{}\n")
            _write(smoke / "stock_prompts.jsonl", "{}\n")
            _write(smoke / "portfolio_prompts.jsonl", "{}\n")
            _write(smoke / "doc_extractions.csv", "doc_id,parse_status\n")
            _write(smoke / "daily_stock_text_features.csv", "tic\nAAPL\n")
            _write(smoke / "daily_portfolio_text_features.csv", "portfolio_id\np1\n")
            _write(smoke / "legacy_stock_features.csv", "tic\nAAPL\n")
            _write(smoke / "legacy_portfolio_features.csv", "portfolio_id\np1\n")
            _write(smoke / "feature_provenance.csv", "doc_id\n")

            summary = build_smoke_summary(contexts_path=contexts, smoke_dir=smoke)

        self.assertEqual(summary["status"], "failed")
        self.assertIn("Some context docs are missing from doc extractions.", summary["hard_issues"])


if __name__ == "__main__":
    unittest.main()
