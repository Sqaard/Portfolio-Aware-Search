"""Engine abstraction: one RDD-like ``Dataset`` interface, two backends.

* ``LocalEngine`` / ``LocalDataset`` -- a pure-Python multiprocessing MapReduce
  engine with no third-party dependency. Always runnable; used as the
  correctness oracle and the portable fallback.
* ``SparkEngine`` / ``SparkDataset`` -- a thin wrapper over a PySpark ``RDD``.
  The genuine Big Data framework deliverable.

Use :func:`bigdata.engine.factory.get_engine` to obtain an engine by name
(``"spark"`` / ``"local"`` / ``"auto"``).
"""

from __future__ import annotations

from .base import Dataset, Engine
from .factory import EngineUnavailableError, describe_backends, get_engine
from .local_mapreduce import LocalDataset, LocalEngine

__all__ = [
    "Dataset",
    "Engine",
    "LocalDataset",
    "LocalEngine",
    "EngineUnavailableError",
    "describe_backends",
    "get_engine",
]
