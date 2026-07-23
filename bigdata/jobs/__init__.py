"""MapReduce jobs for the FinPortfolio IR Big Data layer.

Each job is expressed purely against the :class:`bigdata.engine.Dataset`
contract, so it runs unchanged on Spark or on the local MapReduce engine. All
map/reduce callables are module-level (hence picklable), which the local
process-pool backend requires.
"""

from __future__ import annotations

from . import corpus_analytics, inverted_index, mapping, search_index

__all__ = ["mapping", "inverted_index", "corpus_analytics", "search_index"]
