from datetime import date
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from crawler.collect_sec_dow30 import FilingCandidate, select_balanced_filings
from features.build_sec_dow30_300_contexts import target_ticker
from features.build_sec_medium_handoff import _select_unique_context_rows, _split_for_available_at
from features.export_fingpt_contexts import export_context_records


def filing(form: str, filing_date: str, accession: str) -> FilingCandidate:
    return FilingCandidate(
        ticker="AAPL",
        cik="0000320193",
        company_name="Apple Inc.",
        sector="Information Technology",
        accession_number=accession,
        filing_date=date.fromisoformat(filing_date),
        report_date=filing_date,
        form=form,
        primary_document="aapl.htm",
    )


class SecMediumHandoffTests(unittest.TestCase):
    def test_select_balanced_filings_covers_train_and_test(self):
        candidates = [
            filing("10-K", "2011-10-26", "a"),
            filing("10-Q", "2015-04-28", "b"),
            filing("8-K", "2019-01-30", "c"),
            filing("10-Q", "2020-07-31", "d"),
            filing("10-K", "2021-10-29", "e"),
            filing("10-Q", "2022-04-29", "f"),
            filing("8-K", "2022-09-07", "g"),
        ]

        selected = select_balanced_filings(candidates, train_per_ticker=3, test_per_ticker=2)

        self.assertEqual(len(selected), 5)
        self.assertEqual(sum(1 for item in selected if item.filing_date < date(2021, 10, 1)), 3)
        self.assertEqual(sum(1 for item in selected if item.filing_date >= date(2021, 10, 1)), 2)

    def test_context_export_preserves_split_fields(self):
        base = {
            "portfolio_id": "p",
            "decision_date": "2022-01-01",
            "decision_time": "2022-01-01T15:00:00Z",
            "retrieval_cutoff": "2022-01-01T15:00:00Z",
            "rank": 1,
            "doc_id": "d1",
            "published_at": "2021-12-31T23:59:59Z",
            "available_at": "2021-12-31T23:59:59Z",
            "title": "AAPL 10-K",
            "body_excerpt": "Apple filing",
            "matched_tickers": ["AAPL"],
            "portfolio_weight_sum": 1.0,
            "sparse_score": 1.0,
            "dense_score": 0.0,
            "entity_score": 1.0,
            "recency_score": 1.0,
            "event_importance_score": 0.0,
            "final_score": 1.0,
            "document_hash": "hash",
            "split": "test",
            "document_split": "test",
        }

        exported = export_context_records([base])

        self.assertEqual(exported[0]["split"], "test")
        self.assertEqual(exported[0]["document_split"], "test")

    def test_unique_context_selection_preserves_split_order(self):
        rows = [
            {"doc_id": "train_1", "split": "train", "portfolio_id": "p", "rank": 2},
            {"doc_id": "train_1", "split": "train", "portfolio_id": "p", "rank": 3},
            {"doc_id": "test_1", "split": "test", "portfolio_id": "p", "rank": 1},
        ]

        selected = _select_unique_context_rows(rows, 2)

        self.assertEqual([row["doc_id"] for row in selected], ["train_1", "test_1"])
        self.assertEqual([row["rank"] for row in selected], [1, 2])

    def test_split_for_available_at_uses_oos_boundary(self):
        self.assertEqual(_split_for_available_at("2021-09-30T23:59:59Z"), "train")
        self.assertEqual(_split_for_available_at("2021-10-01T23:59:59Z"), "test")

    def test_sec300_target_ticker_ignores_market_pseudo_ticker(self):
        document = {"matched_tickers": ["MARKET", "PG"], "tickers_detected": ["MARKET", "PG"]}

        self.assertEqual(target_ticker(document), "PG")


if __name__ == "__main__":
    unittest.main()
