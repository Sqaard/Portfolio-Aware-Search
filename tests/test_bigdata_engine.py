"""Correctness of the local MapReduce engine's Dataset operations."""

from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bigdata.engine import LocalEngine, get_engine
from bigdata.engine.base import key_partition


# Module-level ops (picklable) so the same tests exercise sequential and,
# where the pool engages, process-parallel execution identically.
def _split(line):
    return line.split()


def _to_pair(word):
    return (word, 1)


def _add(a, b):
    return a + b


def _is_even(n):
    return n % 2 == 0


def _partition_sum(iterator):
    yield sum(iterator)


class LocalEngineTests(unittest.TestCase):
    def setUp(self):
        self.engine = LocalEngine(num_workers=1, default_partitions=3)

    def tearDown(self):
        self.engine.stop()

    def test_parallelize_and_collect(self):
        ds = self.engine.parallelize([1, 2, 3, 4, 5])
        self.assertEqual(sorted(ds.collect()), [1, 2, 3, 4, 5])
        self.assertEqual(ds.count(), 5)

    def test_map_filter_flatmap(self):
        ds = self.engine.parallelize(["a b", "c", "d e f"])
        words = ds.flat_map(_split)
        self.assertEqual(sorted(words.collect()), ["a", "b", "c", "d", "e", "f"])
        evens = self.engine.parallelize(range(10)).filter(_is_even)
        self.assertEqual(sorted(evens.collect()), [0, 2, 4, 6, 8])
        squared = self.engine.parallelize([1, 2, 3]).map(lambda x: x * x)
        self.assertEqual(sorted(squared.collect()), [1, 4, 9])

    def test_reduce_by_key_wordcount(self):
        ds = self.engine.parallelize(["apple banana apple", "banana cherry", "apple"])
        counts = ds.flat_map(_split).map(_to_pair).reduce_by_key(_add).collect_as_map()
        self.assertEqual(counts, {"apple": 3, "banana": 2, "cherry": 1})

    def test_group_by_key(self):
        ds = self.engine.parallelize([("a", 1), ("b", 2), ("a", 3)])
        grouped = {k: sorted(v) for k, v in ds.group_by_key().collect()}
        self.assertEqual(grouped, {"a": [1, 3], "b": [2]})

    def test_distinct(self):
        ds = self.engine.parallelize([1, 1, 2, 3, 3, 3, 4])
        self.assertEqual(sorted(ds.distinct().collect()), [1, 2, 3, 4])

    def test_map_partitions(self):
        ds = self.engine.parallelize([1, 2, 3, 4, 5, 6], num_partitions=3)
        # Each partition summed, then summed on the driver -> total.
        self.assertEqual(sum(ds.map_partitions(_partition_sum).collect()), 21)

    def test_count_by_value_and_reduce(self):
        ds = self.engine.parallelize([1, 1, 2, 3, 3, 3])
        self.assertEqual(ds.count_by_value(), {1: 2, 2: 1, 3: 3})
        self.assertEqual(self.engine.parallelize([1, 2, 3, 4]).reduce(_add), 10)

    def test_keys_values_map_values(self):
        ds = self.engine.parallelize([("a", 1), ("b", 2)])
        self.assertEqual(sorted(ds.keys().collect()), ["a", "b"])
        self.assertEqual(sorted(ds.values().collect()), [1, 2])
        self.assertEqual(dict(ds.map_values(lambda v: v * 10).collect()), {"a": 10, "b": 20})

    def test_cache_is_semantically_transparent(self):
        ds = self.engine.parallelize(["a b", "b c"]).flat_map(_split).map(_to_pair).cache()
        self.assertEqual(ds.reduce_by_key(_add).collect_as_map(), {"a": 1, "b": 2, "c": 1})
        # A second action on the cached dataset yields the same result.
        self.assertEqual(ds.count(), 4)

    def test_take_is_bounded(self):
        ds = self.engine.parallelize(range(100))
        self.assertEqual(len(ds.take(5)), 5)
        self.assertEqual(ds.take(0), [])

    def test_text_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "lines.txt"
            path.write_text("one\n\ntwo\nthree\n", encoding="utf-8")
            ds = self.engine.text_file(str(path))
            self.assertEqual(sorted(ds.collect()), ["one", "three", "two"])

    def test_empty_reduce_raises(self):
        with self.assertRaises(ValueError):
            self.engine.parallelize([]).reduce(_add)

    def test_multiprocess_path_matches_sequential(self):
        # num_workers>1 uses the process pool for module-level ops (falling back
        # to sequential only if pickling fails); results must be identical.
        parallel_engine = LocalEngine(num_workers=2, default_partitions=4)
        try:
            ds = parallel_engine.parallelize(
                ["apple banana", "banana cherry apple", "cherry", "apple apple"],
                num_partitions=4,
            )
            counts = ds.flat_map(_split).map(_to_pair).reduce_by_key(_add).collect_as_map()
            self.assertEqual(counts, {"apple": 4, "banana": 2, "cherry": 2})
        finally:
            parallel_engine.stop()


class KeyPartitionTests(unittest.TestCase):
    def test_partition_is_stable_and_in_range(self):
        for key in ["apple", "banana", ("a", 1), 42, "инфляция"]:
            p = key_partition(key, 8)
            self.assertTrue(0 <= p < 8)
            self.assertEqual(p, key_partition(key, 8))  # deterministic

    def test_single_partition(self):
        self.assertEqual(key_partition("anything", 1), 0)


class FactoryTests(unittest.TestCase):
    def test_local_engine_selected(self):
        engine = get_engine("local")
        try:
            self.assertEqual(engine.name, "local")
        finally:
            engine.stop()

    def test_unknown_engine_rejected(self):
        with self.assertRaises(ValueError):
            get_engine("dask")


if __name__ == "__main__":
    unittest.main()
