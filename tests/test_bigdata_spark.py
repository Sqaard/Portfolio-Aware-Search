"""Spark-backed parity: Spark == local engine == single-machine BM25Index.

Skipped automatically when PySpark is not installed or a SparkSession cannot be
started (e.g. no JVM), so the suite stays green on machines without Java.
"""

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bigdata.engine.factory import spark_available
from bigdata.jobs import corpus_analytics, inverted_index
from finportfolio_ir.io_utils import read_jsonl
from finportfolio_ir.schema import load_documents
from indexing.build_sparse_index import BM25Index

SAMPLE = ROOT / "data" / "processed_documents" / "documents.jsonl"


@unittest.skipUnless(spark_available(), "pyspark not installed")
class SparkParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from bigdata.engine.spark_engine import SparkEngine

        try:
            cls.engine = SparkEngine(master="local[2]", app_name="finportfolio-ir-tests")
        except Exception as exc:  # pragma: no cover - environment dependent
            raise unittest.SkipTest(f"Spark could not start: {exc}")
        cls.dataset = cls.engine.text_file(str(SAMPLE)).cache()
        cls.documents = load_documents(read_jsonl(SAMPLE))
        cls.reference = BM25Index.from_documents(cls.documents)
        cls.ref_artifact = cls.reference.to_artifact()
        cls.artifact = inverted_index.build_bm25_index(cls.dataset, top_terms=25)

    @classmethod
    def tearDownClass(cls):
        engine = getattr(cls, "engine", None)
        if engine is not None:
            engine.stop()

    def test_spark_bm25_matches_reference(self):
        self.assertEqual(
            self.artifact["document_frequencies"],
            dict(self.ref_artifact["document_frequencies"]),
        )
        self.assertEqual(self.artifact["document_lengths"], self.ref_artifact["document_lengths"])
        self.assertAlmostEqual(
            self.artifact["average_document_length"],
            self.ref_artifact["average_document_length"],
            places=9,
        )
        self.assertEqual(self.artifact["n_docs"], len(self.documents))

    def test_spark_query_matches_reference(self):
        for query in ["Apple supply chain", "inflation interest rates"]:
            distributed = dict(inverted_index.query_bm25(self.dataset, query, self.artifact, top_k=0))
            reference = self.reference.score_query(query)
            for doc_id, ref_score in reference.items():
                self.assertAlmostEqual(distributed.get(doc_id, 0.0), ref_score, places=9)

    def test_spark_analytics_totals(self):
        report = corpus_analytics.compute_analytics(self.dataset, top_n=10)
        self.assertEqual(report["total_documents"], len(self.documents))
        self.assertEqual(sum(report["by_source_family"].values()), report["total_documents"])


if __name__ == "__main__":
    unittest.main()
