"""Live-arrival benchmark: how long is the site busy when a fetch lands?

Scenario (the one the course project actually runs): the site is up, a streaming
consumer is already running and idle, and ``crawler/live_incremental_fetch.py``
delivers a new batch of documents. The fetch itself is simulated
(``--simulate-from``: a reproducible random sample of an existing corpus), so the
benchmark measures the consumers, not the SEC API.

Consumers:

* ``ss``  -- Spark Structured Streaming in the Docker cluster, one long-lived query.
  ``--ss-analytics full`` runs the batch pipeline through Python workers,
  ``--ss-analytics sql`` the Catalyst port (:mod:`bigdata.streaming.sql_analytics`).
  ``--ss-master`` picks the standalone cluster or an in-driver ``local[N]``.
* ``inc`` -- ``bigdata.streaming.incremental_update`` on Windows, one long-lived
  polling loop. Both of its engines run under the conda env that carries PySpark.

Storage (``--ss-storage``):

* ``extra``  -- inbox and state on ``C:/tmp/finportfolio_bigdata/<dir>``, which every
  container sees as ``/extra`` (a Docker Desktop bind mount, 9p under WSL2). The
  producer runs on Windows and the host polls ``state.json``.
* ``native`` -- inbox, checkpoint and state on the container's own filesystem; the
  producer runs in the container next to Spark, and ``live_arrival_probe.py`` times
  the arrival there, on the container clock. This is each consumer on its own
  local disk: ``inc`` reads NTFS, ``ss`` reads the Linux overlay.

Protocol, per consumer:

* one warm-up arrival of EVERY size first, discarded;
* then the sizes are INTERLEAVED (12, 200, 12, 200, ...), so batch size is not
  confounded with run order and warming drift;
* ``--gap`` seconds between arrivals, so each batch lands on an idle consumer the
  way a real fetch every few minutes does (not back to back).

Recorded per arrival:

* ``latency_s``    -- producer's ``emitted_at`` (right after the atomic rename) to
  the moment ``state.json`` shows the batch merged: what a user of the site feels.
* ``processing_s`` -- the consumer's own record in ``state.batches[-1].seconds``.
  For Structured Streaming this is the sink only; the analysis replaces it with
  Spark's ``triggerExecution`` from the per-arm ``*_progress.jsonl`` (joined on
  ``batch_key``), which also covers listing, planning and the offset / commit logs.

    python deploy/spark_cluster/bench_live_arrival.py --consumer ss  --sizes 12,200 --repeats 5 --gap 30
    python deploy/spark_cluster/bench_live_arrival.py --consumer ss  --ss-analytics sql --ss-master local[4]
    python deploy/spark_cluster/bench_live_arrival.py --consumer inc --engine local --sizes 12,200 --repeats 5 --gap 30
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import signal
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXTRA_HOST = Path("C:/tmp/finportfolio_bigdata")          # == /extra inside every container
CONTAINER_ROOT = "/workspace"                              # the repo inside every container
NATIVE_ROOT = "/tmp/live"                                  # container-native storage (not a bind mount)
COMPOSE = ROOT / "deploy" / "spark_cluster" / "docker-compose.arch-D-3x4.yml"
DEFAULT_CORPUS = ROOT / "data" / "processed_documents" / "sec_macro_company_ir_ppo_2010_2023_documents.jsonl"
CONDA_PY = "C:/Users/ivanp/anaconda3/envs/tensorflow/python.exe"
FIELDS = ["consumer", "size", "run", "latency_s", "processing_s", "new_documents", "producer_s",
          "interval_s", "gap_s", "batch_key", "python", "host", "corpus"]


def _env() -> dict:
    env = dict(os.environ)
    env["MSYS_NO_PATHCONV"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["FINPORTFOLIO_EXTRA_DIR"] = str(EXTRA_HOST).replace("\\", "/")
    # Windows Spark dies resolving a Cyrillic event-log dir; the cluster sets its own.
    env.pop("FINPORTFOLIO_SPARK_EVENTLOG_DIR", None)
    return env


def _exec(*cmd: str) -> list:
    return ["docker", "compose", "-f", str(COMPOSE), "exec", "-T", "spark-master", *cmd]


def read_state(state_path: Path) -> dict:
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def wait_for_total(state_path: Path, target: int, timeout: float, proc: subprocess.Popen) -> float:
    """Poll ``state.json`` until ``total_documents >= target``; fail fast if the consumer dies."""

    deadline = time.time() + timeout
    while time.time() < deadline:
        if int(read_state(state_path).get("total_documents", 0) or 0) >= target:
            return time.time()
        if proc.poll() is not None:
            raise SystemExit(f"consumer exited (code {proc.returncode}) while waiting for {target} documents")
        time.sleep(0.05)
    raise SystemExit(f"timed out after {timeout:.0f}s waiting for {target} documents")


def consumer_label(args) -> str:
    if args.consumer == "inc":
        return f"inc-{args.engine}"
    label = "ss" if args.ss_analytics == "full" else "ss-sql"
    if args.ss_master.startswith("local"):
        label += "-local"
    if args.ss_storage == "native":
        label += "-native"
    return label


def consumer_python(args) -> str:
    if args.consumer == "ss":
        out = subprocess.run(_exec("python3", "-c", "import sys; print(sys.version.split()[0])"),
                             env=_env(), capture_output=True, text=True)
        return out.stdout.strip() or "?"
    out = subprocess.run([CONDA_PY, "-c", "import sys; print(sys.version.split()[0])"],
                         capture_output=True, text=True)
    return out.stdout.strip() or "?"


def start_consumer(args, paths: dict, log_path: Path) -> subprocess.Popen:
    if args.consumer == "ss":
        cmd = [
            "docker", "compose", "-f", str(COMPOSE), "exec", "-T",
            "-e", f"PYSPARK_SUBMIT_ARGS=--conf spark.sql.shuffle.partitions={args.partitions} "
                  f"--driver-memory 3g pyspark-shell",
            "spark-master", "python3", "-m", "bigdata.streaming.spark_structured_streaming",
            "--inbox", paths["inbox"],
            "--output-dir", paths["state"],
            "--checkpoint", paths["checkpoint"],
            "--master", args.ss_master,
            "--analytics", args.ss_analytics, "--interval", str(args.interval),
            "--partitions", str(args.partitions),
        ]
    else:
        cmd = [
            CONDA_PY, "-m", "bigdata.streaming.incremental_update",
            "--inbox", paths["inbox"],
            "--state-dir", paths["state"],
            "--engine", args.engine, "--interval", str(args.interval),
            "--partitions", str(args.partitions),
        ]
        if args.engine == "spark":
            cmd += ["--master", f"local[{args.partitions}]"]
    log = open(log_path, "w", encoding="utf-8")
    return subprocess.Popen(cmd, cwd=ROOT, env=_env(), stdout=log, stderr=subprocess.STDOUT,
                            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))


def stop_consumer(args, proc: subprocess.Popen) -> None:
    if args.consumer == "ss":
        # exec -T does not forward a kill to the in-container python; stop it there.
        subprocess.run(_exec("pkill", "-f", "spark_structured_streaming"), env=_env(),
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        proc.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGTERM)
        proc.wait(timeout=20)
    except Exception:
        proc.kill()


def deliver(args, inbox_host: Path, size: int, seed: int, live_file: Path) -> dict:
    out = subprocess.run(
        [sys.executable, "-m", "crawler.live_incremental_fetch",
         "--simulate-from", str(args.corpus), "--simulate-count", str(size),
         "--simulate-seed", str(seed), "--processed-output", str(live_file),
         "--streaming-inbox", str(inbox_host)],
        cwd=ROOT, env=_env(), capture_output=True, text=True, encoding="utf-8", check=True,
    )
    return json.loads(out.stdout)


def deliver_native(args, paths: dict, size: int, seed: int, target: int) -> dict:
    """Producer + completion probe inside the container (one clock for both stamps)."""

    corpus = f"{CONTAINER_ROOT}/{args.corpus.resolve().relative_to(ROOT).as_posix()}"
    out = subprocess.run(
        _exec("python3", "deploy/spark_cluster/live_arrival_probe.py",
              "--corpus", corpus, "--size", str(size), "--seed", str(seed),
              "--inbox", paths["inbox"], "--live-file", paths["live"],
              "--state", f"{paths['state']}/state.json", "--target", str(target),
              "--timeout", str(args.timeout)),
        cwd=ROOT, env=_env(), capture_output=True, text=True, encoding="utf-8",
    )
    if out.returncode != 0:
        raise SystemExit(f"in-container probe failed: {out.stderr.strip()[-800:]}")
    return json.loads(out.stdout.strip().splitlines()[-1])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--consumer", choices=["ss", "inc"], required=True)
    parser.add_argument("--engine", choices=["local", "spark"], default="local",
                        help="inc only: incremental_update engine on Windows.")
    parser.add_argument("--ss-analytics", choices=["full", "sql"], default="full",
                        help="ss only: full = batch pipeline through Python workers, sql = Catalyst port.")
    parser.add_argument("--ss-master", default="spark://spark-master:7077",
                        help="ss only: the standalone cluster, or local[N] inside the driver.")
    parser.add_argument("--ss-storage", choices=["extra", "native"], default="extra",
                        help="ss only: inbox/state on the Windows bind mount, or on the container's own disk.")
    parser.add_argument("--sizes", default="12,200")
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--interval", type=float, default=1.0, help="Trigger / poll interval, seconds.")
    parser.add_argument("--gap", type=float, default=30.0, help="Idle seconds between arrivals.")
    parser.add_argument("--partitions", type=int, default=12)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--csv", type=Path, default=ROOT / "data" / "exports" / "bigdata" / "live_arrival.csv")
    args = parser.parse_args()

    label = consumer_label(args)
    native = args.consumer == "ss" and args.ss_storage == "native"
    inbox_name = f"live_inbox_{label}"
    inbox_host = EXTRA_HOST / inbox_name
    state_host = EXTRA_HOST / f"live_state_{label}"
    live_file = EXTRA_HOST / f"live_processed_{label}.jsonl"   # outside the inbox, on purpose
    if native:
        base = f"{NATIVE_ROOT}/{label}"
        paths = {"inbox": f"{base}/inbox", "state": f"{base}/state", "live": f"{base}/processed.jsonl",
                 "checkpoint": f"{base}/checkpoint"}
    elif args.consumer == "ss":
        paths = {"inbox": f"/extra/{inbox_name}", "state": f"/extra/{state_host.name}",
                 "checkpoint": f"/tmp/live_ckpt_{inbox_name}"}
    else:
        paths = {"inbox": str(inbox_host), "state": str(state_host)}

    for path in (inbox_host, state_host):
        shutil.rmtree(path, ignore_errors=True)
        if not native:
            path.mkdir(parents=True, exist_ok=True)
    live_file.unlink(missing_ok=True)
    if args.consumer == "ss":
        stale = [paths["checkpoint"]] + ([f"{NATIVE_ROOT}/{label}"] if native else [])
        subprocess.run(_exec("rm", "-rf", *stale), env=_env(), check=False)
    state_path = state_host / "state.json"

    sizes = [int(s) for s in args.sizes.split(",") if s.strip()]
    python = consumer_python(args)
    host = ("docker-linux" + ("-native-fs" if native else "")) if args.consumer == "ss" else "windows"
    new_file = not args.csv.exists()
    args.csv.parent.mkdir(parents=True, exist_ok=True)
    sink = open(args.csv, "a", newline="", encoding="utf-8")
    writer = csv.DictWriter(sink, fieldnames=FIELDS)
    if new_file:
        writer.writeheader()

    # one warm-up per size, then sizes interleaved so size is not confounded with order
    plan = [(s, "warmup") for s in sizes] + [(s, str(r)) for r in range(1, args.repeats + 1) for s in sizes]
    print(f"[{label}] python {python} on {host}; {len(plan)} arrivals, gap {args.gap:g}s", flush=True)
    EXTRA_HOST.mkdir(parents=True, exist_ok=True)
    proc = start_consumer(args, paths, EXTRA_HOST / f"live_state_{label}.consumer.log")
    expected_total = 0
    seed = 1000
    try:
        time.sleep(3)
        if proc.poll() is not None:
            raise SystemExit(f"consumer exited immediately, see {state_host}.consumer.log")
        for size, run in plan:
            seed += 1
            expected_total += size
            if native:
                probe = deliver_native(args, paths, size, seed, expected_total)
                if proc.poll() is not None:
                    raise SystemExit(f"consumer exited (code {proc.returncode})")
                delivered, done = probe["delivered"], probe["done"]
                last, batch_key = probe["last_batch"], probe["last_batch_id"]
            else:
                delivered = deliver(args, inbox_host, size, seed, live_file)
                done = wait_for_total(state_path, expected_total, args.timeout, proc)
                state = read_state(state_path)
                last = (state.get("batches") or [{}])[-1]
                batch_key = state.get("last_batch_id", state.get("batches_processed"))
            if delivered["processed_appended"] != size:
                raise SystemExit(f"producer delivered {delivered['processed_appended']} of {size}")
            latency = round(done - delivered["emitted_at"], 3)
            print(f"  {run:>7}  size={size:<4} latency={latency:7.2f}s  processing={last.get('seconds')}s  "
                  f"new_docs={last.get('new_documents')}  batch={batch_key}", flush=True)
            if run != "warmup":
                writer.writerow({
                    "consumer": label, "size": size, "run": run, "latency_s": latency,
                    "processing_s": last.get("seconds"), "new_documents": last.get("new_documents"),
                    "producer_s": delivered["producer_seconds"], "interval_s": args.interval,
                    "gap_s": args.gap, "batch_key": batch_key, "python": python, "host": host,
                    "corpus": args.corpus.name,
                })
                sink.flush()
            time.sleep(args.gap)
    finally:
        stop_consumer(args, proc)
        sink.close()
        if args.consumer == "ss":
            progress_dest = args.csv.with_name(f"{args.csv.stem}_{label}_progress.jsonl")
            if native:
                out = subprocess.run(_exec("cat", f"{paths['state']}/progress.jsonl"), env=_env(),
                                     capture_output=True, text=True, encoding="utf-8")
                if out.returncode == 0:
                    progress_dest.write_text(out.stdout, encoding="utf-8")
            elif (state_host / "progress.jsonl").exists():
                shutil.copyfile(state_host / "progress.jsonl", progress_dest)

    rows = [r for r in csv.DictReader(open(args.csv, encoding="utf-8")) if r["consumer"] == label]
    for size in sizes:
        lat = [float(r["latency_s"]) for r in rows if int(r["size"]) == size]
        busy = [float(r["processing_s"]) for r in rows if int(r["size"]) == size and r["processing_s"]]
        if lat:
            print(f"[{label}] size {size}: median latency {statistics.median(lat):.2f}s"
                  + (f", median processing {statistics.median(busy):.2f}s" if busy else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
