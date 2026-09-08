# Reproduce this project from a clean clone

**Portfolio-Aware Search** is a source-first information-retrieval system for US
equities, and the Big Data course project for ITMO, 2nd semester. It has two
halves over one corpus:

- the **IR half** (`crawler/`, `indexing/`, `retrieval/`, `evaluation/`,
  `web_app.py`) collects official filings, macro releases and company investor
  material, dates every record point-in-time, ranks it against a portfolio, and
  scores that ranking against human relevance judgements;
- the **Big Data half** (`bigdata/`, `deploy/spark_cluster/`) re-expresses the
  corpus-wide processing as MapReduce jobs behind one `Dataset` interface, with
  two interchangeable backends: Apache Spark, and a pure-Python engine that
  needs no JVM. The write-up is
  [docs/BIG_DATA_INFRASTRUCTURE.md](docs/BIG_DATA_INFRASTRUCTURE.md).

This guide goes from `git clone` to a green test run. Every step states what you
should see, so you know immediately whether it worked.

## What to check first

1. **Steps 1-4** give you a green suite of 243 tests. Budget five minutes.
2. **Step 6** runs the Big Data pipeline over the 993-document corpus that is
   committed to this repository, so you can watch a MapReduce job produce a
   report instead of reading about one.
3. **[What cannot be reproduced here](#what-cannot-be-reproduced-here)** says
   plainly which claims in the write-up your machine re-runs and which ones rest
   on recorded artifacts. Read it before judging the numbers.

## Before you start

- **Python 3.9 or newer**, and **git**. Nothing else is needed for steps 1-8.
- About **90 MB** for the clone; ~150 MB with the virtual environment.
- Optional, for step 9 only: a **Java 8, 11 or 17** runtime, for Apache Spark.

---

## 1. Clone

```
git clone https://github.com/Sqaard/Portfolio-Aware-Search.git
cd Portfolio-Aware-Search
```

Expected: git ends with `Resolving deltas: 100% ... done.`, and the directory
contains `README.md`, `bigdata/` and `requirements.txt`. If the clone aborts,
see [Troubleshooting](#troubleshooting).

## 2. Create a virtual environment

This is the only step that differs between operating systems.

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Linux / macOS bash:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Expected: your prompt is prefixed with `(.venv)`, and `python --version` prints
`Python 3.9` or newer.

> From here on every command is a single line and is **identical in PowerShell
> and in bash**. All paths use forward slashes, which both shells accept. Run
> everything from the repository root.

## 3. Install dependencies

```
python -m pip install -r requirements.txt
```

Expected: a `Successfully installed ...` line that includes `beautifulsoup4`,
`pytest`, `pyyaml`, `requests` and `tzdata` (exact versions vary). Those five
are the whole dependency set for everything in this guide; the Big Data layer's
local engine is pure standard library.

## 4. Run the tests

```
python -m unittest discover -s tests
```

Expected (the timing varies with your machine):

```
Ran 243 tests in 14.3s

OK (skipped=5)
```

Or, equivalently:

```
python -m pytest -q
```

```
238 passed, 5 skipped in 15.7s
```

**The five skips are expected at this point.** Four are the real-Spark parity
tests (`tests/test_bigdata_spark.py:23`,
`tests/test_bigdata_search_index.py:156`); they skip because PySpark is not
installed, and step 9 runs them. The fifth
(`tests/test_bigdata_search_index.py:129`) skips because the search index has
not been built yet; step 5 removes it.

If you instead see `Interrupted: N errors during collection` and zero tests run,
the dependencies did not install into the interpreter you are using -- see
[Troubleshooting](#troubleshooting).

## 5. Build the search index

```
python indexing/build_search_index.py --documents data/processed_documents/repo_demo_documents.jsonl --output data/search_index/finportfolio_search.sqlite
```

Expected:

```
{
  "output_path": ".../data/search_index/finportfolio_search.sqlite",
  "document_count": 993,
  "feature_doc_count": 0
}
```

`document_count: 993` is the number to check. `feature_doc_count: 0` is correct
here: the doc-level text-feature CSV that would otherwise be joined in is a
large derived artifact that is not redistributed. The `.sqlite` file itself is
gitignored, so this does not dirty your clone.

Re-run the suite, and the tail of the output changes to:

```
OK (skipped=4)
```

(`python -m pytest -q` -> `239 passed, 4 skipped`.) One skip fewer: the index
you just built is now checked against what the web app expects. Only the four
Spark tests remain skipped.

## 6. Run the Big Data layer

```
python -m bigdata.run_all --corpus repo_demo --engine local --query "inflation interest rates" --output-dir data/exports/bigdata/local_runs/demo
```

Expected (paths print with backslashes on Windows; the two `_seconds` values are
wall-clock and will differ, everything else is deterministic):

```
[bigdata] full pipeline | engine=local corpus=repo_demo
{
  "engine": "local",
  "corpus": ".../data/processed_documents/repo_demo_documents.jsonl",
  "total_documents": 993,
  "vocabulary_size": 21211,
  "analytics_seconds": 0.81,
  "index_seconds": 0.42,
  "output_dir": "data/exports/bigdata/local_runs/demo",
  "report": "data/exports/bigdata/local_runs/demo/REPORT.md"
}
```

That is one pass over 993 documents producing corpus analytics, a BM25 inverted
index and a scored query, in a couple of seconds, with no Spark and no
third-party packages. The report it wrote,
`data/exports/bigdata/local_runs/demo/REPORT.md`, should begin:

```
# Big Data corpus report -- `repo_demo`

- Engine: **local**
- Documents: **993**
- Vocabulary (distinct terms): **21,211**
- Total tokens: **1,440,187**
- Average document length: **1450.3** tokens
```

Smoke test on the 24-document corpus that the parity tests use:

```
python -m bigdata.run_all --corpus sample --engine local --output-dir data/exports/bigdata/local_runs/sample
```

```
  "total_documents": 24,
  "vocabulary_size": 316,
```

`repo_demo` and `sample` are the two corpora this repository ships. Passing
`--output-dir` keeps your run in its own directory, beside -- not on top of --
the committed evidence directories under `data/exports/bigdata/`.

## 7. Run IR retrieval and evaluation

Retrieve the top 10 causally valid documents for the sample portfolio, as of a
fixed decision time:

```
python retrieval/retrieve_for_portfolio.py --documents data/processed_documents/documents.jsonl --portfolio configs/sample_portfolio.yaml --metadata data/processed_documents/ticker_metadata.csv --decision-datetime 2022-03-15T09:30:00-05:00 --top-k 10 --output data/exports/local_runs/retrieved_docs.jsonl --run-csv data/exports/local_runs/run.csv
```

```
Wrote 10 retrieved documents to data/exports/local_runs/retrieved_docs.jsonl
```

Score that ranking against the human relevance judgements in
`data/annotations/sample_qrels.csv`:

```
python evaluation/evaluate_ir_metrics.py --qrels data/annotations/sample_qrels.csv --run data/exports/local_runs/run.csv --output data/exports/local_runs/metrics.csv
```

```
{'query_id': 'sample_portfolio_001_2022-03-15', 'method': 'full_hybrid', 'precision_at_5': 0.6, 'precision_at_10': 0.5, 'ndcg_at_5': 0.6992148198508502, 'ndcg_at_10': 0.8119895687410572, 'map': 0.75, 'mrr': 1.0}
```

> **Note.** The committed `data/exports/sample_run.csv` and
> `data/exports/sample_metrics.csv` were written by an earlier revision of the
> ranker, and from two different `--method` settings. Your run reproduces the
> same document ordering but not the same scores to the last digit. The numbers
> your own run prints are the current ones.

## 8. Start the web app

```
python web_app.py
```

```
FinPortfolio IR dashboard: http://127.0.0.1:8765
```

Open that URL; `Ctrl+C` stops the server. With step 5 done it searches through
the SQLite FTS index. Without it, it falls back to an in-memory scan of the same
993 documents -- slower, same results.

## 9. Optional: run the Spark engine

Everything above uses the portable local engine. To exercise the distributed
one:

```
python -m pip install -r requirements-bigdata.txt
```

Spark 3.5 needs a **Java 8, 11 or 17** runtime, found via `JAVA_HOME` or `PATH`.
It will not start on a newer JDK.

```
python -m bigdata.run_all --corpus repo_demo --engine spark --master "local[*]" --output-dir data/exports/bigdata/local_runs/demo_spark
```

Expected: `"engine": "spark"`, with `total_documents` and `vocabulary_size`
identical to step 6 -- 993 and 21211. That equality, on the same corpus with one
flag changed, is the parity claim.

Re-running the suite now executes the four tests that skipped before. If Java is
missing they skip a second time, with `Spark could not start: ...`
(`tests/test_bigdata_spark.py:31`).

For the Docker Compose standalone cluster (1 master, 2 workers) see
[`deploy/spark_cluster/`](deploy/spark_cluster); for live-demo sequences see
[docs/DEFENCE_RUNBOOK.md](docs/DEFENCE_RUNBOOK.md).

---

## What cannot be reproduced here

**1. The large corpora, and every headline number that comes from them.** The
`macro` (18,240 documents, 39 MB), `sec300`, `sec_ppo`, `sec_sections`
(~294 MB) and `all_ppo` (26,368 documents, ~352 MB) corpora are not in this
repository. They are too large to redistribute in a normal git repository, and
part of the company material is issuer-copyrighted. Any command naming those
corpora stops with `FileNotFoundError: Corpus not found: ...` on your machine.
The evidence for those runs is therefore **recorded, not re-run** -- committed
output you can read and diff:

- `data/exports/bigdata/report_macro_local/` -- the macro corpus, local engine;
- `data/exports/bigdata/report_macro_cluster/` -- the same corpus on the Spark
  cluster. Its `document_frequencies.csv` is byte-identical to the local one;
  that is the byte-identity claim, as a recorded artifact;
- `data/exports/bigdata/report_all_ppo_cluster/` -- the 352 MB corpus on the
  cluster;
- `docs/assets/spark_cluster_master.png`, `spark_cluster_executors.png`,
  `spark_cluster_stages.png`, `spark_history_stage_detail.png` -- the cluster
  and History Server UIs during those runs.

Those are artifacts of runs that happened. Read them as records, not as
something your `python -m unittest` reproduces.

**2. The Spark half of the test suite.** Four of the 243 tests are real-Spark
parity tests, and they self-skip when PySpark or a JVM is absent; step 9 is what
runs them. The other 239 do run on a clean clone, and they still pin
`local engine == the single-machine BM25Index reference` -- the local engine is
the correctness oracle, so a Spark regression cannot hide behind them. But a
clean clone does not itself execute Spark, and this guide does not pretend
otherwise.

**3. The public tunnel demo.** `docs/PROFESSOR_DEMO.md` additionally needs
`cloudflared` installed. Step 8 serves the same site locally.

## Troubleshooting

**`RPC failed; curl 56 ...` or `early EOF` during the clone.** The history is
large enough that some networks drop the transfer. Retry without the historical
blobs:

```
git clone --filter=blob:none https://github.com/Sqaard/Portfolio-Aware-Search.git
```

Blobs are then fetched on demand. Everything in this guide still works.

**`Filename too long` on Windows.** Enable long paths for the clone:

```
git clone -c core.longpaths=true https://github.com/Sqaard/Portfolio-Aware-Search.git
```

Combine both flags if you need to.

**`pytest` reports `Interrupted: N errors during collection` and runs no
tests.** The dependencies are not installed in the interpreter you are running.
Confirm the virtual environment is active, re-run step 3, then check:

```
python -c "import requests, bs4; from zoneinfo import ZoneInfo; ZoneInfo('America/New_York'); print('ok')"
```

**`ZoneInfoNotFoundError: 'No time zone found with key America/New_York'`.**
Python's `zoneinfo` ships no time-zone database of its own, and Windows and slim
Linux images have none to fall back on: `python -m pip install tzdata`.

**`FileNotFoundError: Corpus not found: ...`.** You named a corpus that is not
in this repository. `repo_demo` and `sample` are the two that ship; see
[What cannot be reproduced here](#what-cannot-be-reproduced-here).

**`EngineUnavailableError` on `--engine spark`.** PySpark is not installed
(step 9), or no Java 8/11/17 runtime was found.

---

The code in this repository is MIT-licensed; see [LICENSE](LICENSE). The corpora
under `data/` are not: each record carries its own `content_license_note` and
`robots_policy` recording the terms it was collected under. If you reuse the
data, go back to the primary source rather than to this cache.
