#!/usr/bin/env bash
# Стриминг: Spark Structured Streaming против incremental_update.
#
# Все три конфигурации исполняются ВНУТРИ контейнеров, читают один и тот же
# /fast/inbox, поэтому файловая система и данные одинаковы. Замеряется wall clock
# процесса: ни у одной из двух реализаций нет внутреннего таймера, как у
# run_inverted_index, а для авто-обновления сквозное время и есть то, что важно.
#
# Состояние сбрасывается ПЕРЕД каждым прогоном: incremental_update помнит
# байтовые смещения обработанных файлов, а Structured Streaming — свой чекпойнт;
# без сброса второй прогон не сделал бы ничего и отчитался бы нулём за 5 секунд.
set -u
PROJ="C:/Users/ivanp/OneDrive/Рабочий стол/доки+черчи/ITMO/2_sem/FinRL_Tsinghua/FinPortfolio_IR"
cd "$PROJ" || exit 1
COMPOSE="deploy/spark_cluster/docker-compose.arch-D-3x4.yml"
CSV="data/exports/bigdata/streaming_experiment.csv"
REPEATS="${1:-3}"

echo "config,kind,run,seconds,documents" > "$CSV"

run_ss() {   # $1=inbox  $2=shuffle partitions
  MSYS_NO_PATHCONV=1 docker compose -f "$COMPOSE" exec -T \
    -e PYSPARK_SUBMIT_ARGS="--conf spark.sql.shuffle.partitions=$2 --driver-memory 3g pyspark-shell" \
    spark-master sh -c "rm -rf /tmp/ss_ckpt /tmp/ss_out && \
      python3 -m bigdata.streaming.spark_structured_streaming \
        --inbox $1 --master spark://spark-master:7077 --once \
        --checkpoint /tmp/ss_ckpt --output-dir /tmp/ss_out" 2>/dev/null \
    | grep -o '"total_documents": [0-9]*' | tail -1 | grep -o '[0-9]*'
}

run_inc() {  # $1=inbox  $2=engine spec
  MSYS_NO_PATHCONV=1 docker compose -f "$COMPOSE" exec -T \
    -e PYSPARK_SUBMIT_ARGS="--driver-memory 3g pyspark-shell" \
    spark-master sh -c "rm -rf /tmp/inc_state && \
      python3 -m bigdata.streaming.incremental_update \
        --inbox $1 --state-dir /tmp/inc_state --engine $2 --partitions 12" 2>/dev/null \
    | grep -o '"new_documents": [0-9]*' | tail -1 | grep -o '[0-9]*'
}

measure() {  # $1=label  $2=kind(full|skeleton)  $3=fn  $4=arg1  $5=arg2
  local t0 t1 docs secs
  t0=$(date +%s.%N)
  docs=$("$3" "$4" "$5")
  t1=$(date +%s.%N)
  secs=$(python -c "print(round($t1-$t0,2))")
  docs=${docs:-0}
  local expected=3029
  [ "$2" = "skeleton" ] && expected=12
  local flag=""
  [ "$docs" != "$expected" ] && flag="  <-- ОЖИДАЛОСЬ $expected, ПРОГОН НЕ ЗАСЧИТАН"
  printf "  %-11s %-9s %7.2f s   документов=%s%s\n" "$1" "$2" "$secs" "$docs" "$flag"
  echo "$1,$2,$6,$secs,$docs" >> "$CSV"
}

echo "=== SS-12: Structured Streaming, shuffle.partitions=12 ==="
measure SS-12 full run_ss /fast/inbox 12 warmup >/dev/null
for i in $(seq 1 "$REPEATS"); do measure SS-12 full run_ss /fast/inbox 12 "$i"; done
for i in $(seq 1 "$REPEATS"); do measure SS-12 skeleton run_ss /fast/inbox_skel 12 "$i"; done

echo "=== SS-200: то же, но с дефолтными 200 shuffle-партициями ==="
for i in $(seq 1 "$REPEATS"); do measure SS-200 full run_ss /fast/inbox 200 "$i"; done

echo "=== INC-SPARK: incremental_update на кластере ==="
measure INC-SPARK full run_inc /fast/inbox "spark --master spark://spark-master:7077" warmup >/dev/null
for i in $(seq 1 "$REPEATS"); do measure INC-SPARK full run_inc /fast/inbox "spark --master spark://spark-master:7077" "$i"; done
for i in $(seq 1 "$REPEATS"); do measure INC-SPARK skeleton run_inc /fast/inbox_skel "spark --master spark://spark-master:7077" "$i"; done

echo "=== INC-LOCAL: incremental_update, локальный движок (12 процессов) ==="
measure INC-LOCAL full run_inc /fast/inbox local warmup >/dev/null
for i in $(seq 1 "$REPEATS"); do measure INC-LOCAL full run_inc /fast/inbox local "$i"; done
for i in $(seq 1 "$REPEATS"); do measure INC-LOCAL skeleton run_inc /fast/inbox_skel local "$i"; done

echo
echo "Результаты: $CSV"
