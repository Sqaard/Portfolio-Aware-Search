from pathlib import Path
import csv
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

import sys

sys.path.insert(0, str(ROOT))

from finportfolio_ir.io_utils import read_jsonl
from indexing.build_search_index import build_search_index
from web_app import FinPortfolioWebService, SIGNAL_FEATURE_COLUMNS, SIGNAL_FLAG_COLUMNS


class SearchIndexTests(unittest.TestCase):
    def _write_feature_seed(self, path: Path) -> None:
        fieldnames = [
            "doc_id",
            *SIGNAL_FEATURE_COLUMNS,
            *SIGNAL_FLAG_COLUMNS,
            "impact_direction",
            "retrieval_layer",
            "query_intent_primary",
        ]
        row = {name: "0" for name in fieldnames}
        row.update(
            {
                "doc_id": "doc_000002",
                "sentiment_proxy": "0.7",
                "opportunity_intensity": "0.9",
                "forward_looking_intensity": "0.8",
                "portfolio_action_relevance": "0.9",
                "event_severity_score": "0.7",
                "final_score": "0.8",
                "signal_earnings_guidance": "1",
                "impact_direction": "positive",
                "retrieval_layer": "stock",
                "query_intent_primary": "earnings_guidance",
            }
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerow(row)

    def test_build_search_index_and_use_signal_candidate_search(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            documents_path = ROOT / "data" / "processed_documents" / "documents.jsonl"
            features_path = tmp / "features.csv"
            index_path = tmp / "search.sqlite"
            self._write_feature_seed(features_path)

            summary = build_search_index(
                documents_path=documents_path,
                output_path=index_path,
                text_features_path=features_path,
                feature_relations_path=tmp / "missing_relations.csv",
            )
            service = FinPortfolioWebService(
                settings_path=tmp / "settings.json",
                documents_path=documents_path,
                text_features_path=features_path,
                feature_relations_path=tmp / "missing_relations.csv",
                search_index_path=index_path,
            )
            payload = service.search_payload("what stock should I invest in")

        self.assertEqual(summary["document_count"], len(read_jsonl(documents_path)))
        self.assertEqual(summary["feature_doc_count"], 1)
        self.assertTrue(payload["corpus"]["search_index"]["usable"])
        self.assertGreaterEqual(payload["count"], 1)
        self.assertEqual(payload["results"][0]["doc_id"], "doc_000002")
        self.assertGreater(payload["results"][0]["signal_strength"], 0)


if __name__ == "__main__":
    unittest.main()
