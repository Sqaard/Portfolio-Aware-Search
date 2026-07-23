#!/usr/bin/env bash
# Submit a FinPortfolio IR Big Data job to the standalone Spark cluster.
#
# The driver runs inside the master container and connects to the cluster master
# at spark://spark-master:7077; the map/reduce stages are distributed to the
# worker containers. Watch it at http://localhost:8080 (and http://localhost:4040
# while a job runs).
#
# Usage:
#   deploy/spark_cluster/submit.sh bigdata.run_inverted_index --corpus sample
#   deploy/spark_cluster/submit.sh bigdata.run_corpus_analytics --corpus macro
#   deploy/spark_cluster/submit.sh bigdata.run_all --corpus macro --query "inflation"
set -euo pipefail

MODULE="${1:?usage: submit.sh <module> [args...]}"
shift || true

COMPOSE_FILE="$(dirname "$0")/docker-compose.yml"

exec docker compose -f "$COMPOSE_FILE" exec \
  spark-master \
  /opt/bitnami/python/bin/python3 -m "$MODULE" \
    --engine spark \
    --master spark://spark-master:7077 \
    "$@"
