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

- The image is `bitnami/spark:3.5.3` (bundles PySpark). If that tag is
  unavailable in your registry, substitute another Spark 3.5 image with Python
  (e.g. `apache/spark:3.5.3`) and keep `PYSPARK_PYTHON` pointing at its Python.
- `SparkEngine` skips the loopback (`127.0.0.1`) driver binding for `spark://`
  masters, so executors on the worker containers can reach the driver.
- Structured Streaming (`bigdata.streaming.spark_structured_streaming`) runs
  cleanly here — the cluster provides the Hadoop environment that Windows lacks.
