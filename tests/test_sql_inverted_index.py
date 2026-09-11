"""The Catalyst BM25 statistics == the RDD ``build_bm25_index``, line by line and on a corpus.

Every edge-case line of ``tests/test_sql_analytics.py`` is indexed on its own by
both jobs and the artifacts must be equal (a Python crash must stay a crash);
the 24-document sample must match as a whole; and the plan must contain no
Python operator -- the point of the port.

    docker compose -f deploy/spark_cluster/docker-compose.yml exec -T spark-master \\
        python3 -m unittest tests.test_sql_inverted_index -v
"""

from pathlib import Path
import importlib.util
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SAMPLE = ROOT / "data" / "processed_documents" / "documents.jsonl"
HAVE_SPARK = importlib.util.find_spec("pyspark") is not None


def comparable(artifact: dict) -> dict:
    return {k: v for k, v in artifact.items() if k != "sql"}


@unittest.skipUnless(HAVE_SPARK, "pyspark not installed")
class SqlInvertedIndexTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from pyspark.sql import SparkSession

        cls.spark = (SparkSession.builder.master("local[2]").appName("test-sql-inverted-index")
                     .config("spark.ui.enabled", "false").config("spark.sql.shuffle.partitions", "2")
                     .getOrCreate())
        cls.spark.sparkContext.setLogLevel("ERROR")

    @classmethod
    def tearDownClass(cls):
        cls.spark.stop()

    def sql(self, lines):
        from bigdata.jobs import sql_inverted_index

        frame = self.spark.createDataFrame([(line,) for line in lines], "value string")
        return sql_inverted_index.build_bm25_index(frame)

    @staticmethod
    def reference(lines):
        from bigdata.engine import LocalEngine
        from bigdata.jobs import inverted_index

        engine = LocalEngine(num_workers=1)
        try:
            return inverted_index.build_bm25_index(engine.parallelize(lines))
        finally:
            engine.stop()

    def test_every_fixture_line(self):
        from tests.test_sql_analytics import FIXTURES

        for name, line, route in FIXTURES:
            with self.subTest(name):
                if route == "crash":
                    with self.assertRaises(Exception) as caught:
                        self.reference([line])
                    with self.assertRaises(type(caught.exception)):
                        self.sql([line])
                    continue
                self.assertEqual(comparable(self.sql([line])), self.reference([line]))

    def test_sample_corpus(self):
        lines = [l for l in SAMPLE.read_text(encoding="utf-8").split("\n") if l.strip()]
        got = self.sql(lines)
        self.assertEqual(got["sql"], {"fallback_lines": 0, "duplicate_doc_ids": 0})
        self.assertEqual(comparable(got), self.reference(lines))

    def test_plan_runs_without_python(self):
        from bigdata.jobs import sql_inverted_index

        frame = self.spark.createDataFrame([(SAMPLE.read_text(encoding="utf-8").split("\n")[0],)], "value string")
        plan = sql_inverted_index.entries(frame).groupBy("k").count()._jdf.queryExecution().executedPlan().toString()
        for operator in ("BatchEvalPython", "ArrowEvalPython", "PythonUDF", "MapInPandas", "FlatMapGroupsInPandas"):
            self.assertNotIn(operator, plan)
        self.assertEqual(plan.count("from_json("), 1, plan)


if __name__ == "__main__":
    unittest.main()
