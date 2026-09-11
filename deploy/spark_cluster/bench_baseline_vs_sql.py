"""Back to back: the pre-Big-Data single process vs the Spark cluster, RDD and Catalyst SQL.

All three build the same BM25 statistics over the same 364 MiB corpus (3,029 SEC
filings), in one thermal window, because absolute numbers drift between sessions:

* No Big Data   -- ``indexing/build_sparse_index.py`` in one Windows process
  (``bench_pre_bigdata.py``: read + parse + build, one thread);
* Spark RDD     -- ``bigdata.run_inverted_index --api rdd`` on the 4+4+4 cluster
  (Python workers);
* Spark SQL     -- ``bigdata.run_inverted_index --api sql`` on the 4+4+4 cluster
  (one Catalyst job, no Python worker).

Each side: one warm-up, then 3 full-corpus runs and 3 skeleton runs (12 documents,
same plan) -- overhead = skeleton median, compute = wall - overhead,
thread-seconds = compute x threads, efficiency = best thread-seconds / own.

    python deploy/spark_cluster/bench_baseline_vs_sql.py
"""

from __future__ import annotations

import json
import os
import statistics
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXTRA = Path("C:/tmp/finportfolio_bigdata")               # == /extra in every container
CORPUS_CONTAINER = "/fast/sec_full_text_heavy_documents.jsonl"
SUBMIT = ("--conf spark.executor.cores=4 --conf spark.executor.memory=2000m "
          "--conf spark.cores.max=12 --driver-memory 1g pyspark-shell")
OUT = ROOT / "data" / "exports" / "bigdata" / "baseline_vs_spark.json"


def _env() -> dict:
    env = dict(os.environ)
    env["MSYS_NO_PATHCONV"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("FINPORTFOLIO_SPARK_EVENTLOG_DIR", None)
    return env


def spark_run(api: str, limit: int) -> dict:
    out_dir = f"b2b_{api}"
    stats = EXTRA / out_dir / "bm25_stats.json"
    stats.unlink(missing_ok=True)
    cmd = ["docker", "exec", "-i", "-w", "/workspace", "-e", f"PYSPARK_SUBMIT_ARGS={SUBMIT}",
           "finportfolio-spark-master", "python3", "-m", "bigdata.run_inverted_index",
           "--engine", "spark", "--api", api, "--master", "spark://spark-master:7077",
           "--corpus", CORPUS_CONTAINER, "--partitions", "12", "--output-dir", f"/extra/{out_dir}"]
    cmd += ["--limit", str(limit)] if limit else (["--native-read"] if api == "rdd" else [])
    subprocess.run(cmd, env=_env(), capture_output=True, check=False)
    if not stats.exists():
        raise SystemExit(f"spark {api} run failed (limit={limit})")
    result = json.loads(stats.read_text(encoding="utf-8"))
    return {"seconds": result["run"]["seconds"], "n_docs": result["n_docs"],
            "vocabulary_size": result["vocabulary_size"], "total_tokens": result["total_tokens"]}


def spark_side(api: str) -> dict:
    spark_run(api, 0)                                              # warm-up, discarded
    skeleton = [spark_run(api, 12) for _ in range(3)]
    full = [spark_run(api, 0) for _ in range(3)]
    print(f"spark {api}: full {[r['seconds'] for r in full]}  skeleton {[r['seconds'] for r in skeleton]}", flush=True)
    return {"full": full, "skeleton": skeleton}


def baseline_side() -> dict:
    def run(extra: list) -> dict:
        out = subprocess.run([sys.executable, "deploy/spark_cluster/bench_pre_bigdata.py", "--repeats", "3", *extra],
                             cwd=ROOT, env=_env(), capture_output=True, text=True, encoding="utf-8", check=True)
        return json.loads(out.stdout[out.stdout.rfind("\n{") + 1:])
    full, skeleton = run([]), run(["--limit", "12"])
    print(f"no big data: full {full['runs']}  skeleton {skeleton['runs']}", flush=True)
    return {"full": full, "skeleton": skeleton}


def row(name: str, parallelism: str, threads: int, full: list, skeleton: list, **extra) -> dict:
    wall, overhead = statistics.median(full), statistics.median(skeleton)
    return {"architecture": name, "parallelism": parallelism, "threads": threads,
            "wall_clock": round(wall, 2), "overhead": round(overhead, 2), "compute": round(wall - overhead, 2),
            "thread_seconds": round((wall - overhead) * threads, 1), "full_runs": full, "skeleton_runs": skeleton,
            **extra}


def main() -> int:
    sql, rdd = spark_side("sql"), spark_side("rdd")
    base = baseline_side()
    checks = {(r["n_docs"], r["vocabulary_size"], r["total_tokens"]) for r in sql["full"] + rdd["full"]}
    checks.add((base["full"]["n_docs"], base["full"]["vocabulary_size"], base["full"]["total_tokens"]))
    rows = [
        row("No Big Data", "1 thread", 1, base["full"]["runs"], base["skeleton"]["runs"],
            python=base["full"]["python"], peak_rss_mb=base["full"]["peak_rss_mb"]),
        row("Spark SQL + Catalyst — 4+4+4", "12 threads", 12,
            [r["seconds"] for r in sql["full"]], [r["seconds"] for r in sql["skeleton"]]),
        row("Spark RDD (Python workers) — 4+4+4", "12 threads", 12,
            [r["seconds"] for r in rdd["full"]], [r["seconds"] for r in rdd["skeleton"]]),
    ]
    best = min(r["thread_seconds"] for r in rows)
    for r in rows:
        r["efficiency"] = round(100.0 * best / r["thread_seconds"], 1)
    payload = {"rows": rows, "identical_output": len(checks) == 1, "output": sorted(checks)}
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"{'Architecture':<38}{'Parallelism':>12}{'Wall':>8}{'Overhead':>10}{'Compute':>9}{'Thread-s':>10}{'Eff.':>8}")
    for r in rows:
        print(f"{r['architecture']:<38}{r['parallelism']:>12}{r['wall_clock']:>7.1f}s{r['overhead']:>9.2f}s"
              f"{r['compute']:>8.1f}s{r['thread_seconds']:>10.1f}{r['efficiency']:>7.1f}%")
    print(f"identical output across all three: {payload['identical_output']}  -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
