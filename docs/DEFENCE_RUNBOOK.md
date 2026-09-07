# Defence runbook — running the three PySpark paths

Copy-paste sequences for a live demo. Every command here has been executed as
written. Read [`BIG_DATA_INFRASTRUCTURE.md`](BIG_DATA_INFRASTRUCTURE.md) for the
numbers and the analysis behind them.

---

## 0. Prerequisites

**Use the wrapper and there is nothing to set up.** `deploy/run_spark.ps1` finds
an interpreter that can actually import PySpark and runs the job with it:

```powershell
cd "C:\Users\ivanp\OneDrive\Рабочий стол\доки+черчи\ITMO\2_sem\FinRL_Tsinghua\FinPortfolio_IR"
.\deploy\run_spark.ps1 bigdata.run_inverted_index --corpus macro --partitions 4
```

It prints which interpreter it picked, supplies `--engine spark --master local[*]`
unless you passed your own, and forwards everything else to the job. It is the
local counterpart of `deploy/spark_cluster/submit.ps1`.

### Why a wrapper is needed at all

PySpark is **not** in the default interpreter on this machine. Both `py` and
`python` resolve to Python 3.13, which has no pyspark, so this fails:

```powershell
py -m bigdata.run_inverted_index --corpus macro --engine spark    # EngineUnavailableError
```

The interpreter that has it is `anaconda3\envs\tensorflow\python.exe`
(PySpark 3.5.3 on Python 3.9). To call it by hand, two things both matter — the
variable must be defined **first**, and PowerShell needs the `&` call operator in
front of it, otherwise it tries to parse `-m` as an operator:

```powershell
$py = "C:\Users\ivanp\anaconda3\envs\tensorflow\python.exe"
& $py -m bigdata.run_inverted_index --corpus macro --engine spark --master "local[*]" --partitions 12
```

Without the `&` you get *Непредвиденная лексема "-m"* / *Unexpected token '-m'*.
The wrapper exists so neither detail has to be remembered mid-demo. To point it
at a different interpreter:

```powershell
$env:FINPORTFOLIO_PYTHON = "D:\envs\spark\python.exe"
```

Spark 3.5 needs a **Java 8/11/17** runtime. `SparkEngine` finds and prefers one
automatically (`bigdata/engine/spark_engine.py:resolve_java_home`), including
repairing a `JAVA_HOME` that points at `...\bin` instead of the JDK root — so
normally nothing to do. To force one:

```powershell
$env:FINPORTFOLIO_SPARK_JAVA_HOME = "C:\Program Files\Java\jdk-17"
```

Optional, but worth turning on before a demo — it makes finished jobs
inspectable in the History Server afterwards:

```powershell
$env:FINPORTFOLIO_SPARK_EVENTLOG_DIR = "$PWD\data\spark-events"
```

## 1. PySpark on Windows — RDD path (`local[*]`)

This is the production path: it reuses `finportfolio_ir.text_utils.tokenize`, so
its output is the one verified byte-identical to the single-machine reference.

```powershell
# baseline: 12 partitions - deliberately the slow configuration
.\deploy\run_spark.ps1 bigdata.run_inverted_index --corpus macro --partitions 12

# the same job, fewer partitions - this is the cheap Windows remedy
.\deploy\run_spark.ps1 bigdata.run_inverted_index --corpus macro --partitions 4
.\deploy\run_spark.ps1 bigdata.run_inverted_index --corpus macro --partitions 2
```

Each run prints a JSON summary and writes artifacts to
`data/exports/bigdata/inverted_index/`. The wall-clock of the job itself (not of
the JVM start-up) is in `bm25_stats.json` under `run.seconds`:

```powershell
python -c "import json;d=json.load(open('data/exports/bigdata/inverted_index/bm25_stats.json',encoding='utf-8'));print(d['run']['seconds'],'s |',d['n_docs'],'docs |',d['vocabulary_size'],'terms')"
```

Expect **18,240 documents and a 5,520-term vocabulary** on the macro corpus —
these are the numbers the parity tests assert.

The whole pipeline (index + analytics + a scored query) in one go:

```powershell
.\deploy\run_spark.ps1 bigdata.run_all --corpus macro --partitions 4 --query "inflation interest rates"
```

To show that the portable engine agrees, swap one flag — no other change:

```powershell
python -m bigdata.run_inverted_index --corpus macro --engine local --partitions 4
```

---

## 2. PySpark on the Docker cluster

One master + two workers + a History Server, all defined in
`deploy/spark_cluster/docker-compose.yml`. The repository is bind-mounted at
`/workspace`, so the jobs and the corpora are visible inside the containers.

```powershell
# start it (takes ~15 s for the workers to register)
docker compose -f deploy/spark_cluster/docker-compose.yml up -d
docker ps --format "{{.Names}}`t{{.Status}}"
```

Open <http://localhost:8080> — both workers should be listed as ALIVE.

Submit through the PowerShell wrapper. It starts the cluster if it is down and
forwards everything after the module name to the job:

```powershell
.\deploy\spark_cluster\submit.ps1 bigdata.run_inverted_index --corpus macro --native-read --partitions 12
.\deploy\spark_cluster\submit.ps1 bigdata.run_inverted_index --corpus macro --native-read --partitions 4
```

`--native-read` makes Spark read the corpus itself with `sc.textFile` (a genuine
distributed read) instead of the driver-side reader the Windows path needs. Use
it on the cluster; the container paths are ASCII.

The full corpus (352 MB, 26,368 documents), and the production SQLite index built
by Spark:

```powershell
.\deploy\spark_cluster\submit.ps1 bigdata.run_all --corpus all_ppo --native-read --partitions 16
.\deploy\spark_cluster\submit.ps1 bigdata.run_build_search_index --corpus all_ppo --native-read `
    --output data/exports/bigdata/search_index/finportfolio_search_spark.sqlite
```

While a job runs, the driver UI is at <http://localhost:4040>. **It disappears
when the job ends** — for the DAG, the per-task event timeline and shuffle
read/write of a *finished* job use the History Server instead:

- <http://localhost:18080> → pick the application → **Stages** → click a stage →
  expand **DAG Visualization** and **Event Timeline**.

Applications are tagged `[CLUSTER]` or `[local(local[*])]` in that list, so the
two deployments are told apart at a glance.

Tear down when finished:

```powershell
docker compose -f deploy/spark_cluster/docker-compose.yml down
```

If `docker ps` fails with a named-pipe error, Docker Desktop is not running —
start it and wait for the engine before submitting.

---

## 3. DataFrame/SQL on Windows

The same aggregation expressed in Spark SQL. No Python worker is started at all,
which is what makes it fast on Windows.

```powershell
.\deploy\run_spark.ps1 bigdata.run_sql_inverted_index --corpus macro --partitions 4
.\deploy\run_spark.ps1 bigdata.run_sql_inverted_index --corpus macro --partitions 12
```

The first run of a session is JVM/JIT warm-up and is **discarded automatically**;
`--repeat 3` gives two measured runs instead of one:

```powershell
.\deploy\run_spark.ps1 bigdata.run_sql_inverted_index --corpus macro --partitions 4 --repeat 3
```

The same module runs on the cluster through the same wrapper:

```powershell
.\deploy\spark_cluster\submit.ps1 bigdata.run_sql_inverted_index --corpus macro --partitions 4
```

### What to say about the result

It reports an **18,158-term** vocabulary, not 5,520. That is not a bug and it is
worth volunteering before anyone asks: the SQL path tokenises with a SQL
expression rather than `finportfolio_ir.text_utils.tokenize`, and on the macro
corpus — where every document carries a unique date and value — a whitespace
split keeps `2010-01-01` and `217.587` whole. Splitting on punctuation instead
collapses the vocabulary to ~900:

```powershell
.\deploy\run_spark.ps1 bigdata.run_sql_inverted_index --corpus macro --partitions 4 --tokenizer alnum
```

Both settings give the same answer on Windows and on the cluster, so the job is
deterministic; only the wall-clock differs. **The SQL path demonstrates the
mechanism; the RDD path is the one proven byte-identical to the reference.**

---

## 4. Proving the outputs match

```powershell
python -m pytest tests/test_bigdata_engine.py tests/test_bigdata_jobs.py tests/test_bigdata_search_index.py -q
python -m pytest tests/ -q          # whole suite
```

Or compare two runs directly — build the same corpus with each engine into
separate directories and diff the artifacts:

```powershell
python -m bigdata.run_inverted_index --corpus macro --engine local --partitions 4 --output-dir data/exports/bigdata/_cmp_local
.\deploy\run_spark.ps1 bigdata.run_inverted_index --corpus macro --partitions 4 --output-dir data/exports/bigdata/_cmp_spark
fc /b data\exports\bigdata\_cmp_local\document_frequencies.csv data\exports\bigdata\_cmp_spark\document_frequencies.csv
```

`fc /b` reporting no differences is the byte-identity claim, demonstrated live.

---

## 5. Measuring honestly

Two rules, both learned the hard way while producing the numbers in the report:

1. **Discard the first run.** A cold JVM is roughly 1.8x slower than a warm one
   (7.6 s vs 4.2 s for an identical configuration). `run_sql_inverted_index`
   does this for you; for the RDD CLIs, run twice and take the second.
2. **Measure on an idle machine.** Any background load — another benchmark, a
   container starting, a build — inflates the result several-fold. Check first:

```powershell
(Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average
Get-Process | Sort-Object CPU -Descending | Select-Object -First 5 Name, CPU
```

An earlier round of these benchmarks had to be thrown away entirely because a
runaway process from a previous run was still consuming CPU.
