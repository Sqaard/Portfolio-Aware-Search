"""Big Data infrastructure layer for FinPortfolio IR.

This package is an *additive* layer for the Big Data Infrastructure course
project. It does not replace any existing pure-Python retrieval, indexing, or
evaluation code. Instead it re-expresses the heavy, corpus-wide processing steps
(tokenization, inverted-index / BM25 statistics, corpus analytics) as a
MapReduce computation that runs on a real Big Data framework (Apache Spark) and,
for portability and verification, on a small pure-Python multiprocessing
MapReduce engine that implements the same map/shuffle/reduce contract.

The two engines share a single ``Dataset`` interface (see ``bigdata.engine``),
so every job is written once and executes unchanged on either backend. This is
what lets us *verify* the Big Data path: the Spark output, the local-engine
output, and the original single-machine ``BM25Index`` are asserted equal on a
shared corpus (see ``tests/test_bigdata_*`` and ``bigdata/verify``).
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "1.0.0"
