from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from finportfolio_ir.time_utils import parse_datetime
from retrieval.retrieve_for_portfolio import retrieval_records


class CausalFilteringTests(unittest.TestCase):
    def test_future_documents_are_not_retrieved(self):
        records = retrieval_records(
            documents_path=ROOT / "data" / "processed_documents" / "documents.jsonl",
            portfolio_path=ROOT / "configs" / "sample_portfolio.yaml",
            metadata_path=ROOT / "data" / "processed_documents" / "ticker_metadata.csv",
            decision_datetime_text="2022-03-15T09:30:00-05:00",
            config_path=ROOT / "configs" / "default.yaml",
            top_k=20,
        )

        doc_ids = {record["doc_id"] for record in records}
        self.assertNotIn("doc_000005", doc_ids)
        for record in records:
            self.assertLessEqual(
                parse_datetime(record["available_at"]),
                parse_datetime(record["retrieval_cutoff"]),
            )


if __name__ == "__main__":
    unittest.main()
