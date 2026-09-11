# Heavy-load benchmark on a 6 x 2 Spark cluster (12 executor cores)

Companion to the "Isn't this just measuring worker start-up?" bullet in
[`BIG_DATA_INFRASTRUCTURE.md`](BIG_DATA_INFRASTRUCTURE.md). That bullet compares
**Windows, 12 threads** against a **4-core (2 x 2) Docker cluster**. This note adds
the missing row — a **12-core (6 x 2) cluster** — and replaces the *modelled*
start-up column with a *measured* one.

Run date: 2026-09-09. Host: AMD Ryzen 5 5500U, **6 physical cores / 12 logical
threads**, 15.3 GB RAM, Windows 11. WSL2 capped at 11 GB / 12 processors via
`~/.wslconfig`. Docker Desktop 4.37.1, image `spark:3.5.3-scala2.12-java17-python3-ubuntu`.

---

## 1. The corpus is a reconstruction, not the original

The original 376 MiB heavy-load corpus (`/extra/sec_raw_large.jsonl`, visible only
inside the three archived event logs) **is not on disk any more** and was never
checked in, exactly as `BIG_DATA_INFRASTRUCTURE.md` states. It was therefore
rebuilt from the surviving raw HTML cache with the repo's own whole-filing
cleaner, `crawler.sec_section_parser.html_to_sec_text`:

```
data/raw_documents/sec_full_html_cache/*.html          1,904 filings
data/raw_documents/sec_full_html_cache/exhibits/*      1,125 exhibits
                                                       -----
                                                       3,029 documents
```

| | documents | size | tokens | distinct terms | avg doc length |
| --- | ---: | ---: | ---: | ---: | ---: |
| original (documented) | 3,026 | 376 MiB | 59,660,408 | 90,888 | 19,716 |
| **rebuild (this run)** | **3,029** | **364.0 MiB** | **57,185,743** | **82,700** | **18,879** |
| delta | +0.1% | -3.2% | -4.1% | -9.0% | -4.2% |

Close, but **not byte-identical** — most likely because the original records
carried a `title` / entity fields that this rebuild leaves empty, which
`FinancialDocument.text_for_indexing()` concatenates into the indexed text
(that also explains why the vocabulary gap, -9.0%, is the largest one).

**Consequence:** the original table's numbers cannot be extended directly. So all
three configurations were re-measured on the rebuilt corpus, and the table below
is internally consistent by construction rather than comparable to the old one.

Corpus location: `C:/tmp/finportfolio_bigdata/sec_full_text_heavy_documents.jsonl`
(ASCII path — Hadoop's local reader mishandles the project's Cyrillic directory).
Rebuild script: `scratchpad/build_heavy_corpus.py`.

## 2. Method

Job, flags and timer are unchanged from the original experiment:

```powershell
# cluster
docker compose -f deploy/spark_cluster/docker-compose.6x2.yml exec -T `
  -e PYSPARK_SUBMIT_ARGS="--driver-memory 1g pyspark-shell" spark-master `
  python3 -m bigdata.run_inverted_index --engine spark `
    --master spark://spark-master:7077 `
    --corpus /extra/sec_full_text_heavy_documents.jsonl --native-read --partitions 12

# Windows
$env:PYSPARK_SUBMIT_ARGS = "--driver-memory 4g pyspark-shell"
C:/Users/ivanp/anaconda3/envs/tensorflow/python.exe -m bigdata.run_inverted_index `
  --engine spark --master "local[12]" `
  --corpus C:/tmp/finportfolio_bigdata/sec_full_text_heavy_documents.jsonl `
  --native-read --partitions 12
```

- **Reported time = `bm25_stats.json -> run.seconds`**, the `Timer` around
  `build_bm25_index` — the same quantity the original table reports.
- Warm-up run discarded; **3 measured runs per configuration**; median quoted.
- Every configuration executed **4 stages x 12 tasks = 48 tasks**, confirmed in the
  event logs, and produced identical output (3,029 docs / 82,700 terms /
  57,185,743 tokens) — so the configurations differ only in wall clock.
- On Windows `FINPORTFOLIO_SPARK_EVENTLOG_DIR` must stay **unset**: it would be a
  Cyrillic path and Spark dies in `WinNTFileSystem.canonicalize0` before the
  context starts. This is why the Windows column has never had event logs.

### Start-up is measured, not modelled

The original table's start-up column was a *model* carried over from the 39 MB
corpus (48 tasks x ~1.25 s = ~60 s). Here it is measured directly: the identical
job is run with `--limit 12 --partitions 12`, which keeps the **same 48-task
skeleton** but gives each task ~nothing to do. Whatever that costs is fixed
overhead.

## 3. Results

All figures: heavy corpus, 12 partitions, median of 3 runs, warm-up discarded.

| | wall clock *(measured)* | start-up *(measured, 48-task skeleton)* | residual *(compute)* |
| --- | ---: | ---: | ---: |
| **Windows, 12 threads** (`local[12]`) | **112.3 s** | **107.2 s** | **~5 s** |
| **Cluster, 4 cores (2 x 2)** | **41.9 s** | **12.6 s** | **~29 s** on 4 cores |
| **Cluster, 12 cores (6 x 2)** | **39.2 s** | **17.8 s** | **~21 s** on 12 cores |

Raw runs (job seconds):

| configuration | run 1 | run 2 | run 3 | median |
| --- | ---: | ---: | ---: | ---: |
| Windows `local[12]`, full corpus | 112.278 | 111.703 | 112.929 | 112.3 |
| Windows `local[12]`, 48-task skeleton | 106.404 | 107.505 | 107.205 | 107.2 |
| Cluster 2 x 2, full corpus (ext4 volume) | 41.920 | 41.366 | 43.294 | 41.9 |
| Cluster 2 x 2, full corpus (Windows bind mount) | 44.357 | 43.771 | 42.874 | 43.8 |
| Cluster 2 x 2, 48-task skeleton | 12.600 | 12.728 | 11.809 | 12.6 |
| Cluster 6 x 2, full corpus (ext4 volume) | 39.718 | 37.908 | 39.157 | 39.2 |
| Cluster 6 x 2, full corpus (Windows bind mount) | 38.513 | 41.106 | 40.741 | 40.7 |
| Cluster 6 x 2, 48-task skeleton | 18.477 | 17.780 | 17.846 | 17.8 |

### The residual column is independently confirmed

`residual = wall - start-up` is a subtraction. The Spark event logs give the same
quantity a second way — aggregate `Executor Run Time` for stage 0 (the only stage
that reads and tokenises the corpus), divided by the core count:

| | stage-0 core-seconds | / cores | residual by subtraction | agree? |
| --- | ---: | ---: | ---: | :---: |
| Cluster 2 x 2 (4 cores) | 121.2 | **30.3 s** | 29.3 s | yes |
| Cluster 6 x 2 (12 cores) | 254.3 | **21.2 s** | 21.4 s | yes |

Two independent methods, agreement within 1 s.

## 4. What the 6 x 2 row actually shows

**Tripling the cluster (4 -> 12 cores) buys 6.4% of wall clock**, 41.9 s -> 39.2 s.
Not 3x, not even 1.5x. Two effects cancel most of the gain:

1. **The extra "cores" are SMT threads, not cores.** The same job costs
   **121.2 core-seconds on 4 cores but 254.3 on 12** — 2.1x more CPU for identical
   work and identical output. The host has 6 physical cores; the 4-core cluster
   nearly saturates them productively, while the 12-core cluster runs 6 executor
   JVMs and 12 Python workers over the same 6 cores (which also still run Windows,
   the driver and the master). Per-core productivity roughly halves. Net compute
   gain: 30.3 s -> 21.2 s, i.e. **1.43x from 3x the cores**.
2. **Cluster start-up grows with worker count**: 12.6 s at 2 workers -> 17.8 s at 6.
   More containers means more executor registration, more broadcast targets, more
   scheduling round trips. That +5.2 s eats over half of the 9.1 s compute win.

### It is not I/O

The obvious suspicion — that the cluster is throttled reading 364 MiB across the
Windows bind mount (`C:/tmp` -> `/extra`) — was tested directly by copying the
corpus onto a native ext4 Docker volume (`/fast`) inside the WSL VM and re-running:

| | Windows bind mount | native ext4 volume |
| --- | ---: | ---: |
| Cluster 6 x 2 | 40.7 s | 39.2 s |
| Cluster 2 x 2 | 43.8 s | 41.9 s |

A ~1.9 s difference, far too small to explain anything. The bottleneck is
physical cores, not the mount.

## 5. Correction to the original start-up model

Measured start-up on Windows is **107.2 s for 48 tasks = 2.23 s/task**, not the
~1.25 s/task the original bullet modelled. The component measurements move the
same way on this machine: `import pyspark` costs **0.92 s** here against the 0.61 s
the original bullet recorded.

The consequence is bigger than a factor: **start-up is 95% of the Windows wall
clock** (107.2 s of 112.3 s), not 67%. The additive decomposition
`wall = start-up + compute` therefore breaks down on Windows — a ~5 s residual
cannot be the real cost of tokenising 57 M tokens, and the cluster proves it is
not (the same work is 121-254 core-seconds). What actually happens is what the
original bullet already argued: Spark spawns Python workers **serially**, so the
12 cores sit idle during the spawn chain and the real compute hides inside that
idle time, surfacing as only ~5 s at the margin.

So for the Windows row, read the residual as *"compute is not separately
observable"*, not as *"compute takes 5 s"*.

## 6. Headline comparison

| | wall clock | vs Windows |
| --- | ---: | ---: |
| Windows, 12 threads | 112.3 s | 1.0x |
| Cluster, 4 cores (2 x 2) | 41.9 s | **2.68x** |
| Cluster, 12 cores (6 x 2) | 39.2 s | **2.86x** |

The cluster's advantage is still what the original bullet said it was: it is
**not computing faster** — on compute alone the 4-core cluster does the job in
30.3 s of core time where 12 Windows threads need a spawn chain three times
longer than the whole job. The cluster wins because Linux forks Python workers
and Windows does not. Adding 8 more cores to the cluster adds almost nothing,
because the host has no more physical cores to give.

## 7. Reproducing

```powershell
# 1. rebuild the corpus (~4 min, 10 processes)
python scratchpad/build_heavy_corpus.py `
  --project-root . `
  --cache-dir data/raw_documents/sec_full_html_cache `
  --output C:/tmp/finportfolio_bigdata/sec_full_text_heavy_documents.jsonl --workers 10

# 2. bring up the 12-core cluster
$env:FINPORTFOLIO_EXTRA_DIR = "C:/tmp/finportfolio_bigdata"
docker compose -f deploy/spark_cluster/docker-compose.6x2.yml up -d
# master UI http://localhost:8080 -> "Alive Workers: 6", "Cores in use: 12 Total"

# 3. run (discard the first)
docker compose -f deploy/spark_cluster/docker-compose.6x2.yml exec -T spark-master `
  python3 -m bigdata.run_inverted_index --engine spark `
    --master spark://spark-master:7077 `
    --corpus /extra/sec_full_text_heavy_documents.jsonl --native-read --partitions 12
```

`deploy/spark_cluster/docker-compose.6x2.yml` is new and additive; the original
2-worker `docker-compose.yml` is unchanged apart from a shared `fastdata` volume
used by the I/O control in §4. Workers advertise `--cores 2 --memory 1g` (not the
2g of the 4-core file) and `SPARK_DAEMON_MEMORY=512m`, so six executor JVMs plus
twelve Python workers fit in the WSL2 budget.
