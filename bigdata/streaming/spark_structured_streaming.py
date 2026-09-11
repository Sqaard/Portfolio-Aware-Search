"""Genuine Spark Structured Streaming auto-update job (file source).

Watches a directory as an unbounded stream of JSONL documents. Each micro-batch
is handed to ``foreachBatch``, which writes its result with Python -- so the
Hadoop output committer is never invoked. Three analytics modes (``--analytics``):

* ``light`` (default) -- a running count of documents by source family and year,
  computed in Spark SQL from nine raw JSON fields. It approximates -- but is not
  byte-identical to -- the batch ``corpus_analytics`` report, which derives
  ``source_family`` / ``year`` from schema-normalized timestamps via
  ``FinancialDocument``. Cheap: ``title`` and ``body`` are never parsed.
* ``full`` -- every micro-batch runs the batch pipeline itself,
  :func:`bigdata.jobs.corpus_analytics.raw_metrics`, on the executors over the
  raw JSONL lines that arrived in that trigger, and merges the additive metrics
  into persistent state. ``state.json`` + ``analytics.json`` have the same layout
  as :mod:`bigdata.streaming.incremental_update` and are byte-identical to the
  batch report for the same documents. Unlike the incremental updater, which
  builds and stops a Spark session on every tick, the session here is created
  once and reused by every micro-batch. Every document still crosses into a
  Python worker: four Spark jobs per micro-batch, seconds of fixed toll.
* ``sql`` -- the same report, with the per-document work (JSON parsing, field
  defaults, source family, token count) compiled by Catalyst into one
  whole-stage-codegen scan in the JVM (:mod:`bigdata.streaming.sql_analytics`);
  the driver aggregates one small row per document with the reference
  ``emit_metrics``. One Spark job per micro-batch, no Python workers. Records the
  SQL cannot reproduce exactly are handed to the reference Python on the driver,
  so the report stays byte-identical. This is the real-time mode.

Delivery contract -- read this before wiring a producer. Spark's file source
records each input file by path and never re-reads one it has consumed: bytes
appended to an existing file are silently ignored. Producers must drop each batch
as a NEW file, written atomically (hidden temp name, then rename).
``crawler/live_incremental_fetch.py --streaming-inbox DIR`` does exactly that.
Its default live output -- one JSONL rewritten on every fetch -- is safe only for
:mod:`bigdata.streaming.incremental_update`, which tracks byte offsets; this job
would process that file once and then miss every later batch.

Run modes:

* ``--once``  -> ``Trigger.AvailableNow``: process everything currently in the
  directory, then stop (great for scheduled / cron-style automatic updates).
* default     -> micro-batches every ``--interval`` seconds until interrupted;
  ``--interval 0`` starts the next micro-batch as soon as a file lands (Spark
  polls the inbox every ``spark.sql.streaming.pollingDelay``, 10 ms).

Windows note: the Structured Streaming file source and checkpoint use Hadoop
*native IO* (``NativeIO$Windows.access0``), which needs ``winutils.exe`` /
``HADOOP_HOME`` on Windows and otherwise fails with ``UnsatisfiedLinkError``.
It runs cleanly on the bundled Docker Spark cluster and on Linux. For a
Windows-safe auto-updater (no Hadoop native IO) use
:mod:`bigdata.streaming.incremental_update`.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bigdata.config import ROOT as PROJECT_ROOT  # noqa: E402
from bigdata.engine.spark_engine import SparkEngine  # noqa: E402

DEFAULT_INBOX = PROJECT_ROOT / "data" / "streaming_inbox"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "exports" / "bigdata" / "streaming_state" / "spark_structured"
# sql mode: a micro-batch file is split into scan tasks of at most this many bytes,
# so a 3 MiB batch tokenises on several cores instead of one (no shuffle needed).
DEFAULT_SPLIT_BYTES = 512 * 1024


def _document_schema():
    from pyspark.sql.types import ArrayType, DoubleType, StringType, StructField, StructType

    return StructType([
        StructField("doc_id", StringType()),
        StructField("source", StringType()),
        StructField("source_type", StringType()),
        StructField("url", StringType()),
        StructField("canonical_url", StringType()),
        StructField("available_at", StringType()),
        StructField("matched_tickers", ArrayType(StringType())),
        StructField("event_tags", ArrayType(StringType())),
        StructField("source_credibility", DoubleType()),
    ])


def _with_derived_columns(df):
    """Add ``source_family`` and ``year`` columns (parity with jobs.mapping)."""

    from pyspark.sql import functions as F

    source_type = F.lower(F.coalesce(F.col("source_type"), F.lit("")))
    source = F.lower(F.coalesce(F.col("source"), F.lit("")))
    url = F.lower(F.coalesce(F.col("canonical_url"), F.col("url"), F.lit("")))
    family = (
        F.when(source_type.startswith("official_macro") | url.contains("fred.stlouisfed.org"), "official_macro")
        .when(source_type.startswith("sec_filing") | url.contains("sec.gov") | source.contains("edgar"), "sec_edgar")
        .when(source_type.startswith("company_"), "company_ir")
        .when(source_type == "sample", "sample")
        .otherwise("other")
    )
    year = F.when(
        F.col("available_at").rlike(r"^\d{4}"), F.substring(F.col("available_at"), 1, 4)
    ).otherwise("unknown")
    return df.withColumn("source_family", family).withColumn("year", year)


def _atomic_write_json(path: Path, payload: dict) -> None:
    """Write JSON via temp file + replace, so a reader never sees half a file."""

    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _make_batch_writer(output_dir: Path):
    """``light`` sink: collect the (small) family x year aggregate."""

    output_dir.mkdir(parents=True, exist_ok=True)

    def write_batch(batch_df, batch_id: int) -> None:
        rows = batch_df.collect()
        aggregate = [
            {"source_family": r["source_family"], "year": r["year"], "count": int(r["count"])}
            for r in rows
        ]
        aggregate.sort(key=lambda item: (-item["count"], item["source_family"], item["year"]))
        payload = {
            "batch_id": batch_id,
            "groups": aggregate,
            "total_documents": sum(item["count"] for item in aggregate),
        }
        _atomic_write_json(output_dir / "aggregate.json", payload)
        print(json.dumps({"batch_id": batch_id, "groups": len(aggregate),
                          "total_documents": payload["total_documents"]}), flush=True)

    return write_batch


def _row_value(row) -> str:
    """``text`` source row -> the raw JSONL line (module-level, so it pickles)."""

    return row.value


def _make_full_analytics_writer(engine: SparkEngine, output_dir: Path, *, partitions: int | None, top_n: int,
                                mode: str = "full"):
    """``full`` / ``sql`` sink: merge each micro-batch's corpus-analytics metrics.

    ``full`` runs the batch pipeline over an RDD of the batch's lines.
    ``partitions=None`` resolves to the cluster's cores at the first batch, not at
    session start: on a standalone master ``defaultParallelism`` is read before the
    executors register and would report 2. ``sql`` extracts per-document fields
    in Catalyst and aggregates them on the driver (``partitions`` unused: the scan
    is split by ``spark.sql.files.maxPartitionBytes``).
    """

    from bigdata.engine.spark_engine import SparkDataset
    from bigdata.jobs import corpus_analytics
    from bigdata.streaming import sql_analytics
    from bigdata.streaming.incremental_update import _load_state, _merge_dates, _save_state

    output_dir.mkdir(parents=True, exist_ok=True)
    state_path = output_dir / "state.json"
    # This query is the only writer of state.json -- main() refuses a foreign or
    # mismatched one -- so it is read once here and then kept in memory.
    state = _load_state(state_path)

    def compute(batch_df) -> tuple:
        if mode == "sql":
            # batch_df is already the Catalyst extraction (built into the streaming
            # query by main()): one job, one small row per document.
            return sql_analytics.aggregate(batch_df.collect())
        # A micro-batch that arrived as one file is one input partition, i.e. one
        # task on one core. Spread it over the cluster before the Python-heavy map.
        spread = partitions or max(int(engine.sc.defaultParallelism), 1)
        lines = batch_df.repartition(spread).rdd.map(_row_value)
        return corpus_analytics.raw_metrics(SparkDataset(engine, lines)) + (0,)

    def write_batch(batch_df, batch_id: int) -> None:
        started = time.perf_counter()
        # foreachBatch is at-least-once: after a failure Spark replays a batch with
        # the SAME batch_id. The metrics are additive, so merging a replay would
        # double-count it -- skip every id that is already in the state.
        if batch_id <= int(state.get("last_batch_id", -1)):
            return

        metrics, min_available, max_available, new_docs, fallback = compute(batch_df)

        state["metrics"] = corpus_analytics.merge_metrics(state["metrics"], metrics)
        state["min_available_at"] = _merge_dates(state["min_available_at"], min_available, take_min=True)
        state["max_available_at"] = _merge_dates(state["max_available_at"], max_available, take_min=False)
        state["total_documents"] = int(state.get("total_documents", 0)) + new_docs
        state["last_batch_id"] = batch_id
        # The log below keeps the last 100 entries, so its length cannot be the count.
        state["batches_processed"] = int(state.get("batches_processed", len(state["batches"]))) + 1
        seconds = round(time.perf_counter() - started, 3)
        entry = {
            "epoch": time.time(),
            "batch_id": batch_id,
            "engine": "spark-structured-streaming",
            "analytics": mode,
            "new_documents": new_docs,
            "seconds": seconds,
        }
        if mode == "sql":
            entry["fallback_documents"] = fallback
        state["batches"].append(entry)
        state["batches"] = state["batches"][-100:]

        report = corpus_analytics.finalize_report(
            state["metrics"], state["min_available_at"], state["max_available_at"], top_n=top_n
        )
        report["streaming"] = {
            "batches_processed": state["batches_processed"],
            "last_batch_id": batch_id,
            "last_batch_new_documents": new_docs,
        }
        # Report first, state last: state.json carries last_batch_id, so it is the
        # commit marker. A crash between the two replays the batch into the OLD
        # state and rewrites the report -- consistent either way.
        _atomic_write_json(output_dir / "analytics.json", report)
        _save_state(state_path, state)
        print(json.dumps({"batch_id": batch_id, "new_documents": new_docs,
                          "total_documents": state["total_documents"], "seconds": seconds}), flush=True)

    return write_batch


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description="Spark Structured Streaming corpus auto-updater.")
    parser.add_argument("--inbox", default=str(DEFAULT_INBOX), help="Directory streamed as the JSONL file source.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--checkpoint", default=None, help="Checkpoint dir (default: ASCII temp).")
    parser.add_argument("--master", default=None)
    parser.add_argument("--once", action="store_true", help="Process available files once (Trigger.AvailableNow) and stop.")
    parser.add_argument("--interval", type=float, default=10.0,
                        help="Micro-batch interval seconds (continuous mode); 0 = as soon as a file lands.")
    parser.add_argument("--max-files-per-trigger", type=int, default=100)
    parser.add_argument(
        "--analytics", choices=["light", "full", "sql"], default="light",
        help="light: family x year counts in Spark SQL. full: the batch corpus-analytics report per "
             "micro-batch, through Python workers. sql: the same report, per-document work in Catalyst.",
    )
    parser.add_argument("--partitions", type=int, default=None,
                        help="full mode: partitions per micro-batch (default: the cluster's total cores).")
    parser.add_argument("--split-bytes", type=int, default=DEFAULT_SPLIT_BYTES,
                        help="sql mode: max bytes per scan task (spark.sql.files.maxPartitionBytes).")
    parser.add_argument("--top-n", type=int, default=25, help="full/sql mode: top-N tickers / events / risks.")
    args = parser.parse_args(argv)

    inbox = Path(args.inbox)
    inbox.mkdir(parents=True, exist_ok=True)
    output_dir = Path(args.output_dir)
    # One default checkpoint per mode: two queries must never share one, or a
    # report would inherit batch ids and a file log it never merged.
    checkpoint = args.checkpoint or str(
        Path(tempfile.gettempdir()) / "finportfolio_spark" / f"ss_checkpoint_{args.analytics}"
    )
    report_mode = args.analytics in ("full", "sql")

    if report_mode and "://" not in checkpoint:
        # The replay guard keys on batch_id. Batch ids live in the CHECKPOINT, the
        # merged metrics in state.json, and the two must describe the same stream.
        # Spark writes offsets/<N> before running batch N and commits/<N> after it;
        # the sink saves state (last_batch_id = N) in between. A consistent pair
        # therefore has last_batch_id equal to the last commit -- or one ahead of it
        # when the process died after the sink saved but before Spark committed (that
        # replay is then correctly skipped). Anything else means a checkpoint deleted
        # or restored behind the state's back, or a state written by a different
        # consumer: batches would be skipped or counted twice. Refuse loudly instead.
        # (Checkpoint *existence* is no signal: Spark writes its metadata when the
        # query is constructed, so an empty first run would block every later one.)
        from bigdata.streaming.incremental_update import _load_state

        def last_batch_in(folder: str) -> int:
            path = Path(checkpoint) / folder
            ids = [int(p.name) for p in path.iterdir() if p.name.isdigit()] if path.is_dir() else []
            return max(ids) if ids else -1

        planned, committed = last_batch_in("offsets"), last_batch_in("commits")
        prior = _load_state(output_dir / "state.json")
        last = int(prior.get("last_batch_id", -1))
        foreign = last < 0 and (int(prior.get("total_documents", 0) or 0) > 0 or bool(prior.get("metrics")))
        if foreign or not (last == committed or last == planned == committed + 1):
            raise SystemExit(
                f"checkpoint {checkpoint!r} (last committed batch {committed}) and "
                f"{output_dir / 'state.json'} (last merged batch "
                f"{'none: written by another consumer' if foreign else last}) describe different "
                "streams. Reset both (delete the checkpoint AND the output dir) or restore the matching pair."
            )

    extra_conf = {}
    if args.analytics == "sql":
        extra_conf = {
            # Split a batch file into several scan tasks: parallel parse + tokenise.
            "spark.sql.files.maxPartitionBytes": args.split_bytes,
            # With one input file, every task prefers the executor that read the
            # previous batch; a zero wait spreads them instead of queueing them.
            "spark.locality.wait": "0",
        }
        if "://" not in checkpoint or checkpoint.startswith("file:"):
            # Each micro-batch writes three metadata logs (source file list, offsets,
            # commits). Through Hadoop's checksummed FileContext on a local disk that
            # costs ~38 ms apiece; through the raw local file system 5 ms: ~100 ms per
            # trigger (measured). A rename is atomic on a local file system, which is
            # all the file-system-based checkpoint manager needs.
            extra_conf["spark.hadoop.fs.file.impl"] = "org.apache.hadoop.fs.RawLocalFileSystem"
            extra_conf["spark.sql.streaming.checkpointFileManagerClass"] = (
                "org.apache.spark.sql.execution.streaming.FileSystemBasedCheckpointFileManager")
    engine = SparkEngine(master=args.master, app_name="finportfolio-ir-structured-streaming", extra_conf=extra_conf)
    spark = engine.spark
    from pyspark.sql import functions as F
    from pyspark.sql.streaming import StreamingQueryListener

    output_dir.mkdir(parents=True, exist_ok=True)
    progress_log = output_dir / "progress.jsonl"

    class _ProgressLog(StreamingQueryListener):
        """Record Spark's own per-trigger timing next to the sink's.

        The sink can time only what runs inside foreachBatch. Spark's
        ``triggerExecution`` also covers listing the inbox, planning the batch and
        writing the offset and commit logs: the whole cost the site pays per batch.
        ``durations`` keeps every phase Spark reports (latestOffset, getBatch,
        queryPlanning, walCommit, addBatch, commitOffsets).
        """

        def onQueryStarted(self, event):
            pass

        def onQueryProgress(self, event):
            progress = event.progress
            durations = dict(progress.durationMs or {})
            row = {
                "batch_id": progress.batchId,
                "input_rows": progress.numInputRows,
                "trigger_ms": durations.get("triggerExecution"),
                "add_batch_ms": durations.get("addBatch"),
                "timestamp": progress.timestamp,
                "durations": durations,
            }
            with progress_log.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row) + "\n")

        def onQueryIdle(self, event):
            pass

        def onQueryTerminated(self, event):
            pass

    spark.streams.addListener(_ProgressLog())

    if report_mode:
        # Raw lines, not parsed JSON: both report modes parse each record themselves,
        # exactly as the batch job and the incremental updater do.
        stream = (
            spark.readStream
            .option("maxFilesPerTrigger", args.max_files_per_trigger)
            .text(inbox.as_uri())
        )
        if args.analytics == "sql":
            # The extraction becomes part of the streaming query, so its large
            # expression tree is built and analysed once, at start; each micro-batch
            # only re-optimises the resolved plan. Built inside foreachBatch instead,
            # it would cost ~0.7 s of Python-side construction per batch.
            from bigdata.streaming import sql_analytics

            stream = sql_analytics.extract(stream)
        sink = _make_full_analytics_writer(
            engine, output_dir,
            partitions=args.partitions,
            top_n=args.top_n,
            mode=args.analytics,
        )
        writer = stream.writeStream.outputMode("append").foreachBatch(sink)
    else:
        stream = (
            spark.readStream
            .schema(_document_schema())
            .option("maxFilesPerTrigger", args.max_files_per_trigger)
            .json(inbox.as_uri())
        )
        # Approximate the batch causal-safety drop: keep only rows with a parseable
        # available_at (records the batch build_document would reject have none).
        causal_safe = stream.filter(
            F.col("available_at").isNotNull() & F.col("available_at").rlike(r"^\d{4}-\d{2}-\d{2}")
        )
        aggregated = _with_derived_columns(causal_safe).groupBy("source_family", "year").agg(F.count("*").alias("count"))
        writer = aggregated.writeStream.outputMode("complete").foreachBatch(_make_batch_writer(output_dir))

    writer = writer.option("checkpointLocation", checkpoint)
    if args.once:
        # AvailableNow (Spark >= 3.3) processes all present files then stops.
        try:
            writer = writer.trigger(availableNow=True)
        except TypeError:
            writer = writer.trigger(once=True)
    else:
        writer = writer.trigger(processingTime=f"{args.interval} seconds")

    try:
        query = writer.start()
        query.awaitTermination()
    finally:
        engine.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
