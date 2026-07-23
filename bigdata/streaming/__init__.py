"""Streaming / automatic-update jobs for the Big Data layer.

Two complementary implementations of the course's 'automatic updates' requirement:

* :mod:`bigdata.streaming.incremental_update` -- an engine-agnostic, poll-based
  micro-batch updater. It watches an inbox directory, processes only newly
  arrived / changed JSONL files, and merges their aggregates into append-only
  state. Runs on Spark or the local engine and is fully verifiable on Windows.
* :mod:`bigdata.streaming.spark_structured_streaming` -- a genuine Spark
  Structured Streaming job (file source + windowed aggregation + ``foreachBatch``)
  for the real streaming-framework story (best on the Docker cluster / Linux).
"""

from __future__ import annotations

__all__ = ["incremental_update", "spark_structured_streaming"]
