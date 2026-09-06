# Standalone Spark cluster

A Docker Compose standalone Apache Spark cluster (**1 master + 2 workers**) that
runs the FinPortfolio IR Big Data jobs distributed across real worker nodes. This
is the multi-node deployment of the `engine=spark` path documented in
[`docs/BIG_DATA_INFRASTRUCTURE.md`](../../docs/BIG_DATA_INFRASTRUCTURE.md).

## Prerequisites

- Docker Desktop running (Linux containers).

## Bring the cluster up

```bash
docker compose -f deploy/spark_cluster/docker-compose.yml up -d
```

- Master web UI: <http://localhost:8080> — the two workers register here.
- Worker UIs: <http://localhost:8081>, <http://localhost:8082>.
- Driver UI (while a job runs): <http://localhost:4040>.

The repository is bind-mounted at `/workspace` on every node, so `bigdata/` and
`finportfolio_ir/` are importable by driver and executors, and artifacts are
written back to `data/exports/bigdata/` on the host.

## Submit a job

```bash
# helper: runs the driver in the master container against spark://spark-master:7077
deploy/spark_cluster/submit.sh bigdata.run_inverted_index --corpus macro
deploy/spark_cluster/submit.sh bigdata.run_corpus_analytics --corpus sec300
deploy/spark_cluster/submit.sh bigdata.run_all --corpus macro --query "inflation"
```

Watch the stages fan out across the workers in the master UI while the job runs.

## Tear down

```bash
docker compose -f deploy/spark_cluster/docker-compose.yml down
```

## Notes

- The image is the Docker **official** `spark:3.5.3-scala2.12-java17-python3-ubuntu`
  (bundles PySpark + Python 3). `bitnami/spark` was removed from Docker Hub by
  Broadcom in 2025 — do not use it. The official image has no `SPARK_MODE`
  entrypoint, so the compose file starts master/worker explicitly via
  `spark-class`; the container Python is `/usr/bin/python3` and `SPARK_HOME`
  is `/opt/spark`.
- `SparkEngine` skips the loopback (`127.0.0.1`) driver binding for `spark://`
  masters, so executors on the worker containers can reach the driver.
- Structured Streaming (`bigdata.streaming.spark_structured_streaming`) runs
  cleanly here — the cluster provides the Hadoop environment that Windows lacks.
- The production search index can be built distributed on this cluster:

  ```bash
  deploy/spark_cluster/submit.sh bigdata.run_build_search_index \
    --corpus all_ppo --native-read \
    --output data/exports/bigdata/search_index/finportfolio_search_spark.sqlite
  ```

## Inspecting a finished job (Spark History Server)

The driver UI on `:4040` disappears the moment the application exits, so a
20-second job leaves nothing to look at. The cluster therefore runs a **History
Server** that replays finished applications from the event logs written to
`data/spark-events/`:

- **<http://localhost:18080>** — every finished application.

Click an application → **Stages** → click a stage. That page carries exactly the
diagnostics people know from Databricks (Databricks simply hosts this same
Spark UI):

| What you want to see | Where |
| --- | --- |
| Stage DAG (which RDD ops fused into this stage) | **DAG Visualization** (expand) |
| Per-task coloured breakdown: scheduler delay, task deserialisation, executor computing, shuffle read/write, result serialisation | **Event Timeline** (expand) |
| Shuffle Read / Shuffle Write bytes and records | stage header + Tasks table |
| Per-executor totals | **Aggregated Metrics by Executor** |
| Min / median / max task duration, GC time, input size | **Summary Metrics** |

Event logging is opt-in via the `FINPORTFOLIO_SPARK_EVENTLOG_DIR` environment
variable (already set for every cluster node in `docker-compose.yml`). Set it
locally too if you want history for `local[*]` runs:

```powershell
$env:FINPORTFOLIO_SPARK_EVENTLOG_DIR = "$PWD\data\spark-events"
```

## Submitting from PowerShell

`submit.sh` is a bash script; on Windows `.sh` is usually associated with an
editor, so running it from PowerShell just opens the file. Use the PowerShell
wrapper instead — it also starts the cluster if it is not running:

```powershell
.\deploy\spark_cluster\submit.ps1 bigdata.run_all --corpus all_ppo --native-read --partitions 16
```
