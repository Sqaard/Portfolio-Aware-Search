"""Parity + correctness of the Big Data jobs against the single-machine code.

The central guarantee: the distributed inverted-index / BM25 statistics are
identical to ``indexing.build_sparse_index.BM25Index`` on the shared sample
corpus, and distributed BM25 query scores match ``BM25Index.score_query``.
"""

from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bigdata.engine import LocalEngine
from bigdata.jobs import corpus_analytics, inverted_index
from bigdata.jobs.mapping import source_family
from finportfolio_ir.io_utils import read_jsonl
from finportfolio_ir.schema import load_documents
from indexing.build_sparse_index import BM25Index

SAMPLE = ROOT / "data" / "processed_documents" / "documents.jsonl"


class InvertedIndexParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.documents = load_documents(read_jsonl(SAMPLE))
        cls.reference = BM25Index.from_documents(cls.documents)
        cls.ref_artifact = cls.reference.to_artifact()
        # num_workers=2 exercises the process-parallel path with module-level ops.
        cls.engine = LocalEngine(num_workers=2, default_partitions=4)
        cls.dataset = cls.engine.text_file(str(SAMPLE)).cache()
        cls.artifact = inverted_index.build_bm25_index(cls.dataset, top_terms=25)

    @classmethod
    def tearDownClass(cls):
        cls.engine.stop()

    def test_document_frequencies_match_reference(self):
        self.assertEqual(
            self.artifact["document_frequencies"],
            dict(self.ref_artifact["document_frequencies"]),
        )

    def test_document_lengths_match_reference(self):
        self.assertEqual(self.artifact["document_lengths"], self.ref_artifact["document_lengths"])

    def test_average_document_length_matches_reference(self):
        self.assertAlmostEqual(
            self.artifact["average_document_length"],
            self.ref_artifact["average_document_length"],
            places=9,
        )

    def test_n_docs_and_vocabulary(self):
        self.assertEqual(self.artifact["n_docs"], len(self.documents))
        self.assertEqual(self.artifact["vocabulary_size"], len(self.ref_artifact["document_frequencies"]))
        self.assertGreater(self.artifact["vocabulary_size"], 0)

    def test_query_scores_match_reference(self):
        for query in ["Apple supply chain", "inflation interest rates", "Microsoft cloud"]:
            distributed = dict(inverted_index.query_bm25(self.dataset, query, self.artifact, top_k=0))
            reference = self.reference.score_query(query)
            for doc_id, ref_score in reference.items():
                self.assertAlmostEqual(distributed.get(doc_id, 0.0), ref_score, places=9,
                                       msg=f"query={query!r} doc={doc_id}")

    def test_postings_are_consistent_with_df(self):
        postings = dict(inverted_index.build_postings(self.dataset).collect())
        # Every term's posting list length equals its document frequency.
        for term, df in self.artifact["document_frequencies"].items():
            self.assertEqual(len(postings[term]), df)


class AnalyticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = LocalEngine(num_workers=1, default_partitions=3)
        cls.dataset = cls.engine.text_file(str(SAMPLE)).cache()
        cls.report = corpus_analytics.compute_analytics(cls.dataset, top_n=10)

    @classmethod
    def tearDownClass(cls):
        cls.engine.stop()

    def test_total_documents(self):
        expected = len(load_documents(read_jsonl(SAMPLE)))
        self.assertEqual(self.report["total_documents"], expected)

    def test_family_counts_sum_to_total(self):
        self.assertEqual(sum(self.report["by_source_family"].values()), self.report["total_documents"])

    def test_year_and_point_in_time(self):
        self.assertTrue(all(year.isdigit() for year in self.report["by_year"]))
        pit = self.report["point_in_time"]
        self.assertLessEqual(pit["min_available_at"], pit["max_available_at"])

    def test_avg_document_length_is_positive(self):
        self.assertGreater(self.report["avg_document_length_tokens"], 0)

    def test_incremental_merge_equals_full(self):
        # Splitting the corpus, computing per-half, and merging must equal the
        # single-shot analytics -- this underwrites the streaming updater.
        lines = [l for l in SAMPLE.read_text(encoding="utf-8").splitlines() if l.strip()]
        half = len(lines) // 2
        m1, mn1, mx1, _ = corpus_analytics.raw_metrics(self.engine.parallelize(lines[:half]))
        m2, mn2, mx2, _ = corpus_analytics.raw_metrics(self.engine.parallelize(lines[half:]))
        merged = corpus_analytics.merge_metrics(m1, m2)
        min_all = min(d for d in (mn1, mn2) if d)
        max_all = max(d for d in (mx1, mx2) if d)
        incremental = corpus_analytics.finalize_report(merged, min_all, max_all, top_n=10)
        self.assertEqual(incremental["total_documents"], self.report["total_documents"])
        self.assertEqual(incremental["by_source_family"], self.report["by_source_family"])
        self.assertEqual(incremental["by_year"], self.report["by_year"])


class SourceFamilyParityTests(unittest.TestCase):
    def test_matches_search_index_reference(self):
        try:
            from indexing.build_search_index import _source_family as ref_source_family
        except Exception as exc:  # pragma: no cover - environment dependent
            self.skipTest(f"reference _source_family unavailable: {exc}")
        records = [
            {"source_type": "official_macro_release", "url": "https://fred.stlouisfed.org/x"},
            {"source_type": "", "canonical_url": "https://fred.stlouisfed.org/series"},
            {"source_type": "sec_filing_10k", "url": "https://www.sec.gov/x"},
            {"source_type": "", "source": "edgar", "url": ""},
            {"source_type": "company_ir_press", "url": "https://apple.com"},
            {"source_type": "sample", "url": "https://example.com"},
            {"source_type": "random_blog", "url": "https://blog.example.com"},
            {"source_type": "", "url": ""},
        ]
        for record in records:
            self.assertEqual(source_family(record), ref_source_family(record), msg=str(record))


if __name__ == "__main__":
    unittest.main()
