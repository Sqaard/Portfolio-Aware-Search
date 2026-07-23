# `bigdata/` — Big Data layer for FinPortfolio IR

Distributed, MapReduce-style processing of the FinPortfolio IR corpora on
**Apache Spark**, with a portable pure-Python engine behind the same interface.
Additive: it does not modify any existing code. Full write-up:
[`docs/BIG_DATA_INFRASTRUCTURE.md`](../docs/BIG_DATA_INFRASTRUCTURE.md).

## Quickstart

```powershell
# Optional: install Spark to use the distributed engine (else the local engine runs)
pip install pyspark==3.5.3

# Full pipeline → data/exports/bigdata/report/{analytics.json,bm25_stats.json,REPORT.md}
python -m bigdata.run_all --corpus macro --query "inflation interest rates"

# Pick an engine (default auto → Spark if installed, else local)
python -m bigdata.run_all --corpus sample --engine local
python -m bigdata.run_all --corpus macro  --engine spark --master "local[*]"
```

## Layout

```
bigdata/
  engine/                 one Dataset interface, two backends
    base.py               abstract Dataset/Engine (map, reduce_by_key, cache, …)
    local_mapreduce.py    pure-Python multiprocessing MapReduce (fallback + oracle)
    spark_engine.py       PySpark RDD backend (JAVA_HOME repair, Windows-safe IO)
    factory.py            get_engine("auto"|"spark"|"local")
  jobs/
    mapping.py            JSONL → validated, causal-safe document (reuses schema/tokenizer)
    inverted_index.py     distributed BM25 stats + postings + distributed query
    corpus_analytics.py   single-pass corpus aggregates (family/type/year/ticker/event/risk)
  streaming/
    incremental_update.py        poll-based micro-batch auto-updater (Windows-safe)
    spark_structured_streaming.py Spark Structured Streaming file-source job
  run_inverted_index.py   run_corpus_analytics.py   run_all.py   CLIs
  config.py               corpus registry + BM25 constants
```

## Engines

| | `LocalEngine` | `SparkEngine` |
| --- | --- | --- |
| Dependency | none (stdlib) | `pyspark` + a JVM (Java 8/11/17) |
| Parallelism | process pool over partitions | Spark executors / cluster |
| Role | portable fallback **+** correctness oracle | the Big Data framework |

Both implement the same `Dataset` operations, so every job runs unchanged on
either. Correctness is guaranteed by parity tests asserting **Spark == local ==
the single-machine `BM25Index`** (`tests/test_bigdata_*.py`).

## Tests

```powershell
python -m unittest discover -s tests    # Spark tests self-skip if Java/PySpark absent
```

## Distributed cluster

See [`deploy/spark_cluster/`](../deploy/spark_cluster) for a Docker Compose
standalone cluster (1 master + 2 workers) and a `submit.sh` helper.
