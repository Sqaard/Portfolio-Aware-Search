"""Parity: the Big Data index builder == indexing/build_search_index.py.

Both builders run over the shared sample corpus with identical feature inputs;
every table except the manifest (which carries timestamps) must match exactly.
"""

from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bigdata.engine import LocalEngine
from bigdata.engine.factory import spark_available
from bigdata.jobs import search_index
from bigdata.run_build_search_index import build_index_sqlite

SAMPLE = ROOT / "data" / "processed_documents" / "documents.jsonl"

COMPARED_TABLES = {
    "documents": "doc_id",
    "documents_fts": "doc_id",
    "document_features": "doc_id",
    "source_quality": "source_family, source_type",
    "ticker_coverage": "ticker",
}


def _dump_tables(db_path: Path) -> dict:
    connection = sqlite3.connect(db_path)
    try:
        out = {}
        for table, order in COMPARED_TABLES.items():
            out[table] = connection.execute(f"SELECT * FROM {table} ORDER BY {order}").fetchall()
        return out
    finally:
        connection.close()


def _build_reference(documents_path: Path, output_path: Path, features_csv: Path, relations_csv: Path):
    from indexing.build_search_index import build_search_index

    return build_search_index(
        documents_path=documents_path,
        output_path=output_path,
        text_features_path=features_csv,
        feature_relations_path=relations_csv,
    )


def _build_bigdata(engine, documents_path: Path, output_path: Path, features_csv: Path, relations_csv: Path):
    dataset = engine.text_file(str(documents_path))
    rows = dataset.map(search_index.index_rows).filter(search_index.not_none).collect()
    return build_index_sqlite(
        rows=rows,
        output_path=output_path,
        documents_path=documents_path,
        text_features_path=features_csv,
        feature_relations_path=relations_csv,
        engine_name=engine.name,
    )


class SearchIndexParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        base = Path(cls._tmp.name)
        # Deliberately-missing feature CSVs: both builders receive the same
        # (empty) feature inputs, which text_features() handles by design.
        cls.features_csv = base / "missing_features.csv"
        cls.relations_csv = base / "missing_relations.csv"
        cls.ref_db = base / "reference.sqlite"
        cls.big_db = base / "bigdata_local.sqlite"
        _build_reference(SAMPLE, cls.ref_db, cls.features_csv, cls.relations_csv)
        engine = LocalEngine(num_workers=2, default_partitions=3)
        try:
            _build_bigdata(engine, SAMPLE, cls.big_db, cls.features_csv, cls.relations_csv)
        finally:
            engine.stop()
        cls.ref_tables = _dump_tables(cls.ref_db)
        cls.big_tables = _dump_tables(cls.big_db)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_documents_table_identical(self):
        self.assertEqual(self.big_tables["documents"], self.ref_tables["documents"])
        self.assertGreater(len(self.big_tables["documents"]), 0)

    def test_fts_table_identical(self):
        self.assertEqual(self.big_tables["documents_fts"], self.ref_tables["documents_fts"])

    def test_feature_and_quality_tables_identical(self):
        self.assertEqual(self.big_tables["document_features"], self.ref_tables["document_features"])
        self.assertEqual(self.big_tables["source_quality"], self.ref_tables["source_quality"])
        self.assertEqual(self.big_tables["ticker_coverage"], self.ref_tables["ticker_coverage"])

    def test_manifest_is_drop_in_for_web_app(self):
        connection = sqlite3.connect(self.big_db)
        try:
            manifest = dict(connection.execute("SELECT key, value FROM manifest").fetchall())
        finally:
            connection.close()
        self.assertEqual(manifest["index_version"], "search_index_v1")
        self.assertEqual(manifest["document_count"], str(len(self.big_tables["documents"])))
        self.assertEqual(manifest["built_by"], "bigdata.run_build_search_index")


@unittest.skipUnless(spark_available(), "pyspark not installed")
class SearchIndexSparkParityTests(unittest.TestCase):
    def test_spark_built_index_matches_reference(self):
        from bigdata.engine.spark_engine import SparkEngine

        try:
            engine = SparkEngine(master="local[2]", app_name="finportfolio-index-test")
        except Exception as exc:  # pragma: no cover - environment dependent
            raise unittest.SkipTest(f"Spark could not start: {exc}")
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            features_csv = base / "missing_features.csv"
            relations_csv = base / "missing_relations.csv"
            ref_db = base / "reference.sqlite"
            spark_db = base / "bigdata_spark.sqlite"
            _build_reference(SAMPLE, ref_db, features_csv, relations_csv)
            try:
                _build_bigdata(engine, SAMPLE, spark_db, features_csv, relations_csv)
            finally:
                engine.stop()
            self.assertEqual(_dump_tables(spark_db), _dump_tables(ref_db))


if __name__ == "__main__":
    unittest.main()
