# `bigdata/` — Big Data layer for FinPortfolio IR

Distributed, MapReduce-style processing of the FinPortfolio IR corpora on
**Apache Spark**, with a portable pure-Python engine behind the same interface.
Additive: it does not modify any existing code. Full write-up:
[`docs/BIG_DATA_INFRASTRUCTURE.md`](../docs/BIG_DATA_INFRASTRUCTURE.md).

## Quickstart

```powershell
# Optional: install Spark to use the distributed engine (else the local engine runs)
pip install -r requirements-bigdata.txt          # run from the repository root

# Full pipeline over the corpus committed here (993 docs, ~1 s, no JVM needed)
# → data/exports/bigdata/local_runs/report/{analytics.json,bm25_stats.json,REPORT.md}
python -m bigdata.run_all --corpus repo_demo --query "inflation interest rates"

# Pick an engine (default auto → Spark if installed, else local)
python -m bigdata.run_all --corpus repo_demo --engine local
python -m bigdata.run_all --corpus repo_demo --engine spark --master "local[*]"
```

`repo_demo` is the default corpus and the only large one in the repository
(`data/processed_documents/repo_demo_documents.jsonl`, 993 documents, 11.5 MB,
`vocabulary_size = 21,211`); `sample` is the 24-document corpus the parity tests
use. The `sec300`, `macro`, `sec_ppo`, `sec_sections` and `all_ppo` names in
`config.py` point at corpora from 39 MB to 352 MB that are **not** committed —
the runs over them are, under `data/exports/bigdata/report_macro_local/`,
`report_macro_cluster/` and `report_all_ppo_cluster/`. Output goes to
`data/exports/bigdata/local_runs/`, which is git-ignored, so those committed
reports are never overwritten.

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
    sql_inverted_index.py the BM25 statistics as one Catalyst job, exact (run_inverted_index --api sql)
  streaming/
    incremental_update.py        poll-based micro-batch auto-updater (Windows-safe)
    spark_structured_streaming.py Structured Streaming file source (--analytics full|sql: batch report per micro-batch)
    sql_analytics.py             the report's per-document work as Catalyst SQL, exact (atypical records → Python)
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
either. Correctness is pinned by parity tests asserting **Spark == local == the
single-machine `BM25Index`** (`tests/test_bigdata_*.py`). Four of those tests
need a real PySpark and a JVM and **self-skip without them**, so a clean install
exercises the `local == BM25Index` half only — see
[`docs/BIG_DATA_INFRASTRUCTURE.md`](../docs/BIG_DATA_INFRASTRUCTURE.md) § 8 for
what that does and does not prove, and for how to run the Spark half.

## Tests

```powershell
python -m unittest discover -s tests                          # -> Ran 243 tests, OK (skipped=5)
python -m unittest discover -s tests -p "test_bigdata_*.py"   # -> Ran 42 tests, OK (skipped=5)
```

Four of the 5 skips are the real-Spark parity tests; they run once PySpark and a
Java 8/11/17 runtime are present. The fifth needs the search index built.

## Distributed cluster

See [`deploy/spark_cluster/`](../deploy/spark_cluster) for a Docker Compose
standalone cluster (1 master + 2 workers) and a `submit.sh` helper.
