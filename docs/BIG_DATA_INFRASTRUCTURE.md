# Big Data Infrastructure

## 1. Course requirement mapping

| Course requirement | Where it is delivered |
| --- | --- |
| **1. Data gathering** with automatic updates | Pre-existing crawler (`crawler/`, `deploy/update_live_ir.py`, `crawler/live_incremental_fetch.py`) **+** new streaming auto-updater ([`bigdata/streaming/`](../bigdata/streaming)) |
| **2. Selection & implementation of a Big Data technology** | **Apache Spark** (RDD MapReduce + Structured Streaming), implemented in [`bigdata/`](../bigdata), with a portable pure-Python MapReduce engine behind one shared interface |
| **3. Processing & analysis of the collected data** | Distributed **inverted index / BM25** build + **corpus analytics** (producing `analytics.json`, `bm25_stats.json`, CSV tables, `REPORT.md`) **+ the production SQLite FTS index served by `web_app.py` is built by the layer** (`bigdata/run_build_search_index.py`, §5.2) |
| **4. Depth & volume** | Two engines behind one contract, exact parity verification against the single-machine code, streaming auto-updates, a Docker multi-node cluster **with recorded full-corpus runs** (§10), 41 Big Data tests, and an honest cost/benefit analysis (§11) |

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

### 5.2 The production search index ([`jobs/search_index.py`](../bigdata/jobs/search_index.py))

The SQLite FTS index that `web_app.py` serves search from is **built by the Big
Data layer** (`python -m bigdata.run_build_search_index`). The corpus-scale,
per-document work — parsing every record of the 352 MB combined corpus,
deriving `source_family`/lengths, serialising the canonical `record_json`, and
preparing the FTS payloads — is a distributed `map` over Spark executors; the
driver then assembles the SQLite artifact (single-writer sink, exactly like any
Spark job that terminates in a non-parallel store). The output is **drop-in
identical** to `indexing/build_search_index.py`: every table (`documents`,
`documents_fts`, `document_features`, `source_quality`, `ticker_coverage`)
matches the original builder row-for-row — asserted by
`tests/test_bigdata_search_index.py` for both engines.

### 5.3 Corpus analytics ([`jobs/corpus_analytics.py`](../bigdata/jobs/corpus_analytics.py))

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

# Build the production SQLite FTS index (what web_app.py serves) via the layer
python -m bigdata.run_build_search_index --corpus all_ppo --engine spark

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

### Performance

**Headline result — the full 352 MB corpus (26,368 documents).** This is the run
that matters, and it reverses the small-corpus conclusion:

| Engine / platform | Inverted index over 352 MB | Cores |
| --- | ---: | ---: |
| Spark, **Docker cluster** (2 workers × 2 cores, Linux) | **18.5 s** | 4 |
| Local MapReduce engine (Windows) | 23.5 s | 12 |
| Spark `local[*]` (Windows) | 64–71 s | 12 |

On real data volume the cluster is the fastest option **while using 3× fewer
cores**, and Spark's per-task overhead — which dominated the tiny 39 MB corpus —
is amortised away. The often-quoted "the local engine is 70× faster than Spark"
holds *only* for the 39 MB toy corpus; at 352 MB that same Windows comparison
shrinks to 2.8×, and the cluster beats the local engine outright.

All five figures above were re-measured on an idle machine. An earlier set of
numbers for this table (44 s / 81–105 s) was discarded: a previous benchmark
process had survived its wrapper and was competing for CPU, inflating every
measurement by 1.3–1.9×. Two lessons, both worth stating: a benchmark script
that uses `multiprocessing` on Windows **must** guard its entry point with
`if __name__ == "__main__":` (spawn re-imports `__main__`, so an unguarded
script re-runs itself in every worker), and the first timing of a Spark session
is warm-up (26.8 s vs 18.5 s here) and must be discarded.

| Job | Local engine (12 cores, Windows) | Spark `local[*]` (Windows) | Spark cluster (Docker, 2 workers × 2 cores) |
| --- | ---: | ---: | ---: |
| Inverted index, macro (18,240 docs) | **0.86 s** | 60.1 s | **3.45 s** |
| Corpus analytics, macro | ~1.1 s | ~60 s | **10.9 s** |
| Inverted index, full corpus (26,368 docs, 352 MB) | — | — | **17.8 s** |
| Corpus analytics, full corpus | — | — | **20.3 s** |
| Production SQLite index build, full corpus | — | — | **~73 s** (24.8 s distributed map + 47.8 s SQLite write) |

Two lessons worth stating explicitly (см. §11): the notorious 60-second
Windows `local[*]` number was mostly *Windows-specific* overhead (no `fork`,
Python-worker spawn, py4j on loopback) — the same job on the Linux cluster
runs in 3.5 s with a third of the cores; and the full 352 MB corpus is
processed end-to-end on the toy 4-core cluster in tens of seconds.

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
- **The production search index is the strongest parity check.** The index
  built **on the Spark cluster** over the full 352 MB corpus was compared
  table-by-table against the index built by the original single-machine
  `indexing/build_search_index.py`: `documents` (26,368 rows), `documents_fts`
  (26,368), `document_features` (3,456) and `ticker_coverage` (30) are
  **row-for-row identical**; `source_quality` (10 rows) matches on every count,
  with the two `AVG` columns differing by ≤ 2.5 × 10⁻¹³ — floating-point noise
  from the container's newer SQLite (compensated summation in `AVG`), not a
  logic difference. That cluster-built index **now serves the site**
  (`data/search_index/finportfolio_search.sqlite`; the previous one is kept as
  `finportfolio_search_pre_spark_backup.sqlite`), and the app's own 49 tests
  (`test_search_index`, `test_web_app`) pass against it.
- **41 tests** in `tests/test_bigdata_*.py` (engine ops, index parity, query
  parity, postings/df consistency, source-family parity vs the search index,
  analytics, incremental-merge == full-batch, append-safe streaming, search-index
  builder parity for both engines, and a real-Spark parity suite that self-skips
  when Java/PySpark is absent) — the full suite stays green:

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

The cluster **was brought up and used for the headline runs** (Docker official
`spark:3.5.3` image; `bitnami/spark` no longer exists on Docker Hub). Evidence
captured from the live UIs during the runs:

- **2 workers ALIVE** (2 cores / 2 GB each), applications running and completed
  on the master — [`assets/spark_cluster_master.png`](assets/spark_cluster_master.png);
- distributed **map / reduceByKey / collect stages, 16/16 tasks with shuffle
  read/write** over 337–401 MiB inputs —
  [`assets/spark_cluster_stages.png`](assets/spark_cluster_stages.png);
- executors on both worker nodes —
  [`assets/spark_cluster_executors.png`](assets/spark_cluster_executors.png).

Cluster runs performed: the **production SQLite index build over the full
352 MB / 26,368-document corpus** (~73 s end-to-end; the resulting index now
*serves the site* — see §5.2 and §8), plus `run_all` analytics over both the
macro corpus and the full combined corpus (timings in §7). Reads used
`--native-read` (`sc.textFile`), i.e. genuinely distributed IO. The engine
skips the loopback driver binding for `spark://` masters, so the same code
runs unchanged on the cluster.

---

## 11. Honest analysis: when does Big Data pay off?

The performance table (§7) is deliberately not spun. On the Windows laptop the
local multiprocessing engine beat Spark `local[*]` by ~70× — and the cluster
runs then showed **most of that gap was Windows-specific**, not intrinsic to
Spark:

- **Measured, not guessed.** Two controlled experiments on the macro corpus
  (18,240 docs) isolate the cause:

  | Experiment | Result | Conclusion |
  | --- | --- | --- |
  | Read path: driver-read+`parallelize` vs `sc.textFile` (Windows `local[*]`, 12 parts) | 65.9 s vs 63.4 s | the IO path is **irrelevant** |
  | Partition count, **Windows `local[*]`, 12 cores** | 2 → 12.5 s, 4 → 21.5 s, 12 → **64.0 s** | time *grows* with parallelism → fixed per-task cost dominates |
  | Partition count, **Linux cluster, 4 cores** (same code, same corpus) | 2 → 7.4 s, 4 → **3.9 s**, 12 → 4.2 s | time *falls* then flattens → per-task cost is negligible |

  The two platforms behave in *opposite* directions on identical data and
  identical code, which is the whole diagnosis: on Windows more parallelism buys
  nothing and costs ~5.3 s per extra task, while on Linux parallelism does what
  it should (7.4 s → 3.9 s from 2 to 4 partitions, then flat once the 4 cluster
  cores are saturated). A cost that grows with *task count* rather than data
  volume is a fixed per-task cost — not the algorithm, and not the network
  (there is no network in `local[*]` at all). On Windows PySpark
  cannot `fork`: it spawns a fresh `python.exe` per task, each re-importing
  PySpark and the project modules. On Linux, workers are forked from a pooled
  daemon at near-zero cost — which is why the *same code with the same read
  path* takes **3.45 s on the 4-core Linux cluster** versus 63 s on a 12-core
  Windows box.
- **Where the cluster optimum actually is.** A wider sweep on the 4-core cluster
  (`sc.textFile`, same job) shows over-partitioning costs on Linux too — just far
  more gently than on Windows:

  | partitions | macro (39 MB) | | partitions | all_ppo (352 MB) |
  | ---: | ---: | --- | ---: | ---: |
  | 2 | 4.2 s | | 11 | 18.1 s |
  | **4** | **2.7 s** | | **16** | **18.3 s** |
  | 8 | 3.1 s | | 32 | 22.3 s |
  | 16 | 4.4 s | | 64 | 27.9 s |
  | 64 | 14.0 s | | | |

  The optimum sits at roughly **one partition per core** (4 for the 4-core
  cluster), and 64 partitions costs 5× the optimum. Two further facts fell out of
  the sweep: `minPartitions` is a **floor, not an exact count** — asking for 1, 2,
  4 or 8 on the 352 MB corpus all yielded **11** partitions, because Hadoop's
  32 MB split size decides (352/32 ≈ 11); and the very first run of a session is
  ~1.8× slower (7.6 s vs 4.2 s for an identical config) from JVM/JIT warm-up, so
  benchmarks must discard the first measurement.
- **Windows vs cluster, same RDD job** (macro corpus, 18,240 docs; all figures
  re-measured on an idle machine, warm-up run discarded):

  | Partitions | PySpark on **Windows** (12 cores) | PySpark on **Docker cluster** (4 cores) | Cluster advantage |
  | ---: | ---: | ---: | ---: |
  | 2 | 11.4 s | 2.3 s | 5.0× |
  | 4 | 21.6 s | 2.2 s | 9.8× |
  | 12 | 66.8 s | 4.0 s | **16.7×** |

  The cluster's advantage *grows with the partition count* — because every extra
  partition is an extra task, and on Windows every task costs a fresh
  `python.exe`. On Linux that cost is a `fork` and is effectively free.

- **What actually fixes the Windows path** — four remedies, measured against the
  same baseline (Windows, RDD, 12 partitions = 66.8 s). None of them involves
  writing an engine:

  | Remedy | Time | Speed-up |
  | --- | ---: | ---: |
  | baseline — Windows, RDD+Python, 12 partitions | 66.8 s | 1× |
  | `spark.python.worker.reuse=false` (vs default `true`) | 64.1 s | **1.0× — no effect** |
  | fewer partitions: 12 → 4 | 21.6 s | 3.1× |
  | fewer partitions: 12 → 2 | 11.4 s | 5.9× |
  | same RDD code on the **Docker cluster**, 12 partitions | 4.0 s | 16.7× |
  | same RDD code on the **Docker cluster**, 4 partitions | 2.2 s | **30×** |
  | **DataFrame/SQL API on Windows**, 12 partitions | 2.3 s | 29× |
  | **DataFrame/SQL API on Windows**, 4 partitions | 1.2 s | **56×** |
  | DataFrame/SQL API on the cluster, 4 partitions | 1.9 s | 35× |

  Three things stand out. First, the knob everyone suggests —
  `spark.python.worker.reuse` — does **nothing** here (64.1 s vs 66.8 s, within
  noise): it optimises the daemon/`fork` path, which does not exist on Windows.
  Second, the DataFrame/SQL rewrite is the strongest remedy *and it is fastest on
  Windows*, beating the cluster (1.2 s vs 1.9 s) — because with zero Python
  workers Windows' only weakness disappears and the 12-core laptop simply has
  more cores than the 4-core toy cluster. That is the cleanest proof that the
  bottleneck was never "Windows is slow", it was specifically Python-worker
  spawn. Third, the SQL path produces a different vocabulary (18,152 vs 5,520)
  because its regex tokeniser is not the project tokeniser — it demonstrates the
  mechanism, not parity. Keeping `finportfolio_ir.text_utils.tokenize` verbatim
  is exactly what makes the byte-identical parity guarantee possible, which is
  why the RDD path is the one that ships.

- **Practical consequence:** on Windows use *fewer* partitions (`--partitions 2`
  cut this job from 64 s to 12.5 s); on a cluster, partition for the cores you
  actually have.
- **Spark's benefit** is horizontal scale: partitioned, distributed-IO execution
  across machines. The full 352 MB corpus is processed in tens of seconds on a
  toy 2-worker cluster, and the same code scales by adding workers — which no
  single-process design can do.
- The **local engine still wins on small corpora from a warm interpreter**
  (0.86 s) — the right tool for fast iteration and as the correctness oracle.

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
