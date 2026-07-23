"""A small, dependency-free MapReduce engine (the portable fallback + oracle).

Design mirrors the Spark RDD execution model closely enough to be a faithful
teaching implementation of MapReduce:

* Data lives in *partitions* (lists). The map/filter/flatMap/mapPartitions
  transformations are *narrow* -- they run independently per partition and are
  lazily fused into a single generator pipeline, then executed once per
  partition (optionally across a process pool for real multi-core speedup).
* ``reduce_by_key`` performs a *map-side combine* inside each partition (a
  MapReduce "combiner") and then a driver-side shuffle+merge, exactly like
  Spark's ``reduceByKey``. ``group_by_key`` / ``distinct`` shuffle by a
  process-stable partitioner.

Parallelism is best-effort: if the fused operation chain (or its arguments) is
not picklable -- e.g. it closes over a lambda -- the engine transparently falls
back to sequential, in-process execution. Results are identical either way,
which is precisely what the verification tests rely on.
"""

from __future__ import annotations

import os
import sys
from concurrent.futures import ProcessPoolExecutor
from typing import Any, Callable, Iterable, Iterator, Sequence

from .base import Dataset, Engine, key_partition

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# --------------------------------------------------------------------------
# Narrow-op pipeline (module-level so worker processes can import/apply it).
# --------------------------------------------------------------------------
def _apply_ops(ops: Sequence[tuple], iterable: Iterable[Any]) -> Iterator[Any]:
    """Lazily compose a chain of narrow operations over one partition."""

    it: Iterator[Any] = iter(iterable)
    for name, fn in ops:
        if name == "map":
            it = map(fn, it)
        elif name == "filter":
            it = filter(fn, it)
        elif name == "flatMap":
            it = _flat_map_iter(fn, it)
        elif name == "mapPartitions":
            it = iter(fn(it))
        else:  # pragma: no cover - guarded by construction
            raise ValueError(f"Unknown narrow op: {name}")
    return it


def _flat_map_iter(fn: Callable[[Any], Iterable[Any]], it: Iterator[Any]) -> Iterator[Any]:
    for element in it:
        yield from fn(element)


def _narrow_to_list(args: tuple) -> list:
    ops, partition = args
    return list(_apply_ops(ops, partition))


def _narrow_to_combined(args: tuple) -> dict:
    """Apply narrow ops then fold ``(key, value)`` pairs with a combiner."""

    ops, partition, reduce_fn = args
    combined: dict = {}
    for pair in _apply_ops(ops, partition):
        key, value = pair
        if key in combined:
            combined[key] = reduce_fn(combined[key], value)
        else:
            combined[key] = value
    return combined


def _worker_init(sys_path: Sequence[str]) -> None:
    """Ensure spawned workers can import ``bigdata`` and ``finportfolio_ir``."""

    for entry in sys_path:
        if entry and entry not in sys.path:
            sys.path.insert(0, entry)


class LocalEngine(Engine):
    """Multiprocessing MapReduce engine backed by driver-side shuffles."""

    name = "local"

    def __init__(self, num_workers: int | None = None, default_partitions: int | None = None) -> None:
        cpu = os.cpu_count() or 1
        if num_workers is None:
            num_workers = cpu
        self.num_workers = max(1, int(num_workers))
        if default_partitions is None:
            default_partitions = max(self.num_workers, 1)
        self.default_partitions = max(1, int(default_partitions))
        self._pool: ProcessPoolExecutor | None = None
        self._pool_broken = False
        # Make the project importable inside spawned workers regardless of how
        # the driver was launched (``-m``, script, or pytest).
        if self.num_workers > 1:
            extra = os.pathsep.join(p for p in (_ROOT,) if p)
            existing = os.environ.get("PYTHONPATH", "")
            if _ROOT not in existing.split(os.pathsep):
                os.environ["PYTHONPATH"] = extra + (os.pathsep + existing if existing else "")

    # -- lifecycle ---------------------------------------------------------
    def _get_pool(self) -> ProcessPoolExecutor | None:
        if self.num_workers <= 1 or self._pool_broken:
            return None
        if self._pool is None:
            try:
                self._pool = ProcessPoolExecutor(
                    max_workers=self.num_workers,
                    initializer=_worker_init,
                    initargs=(list(sys.path),),
                )
            except Exception:  # pragma: no cover - platform dependent
                self._pool_broken = True
                self._pool = None
        return self._pool

    def stop(self) -> None:
        if self._pool is not None:
            self._pool.shutdown(wait=True, cancel_futures=True)
            self._pool = None

    def _run_partitions(self, worker: Callable[[tuple], Any], arglist: list) -> list:
        """Run ``worker`` over per-partition arg tuples, parallel if possible."""

        if len(arglist) <= 1:
            return [worker(args) for args in arglist]
        pool = self._get_pool()
        if pool is not None:
            try:
                return list(pool.map(worker, arglist))
            except Exception:
                # Pickling failure or a dead pool: degrade to sequential.
                self._pool_broken = True
                self._pool = None
        return [worker(args) for args in arglist]

    # -- sources -----------------------------------------------------------
    def parallelize(self, items: Iterable[Any], num_partitions: int | None = None) -> "LocalDataset":
        data = list(items)
        parts = self._chunk(data, num_partitions or self.default_partitions)
        return LocalDataset(self, parts, [])

    def text_file(self, paths: Any, num_partitions: int | None = None) -> "LocalDataset":
        lines: list[str] = []
        for path in _as_path_list(paths):
            with open(path, "r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.rstrip("\n")
                    if line.strip():
                        lines.append(line)
        parts = self._chunk(lines, num_partitions or self.default_partitions)
        return LocalDataset(self, parts, [])

    @staticmethod
    def _chunk(data: list, num_partitions: int) -> list:
        num_partitions = max(1, min(num_partitions, len(data) or 1))
        size = len(data)
        base, extra = divmod(size, num_partitions)
        parts: list = []
        start = 0
        for i in range(num_partitions):
            length = base + (1 if i < extra else 0)
            parts.append(data[start:start + length])
            start += length
        return parts


class LocalDataset(Dataset):
    """A partitioned dataset with a lazily-fused narrow-op chain."""

    def __init__(self, engine: LocalEngine, partitions: list, ops: list) -> None:
        self._engine = engine
        self._partitions = partitions  # list[list]
        self._ops = ops  # list[(op_name, fn)]

    # -- narrow transformations (lazy) ------------------------------------
    def _with_op(self, name: str, fn: Any) -> "LocalDataset":
        return LocalDataset(self._engine, self._partitions, self._ops + [(name, fn)])

    def map(self, fn: Callable[[Any], Any]) -> "LocalDataset":
        return self._with_op("map", fn)

    def flat_map(self, fn: Callable[[Any], Iterable[Any]]) -> "LocalDataset":
        return self._with_op("flatMap", fn)

    def filter(self, fn: Callable[[Any], bool]) -> "LocalDataset":
        return self._with_op("filter", fn)

    def map_partitions(self, fn: Callable[[Iterator[Any]], Iterable[Any]]) -> "LocalDataset":
        return self._with_op("mapPartitions", fn)

    def cache(self) -> "LocalDataset":
        # Run the pending narrow-op chain once and keep the concrete partitions,
        # so subsequent actions do not re-tokenize the corpus.
        return LocalDataset(self._engine, self._materialize(), [])

    # -- materialization ---------------------------------------------------
    def _materialize(self) -> list:
        """Execute the narrow-op chain, returning a list of partitions."""

        if not self._ops:
            return [list(part) for part in self._partitions]
        arglist = [(self._ops, part) for part in self._partitions]
        return self._engine._run_partitions(_narrow_to_list, arglist)

    def _materialize_combined(self, reduce_fn: Callable[[Any, Any], Any]) -> list:
        """Execute narrow ops and map-side-combine ``(k, v)`` pairs per partition."""

        arglist = [(self._ops, part, reduce_fn) for part in self._partitions]
        return self._engine._run_partitions(_narrow_to_combined, arglist)

    # -- wide transformations (shuffle) -----------------------------------
    def reduce_by_key(self, fn: Callable[[Any, Any], Any]) -> "LocalDataset":
        combined_dicts = self._materialize_combined(fn)
        merged: dict = {}
        for local in combined_dicts:
            for key, value in local.items():
                if key in merged:
                    merged[key] = fn(merged[key], value)
                else:
                    merged[key] = value
        return self._shuffle_items(list(merged.items()))

    def group_by_key(self) -> "LocalDataset":
        grouped: dict = {}
        for part in self._materialize():
            for key, value in part:
                grouped.setdefault(key, []).append(value)
        return self._shuffle_items([(k, v) for k, v in grouped.items()])

    def distinct(self) -> "LocalDataset":
        seen: dict = {}
        for part in self._materialize():
            for element in part:
                seen.setdefault(element, None)
        return self._shuffle_items(list(seen.keys()), key_of=lambda e: e)

    def _shuffle_items(self, items: list, key_of: Callable[[Any], Any] | None = None) -> "LocalDataset":
        n = self._engine.default_partitions
        parts: list = [[] for _ in range(n)]
        for item in items:
            key = item[0] if key_of is None else key_of(item)
            parts[key_partition(key, n)].append(item)
        return LocalDataset(self._engine, parts, [])

    # -- actions -----------------------------------------------------------
    def collect(self) -> list:
        out: list = []
        for part in self._materialize():
            out.extend(part)
        return out

    def count(self) -> int:
        return sum(len(part) for part in self._materialize())

    def reduce(self, fn: Callable[[Any, Any], Any]) -> Any:
        acc = _UNSET
        for part in self._materialize():
            for element in part:
                acc = element if acc is _UNSET else fn(acc, element)
        if acc is _UNSET:
            raise ValueError("reduce() called on an empty dataset")
        return acc

    def count_by_value(self) -> dict:
        counts: dict = {}
        for part in self._materialize():
            for element in part:
                counts[element] = counts.get(element, 0) + 1
        return counts


class _Unset:
    __slots__ = ()


_UNSET = _Unset()


def _as_path_list(paths: Any) -> list:
    if isinstance(paths, (str, bytes, os.PathLike)):
        return [os.fspath(paths)]
    return [os.fspath(p) for p in paths]
