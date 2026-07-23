# Big Data Infrastructure

## 1. Course requirement mapping

| Course requirement | Where it is delivered |
| --- | --- |
| **1. Data gathering** with automatic updates | Pre-existing crawler (`crawler/`, `deploy/update_live_ir.py`, `crawler/live_incremental_fetch.py`) **+** new streaming auto-updater ([`bigdata/streaming/`](../bigdata/streaming)) |
| **2. Selection & implementation of a Big Data technology** | **Apache Spark** (RDD MapReduce + Structured Streaming), implemented in [`bigdata/`](../bigdata), with a portable pure-Python MapReduce engine behind one shared interface |
| **3. Processing & analysis of the collected data** | Distributed **inverted index / BM25** build + **corpus analytics**, producing `analytics.json`, `bm25_stats.json`, CSV tables and `REPORT.md` |
| **4. Depth & volume** | Two engines behind one contract, exact parity verification against the single-machine code, streaming auto-updates, a Docker multi-node cluster, 34 tests, and an honest cost/benefit analysis (§11) |

---

## 2. The problem: why this system needed Big Data

The original FinPortfolio IR pipeline is a **single-machine, in-memory** program:

- `finportfolio_ir.io_utils.read_jsonl` loads an **entire** corpus into a Python
  list before anything happens.
- `indexing/build_sparse_index.py` builds the BM25 index in **one process**.
- `indexing/build_search_index.py` builds the SQLite FTS "database" serially.

That is fine for the 24-document sample, but the collected corpus is not small:

| Data | Size |
| --- | ---: |
| `data/` total | ~13 GB |
| `data/raw_documents/` | ~4.5 GB |
| SEC section corpus (`..._sections_documents.jsonl`) | ~294 MB |
| Combined SEC+macro+company IR corpus | ~352 MB |
| SQLite search index | ~811 MB |
| Official macro corpus (the demo corpus below) | ~39 MB / 18,240 docs |

Loading a 350 MB JSONL corpus into a Python list and indexing it in one process
is exactly the memory-bound, single-core bottleneck that a Big Data engine
removes by **partitioning** the work and running map/reduce stages in parallel
(and, on a cluster, across machines).

---

## 3. Selection: why Apache Spark

| Option | Verdict for this project |
| --- | --- |
| **Hadoop MapReduce (raw)** | The canonical model, but Java-first, high boilerplate, disk-heavy between stages. Our jobs *are* MapReduce — we want that model without the Java/HDFS ceremony. |
| **Apache Storm** | Tuple-at-a-time stream processor, Java/Clojure-first, awkward on Windows. Overkill for periodic evidence updates; poor fit for batch index/analytics. |
| **Dask** | Pythonic and pleasant, but not a canonical "Big Data framework" for the course, and weaker as a portable teaching artifact of the MapReduce model. |
| **Apache Spark** ✅ | Python-native (PySpark), expresses **MapReduce** directly (`map`/`flatMap`/`reduceByKey`/`groupByKey`), adds **SQL** and **Structured Streaming**, scales from a laptop (`local[*]`) to a multi-node cluster **unchanged**, and is the industry-standard choice. |

**Spark is the selected technology.** To make the deliverable *portable and
verifiable* on any machine (including a Windows box with no Spark/Java), the same
jobs also run on a small **pure-Python multiprocessing MapReduce engine** written
for this project. Both sit behind one interface, so selecting Spark did not cost
us the ability to run and test everywhere.

### Version note (reproducibility)

Verified with **PySpark 3.5.3 on a Java 8 JRE**. PySpark **4.0.0** was tried first
but hits a known Windows bug where the Python worker imports PySpark from
`pyspark.zip` and fails to read `error-conditions.json` (mixed `\`/`/`
separators). 3.5.x embeds its error classes in code and is unaffected. The Spark
engine auto-repairs `JAVA_HOME` (the machine's pointed at `...\jdk-21\bin`
instead of the JDK root) and prefers a Spark-3.5-compatible JVM (17/11/8) over
Java 21.

---

## 4. Architecture

```
                         ┌──────────────────────────────────────────┐
   data/*.jsonl  ──────► │  bigdata.engine  (one Dataset interface)  │
   (corpora)            └───────────────┬───────────────┬──────────┘
                                        │               │
                          LocalEngine (multiprocessing) │  SparkEngine (PySpark RDD)
                          pure-Python MapReduce          │  local[*] or spark://cluster
                                        │               │
                    ┌───────────────────┴───────────────┴───────────────────┐
                    │                    bigdata.jobs                         │
                    │   inverted_index  (BM25 stats, postings, query)         │
                    │   corpus_analytics (per family/type/tier/year/ticker…)  │
                    └───────────────────┬───────────────┬───────────────────┘
                                        │               │
                     batch runners (run_*.py)      streaming/ (auto-updates)
                     analytics.json · bm25_stats   incremental_update (poll)
                     document_frequencies.csv       spark_structured_streaming
                     REPORT.md · query_demo.json
```

**Key idea — one job, two engines.** Every job is written once against the
`bigdata.engine.Dataset` contract (a small subset of the Spark RDD API: `map`,
`flat_map`, `filter`, `map_partitions`, `reduce_by_key`, `group_by_key`,
`distinct`, `cache`, + actions). It runs unchanged on:

- **`SparkEngine`** — the graded Big Data framework; all compute distributed
  across Spark executors/cores (or, with the Docker cluster, worker nodes).
- **`LocalEngine`** — a dependency-free MapReduce engine (lazy narrow-op fusion,
  a map-side **combiner** in `reduce_by_key`, a process-stable shuffle
  partitioner, real multi-core via a process pool). This is the portable
  fallback **and** the correctness oracle.

All job callables are module-level (hence picklable), which is what lets the same
functions execute in Spark executors and in local worker processes.

---

## 5. The MapReduce computations

### 5.1 Inverted index + BM25 statistics ([`jobs/inverted_index.py`](../bigdata/jobs/inverted_index.py))

The distributed counterpart of `indexing/build_sparse_index.py`:

```
map:      JSONL line  → (doc_id, {term: tf}, length)          # parse + tokenise
map:      document     → (doc_id, length)                      # document lengths
flatMap:  document     → (term, 1)  for each distinct term      # df contributions
reduceByKey(+):        → (term, document_frequency)             # the inverted index
```

From these it derives `n_docs`, `vocabulary_size`, `average_document_length`, the
top terms by document frequency, and (optionally) the full `(term → postings)`
index via `groupByKey`. The **BM25 math** (idf + tf-saturation) is replicated so a
full query can also be scored distributed (`query_bm25`), matching
`BM25Index.score_query`.

Parsing reuses `FinancialDocument.from_dict` and the exact tokenizer
`finportfolio_ir.text_utils.tokenize`, and drops causally-unsafe records exactly
as `schema.load_documents` does — so the distributed corpus is identical to the
one the reference code builds.

### 5.2 Corpus analytics ([`jobs/corpus_analytics.py`](../bigdata/jobs/corpus_analytics.py))

A single MapReduce pass that fans each document into many additive counters and
sums them:

```
map:      JSONL line → compact metadata dict                    # parse + tokenise
flatMap:  metadata    → (metric_key, value) …                    # emit counters
reduceByKey(+):       → (metric_key, total)                      # aggregate
```

`metric_key` is namespaced (`family\t…`, `year\t…`, `ticker\t…`, `event\t…`,
`risk\t…`, `tokens\ttotal`, `credsum\t…`, …). The point-in-time coverage window is
two extra `reduce` actions (min/max of `available_at`). Because every metric is
additive, streaming micro-batches merge losslessly (§9).

---

## 6. How to run

```powershell
# Full pipeline (analytics + index + a distributed BM25 query) → REPORT.md
python -m bigdata.run_all --corpus macro --query "inflation interest rates"

# Individual jobs
python -m bigdata.run_inverted_index  --corpus macro --with-postings
python -m bigdata.run_corpus_analytics --corpus sec300

# Choose the engine explicitly (default: auto → Spark if installed, else local)
python -m bigdata.run_all --corpus macro --engine spark --master "local[*]"
python -m bigdata.run_all --corpus macro --engine local --num-workers 12

# Quick smoke on any corpus
python -m bigdata.run_all --corpus all_ppo --limit 5000
```

Named corpora (`bigdata/config.py`): `sample`, `sec300`, `macro`, `sec_ppo`,
`sec_sections`, `all_ppo`; or pass any processed-JSONL path to `--corpus`.
Artifacts are written under `data/exports/bigdata/`.

---

## 7. Results & practical value

Measured on the **official macro corpus** — **18,240 documents, ~39 MB** —
`python -m bigdata.run_all --corpus macro`:

| Metric | Value |
| --- | ---: |
| Documents | 18,240 |
| Vocabulary (distinct terms) | 5,520 |
| Total tokens | 1,173,755 |
| Average document length | 64.35 tokens |
| Point-in-time window | 2010-01-05 → 2026-05-12 |
| Avg source credibility (official_macro) | 0.90 |

**What the evidence base actually contains** (top aggregates):

- **Themes (event tags):** `rates` / `rates_policy` (6,743 docs), `market_volatility`
  (6,638), `energy` (6,614), `yield_curve` (6,584), `credit` / `credit_stress`
  (4,076). The macro corpus is dominated by rates and volatility evidence.
- **Risk terms:** `interest rates` (6,743), `inflation` (3,466), `Fed policy`
  (3,451), `equity risk` / `risk appetite` / `volatility` (3,319 each).
- **Coverage over time:** ~1,320 documents/year, flat across 2010–2022, then
  tapering (2023 onward is partial) — an at-a-glance data-completeness check.

This is the "practical value" of the collected data: a portfolio-aware view of
*what kinds of evidence exist, from which sources, over which period, and about
which risks* — computed over the whole corpus, not a sample. The same jobs run
on the 294 MB SEC section corpus and the 352 MB combined corpus.

### Performance (12-core machine)

| Job (macro, 18,240 docs) | Local engine | Spark `local[*]` |
| --- | ---: | ---: |
| Inverted index (compute) | **0.86 s** | 60.1 s (+ ~15 s startup) |
| Full `run_all` (3 passes) | ~3.1 s | ~3 min |

See §11 for why the local engine is *faster* here — and where Spark wins.

---

## 8. Verification

The central guarantee: **the distributed output is identical to the trusted
single-machine code.**

- **Exact parity vs `indexing.build_sparse_index.BM25Index`** on the sample
  corpus: `document_frequencies`, `document_lengths`, `average_document_length`,
  and `n_docs` all equal; distributed BM25 query scores match
  `BM25Index.score_query` to 1e-9.
- **Spark == Local == reference.** On the full 18,240-document macro corpus the
  Spark and local `document_frequencies.csv` (5,520 terms) are **byte-identical**,
  and both report `avgdl = 64.3506`, `vocab = 5,520`.
- **36 tests** in `tests/test_bigdata_*.py` (engine ops, index parity, query
  parity, postings/df consistency, source-family parity vs the search index,
  analytics, incremental-merge == full-batch, append-safe streaming, and a
  real-Spark parity suite that self-skips when Java/PySpark is absent) — part of a
  **221-test** full suite that stays green:

  ```powershell
  python -m unittest discover -s tests   # includes the Big Data suite
  ```

---

## 9. Automatic updates (streaming)

Two implementations of the "automatic updates" requirement, both aggregating the
corpus incrementally as fresh evidence arrives:

- **[`incremental_update.py`](../bigdata/streaming/incremental_update.py)** — a
  poll-based micro-batch updater on the shared engine (Spark or local). It watches
  an inbox directory and, via **per-file byte-offset tracking**, reads only the
  bytes added since last tick — so both brand-new files **and appends to an
  existing live file** contribute each line exactly once, then merge additively
  into append-only state. No Hadoop checkpoint, so it is fully runnable and
  verified on Windows, and it reuses the batch analytics code so its report is
  byte-identical to the batch job. Wire it to the existing crawler:
  `live_incremental_fetch.py` appends new records to the live JSONL; a scheduled
  task runs `python -m bigdata.streaming.incremental_update --interval 300`.
  *Verified:* seed 10 docs → 10; re-run with no new bytes → still 10 (idempotent);
  **append** 14 docs to the same file → 24 (no double-count); streamed report ==
  batch report.
- **[`spark_structured_streaming.py`](../bigdata/streaming/spark_structured_streaming.py)**
  — a genuine Spark Structured Streaming job (file source → windowed aggregation
  → `foreachBatch`). Its file source uses Hadoop **native IO**
  (`NativeIO$Windows.access0`), so on Windows it needs `winutils.exe` /
  `HADOOP_HOME` and otherwise raises `UnsatisfiedLinkError` (confirmed on the
  reference box); it runs cleanly on the Docker cluster / Linux. The batch RDD
  jobs avoid this by reading driver-side and `collect`-ing.

---

## 10. Distributed deployment (Docker cluster)

[`deploy/spark_cluster/`](../deploy/spark_cluster) defines a standalone Spark
cluster (1 master + 2 workers) so the `engine=spark` path runs across real worker
nodes:

```bash
docker compose -f deploy/spark_cluster/docker-compose.yml up -d   # master + 2 workers
#   Master UI → http://localhost:8080
deploy/spark_cluster/submit.sh bigdata.run_inverted_index --corpus macro
```

The compose file is **config-validated** (`docker compose config` passes). The
multi-node cluster was **not brought up in the reference session** because the
Docker Desktop Linux engine was not running there; the genuinely-distributed Spark
execution reported above used `local[*]` (multiple executors across 12 cores),
which exercises the same distributed code paths. The engine already skips the
loopback driver binding for `spark://` masters so it works unchanged on the
cluster.

---

## 11. Honest analysis: when does Big Data pay off?

The performance table (§7) is deliberately not spun. On this single 12-core
machine over an 18k-document / 39 MB corpus, the **local multiprocessing engine
is ~70× faster** than Spark. That is expected and worth understanding:

- **Spark's cost** is fixed overhead: JVM startup (~10–15 s), task scheduling, and
  serialising every record across the Python↔JVM boundary (twice) — punishing on
  Windows, which has no `fork`.
- **Spark's benefit** is horizontal scale: partitioned, out-of-core execution
  across *many machines*. It dominates when the corpus no longer fits in one
  machine's RAM or when a cluster's aggregate cores/IO exceed one box — i.e. the
  352 MB combined corpus, the full 4.5 GB raw set, and beyond.

So the engineering choice is not "Spark always." It is: **a single MapReduce
codebase that runs locally for fast iteration and correctness, and on Spark when
data outgrows one machine** — which is exactly what the two-engine design gives.

---

## 12. Limitations & future work

- **Driver-side collection.** Batch outputs are `collect`-ed to the driver and
  written with Python (to avoid the Hadoop output committer / `winutils.exe` on
  Windows). For truly cluster-scale postings, switch the writers to
  `df.write.parquet` on the Docker cluster / HDFS.
- **Duplicate `doc_id`s.** Parity is exact for unique `doc_id`s (the norm). With
  duplicate ids the distributed index counts each JSONL record as a document
  (correct BM25 semantics); the single-machine code's last-wins dict can differ
  marginally.
- **Next steps.** Parquet columnar artifacts on the cluster; a Spark SQL view over
  the evidence ledger; and feeding the analytics coverage window back into the
  point-in-time retrieval firewall.
