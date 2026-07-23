"""Abstract ``Dataset`` / ``Engine`` contract shared by every backend.

The surface is intentionally a small subset of the Spark RDD API -- just enough
to express the MapReduce jobs in ``bigdata.jobs``:

Transformations (lazy): ``map``, ``flat_map``, ``filter``, ``map_partitions``,
``reduce_by_key``, ``group_by_key``, ``distinct``, ``keys``, ``values``.

Actions (eager): ``collect``, ``collect_as_map``, ``count``, ``take``,
``reduce``, ``count_by_value``, ``foreach``.

Keeping the interface tiny is what makes the local engine and Spark truly
interchangeable, and what makes the jobs verifiable across both.
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from typing import Any, Callable, Iterable, Iterator


def key_partition(key: Any, num_partitions: int) -> int:
    """Deterministic, cross-process stable partitioner for a shuffle key.

    ``hash()`` is salted per-process in modern CPython, so it cannot be used to
    assign shuffle partitions consistently across worker processes. We hash the
    ``repr`` of the key with a stable digest instead.
    """

    if num_partitions <= 1:
        return 0
    digest = hashlib.blake2b(repr(key).encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") % num_partitions


class Dataset(ABC):
    """An immutable, partitioned collection of records (an RDD in miniature)."""

    # -- transformations ---------------------------------------------------
    @abstractmethod
    def map(self, fn: Callable[[Any], Any]) -> "Dataset":
        """Return a dataset with ``fn`` applied to every element."""

    @abstractmethod
    def flat_map(self, fn: Callable[[Any], Iterable[Any]]) -> "Dataset":
        """Return a dataset flattening ``fn(element)`` for every element."""

    @abstractmethod
    def filter(self, fn: Callable[[Any], bool]) -> "Dataset":
        """Return a dataset keeping only elements where ``fn`` is truthy."""

    @abstractmethod
    def map_partitions(self, fn: Callable[[Iterator[Any]], Iterable[Any]]) -> "Dataset":
        """Return a dataset transforming each partition's iterator as a whole."""

    @abstractmethod
    def reduce_by_key(self, fn: Callable[[Any, Any], Any]) -> "Dataset":
        """For a dataset of ``(key, value)`` pairs, combine values per key.

        ``fn`` must be associative and commutative; engines are free to apply it
        map-side (a combiner) before the shuffle.
        """

    @abstractmethod
    def group_by_key(self) -> "Dataset":
        """For a dataset of ``(key, value)`` pairs, return ``(key, [values])``."""

    @abstractmethod
    def distinct(self) -> "Dataset":
        """Return a dataset with duplicate elements removed."""

    def cache(self) -> "Dataset":
        """Hint that this dataset is reused; backends may materialize/persist it.

        The default is a no-op; ``LocalDataset`` materializes the narrow-op chain
        once and ``SparkDataset`` calls ``RDD.cache``. Semantics are unchanged
        either way -- only repeated recomputation is avoided.
        """

        return self

    def keys(self) -> "Dataset":
        """For a ``(key, value)`` dataset, return just the keys."""

        return self.map(_first)

    def values(self) -> "Dataset":
        """For a ``(key, value)`` dataset, return just the values."""

        return self.map(_second)

    def map_values(self, fn: Callable[[Any], Any]) -> "Dataset":
        """For a ``(key, value)`` dataset, apply ``fn`` to each value."""

        return self.map(_MapValues(fn))

    # -- actions -----------------------------------------------------------
    @abstractmethod
    def collect(self) -> list:
        """Materialize every element into a driver-side list."""

    @abstractmethod
    def count(self) -> int:
        """Return the number of elements."""

    @abstractmethod
    def reduce(self, fn: Callable[[Any, Any], Any]) -> Any:
        """Aggregate all elements to a single value with ``fn``."""

    @abstractmethod
    def count_by_value(self) -> dict:
        """Return a ``{element: occurrences}`` histogram to the driver."""

    def collect_as_map(self) -> dict:
        """Materialize a ``(key, value)`` dataset into a driver-side dict."""

        return dict(self.collect())

    def take(self, n: int) -> list:
        """Return up to ``n`` elements (order is not guaranteed)."""

        if n <= 0:
            return []
        out: list = []
        for item in self.collect():
            out.append(item)
            if len(out) >= n:
                break
        return out

    def foreach(self, fn: Callable[[Any], None]) -> None:
        """Apply ``fn`` to every element for its side effects (driver-side)."""

        for item in self.collect():
            fn(item)


class Engine(ABC):
    """A factory for :class:`Dataset` objects, plus lifecycle management."""

    name: str = "abstract"

    @abstractmethod
    def parallelize(self, items: Iterable[Any], num_partitions: int | None = None) -> Dataset:
        """Distribute an in-memory iterable into a dataset."""

    @abstractmethod
    def text_file(self, paths: Any, num_partitions: int | None = None) -> Dataset:
        """Return a dataset with one element per non-empty line of the input.

        ``paths`` may be a single path or an iterable of paths.
        """

    def stop(self) -> None:  # noqa: D401 - optional lifecycle hook
        """Release backend resources. Safe to call more than once."""

    def __enter__(self) -> "Engine":
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()


# Module-level helpers so they remain picklable for process-based backends.
def _first(pair: Any) -> Any:
    return pair[0]


def _second(pair: Any) -> Any:
    return pair[1]


class _MapValues:
    """Picklable ``(k, v) -> (k, fn(v))`` callable."""

    __slots__ = ("fn",)

    def __init__(self, fn: Callable[[Any], Any]) -> None:
        self.fn = fn

    def __call__(self, pair: Any) -> Any:
        key, value = pair
        return (key, self.fn(value))
