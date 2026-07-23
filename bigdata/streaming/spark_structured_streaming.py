"""Genuine Spark Structured Streaming auto-update job (file source).

Watches a directory as an unbounded stream of JSONL documents and maintains a
running aggregate of document counts by source family and year. Each micro-batch
is handed to ``foreachBatch``, which collects the (small) aggregate and writes it
with Python -- so the Hadoop output committer is never invoked.

Note: this is a lightweight *live* aggregation computed from raw JSON fields in
Spark SQL. It approximates -- but is not byte-identical to -- the batch
``corpus_analytics`` report, which derives ``source_family`` / ``year`` from
schema-normalized (UTC, ``published_at``-fallback) timestamps via
``FinancialDocument``. For exact figures use the batch job; use this for a
continuously-updating overview. The Windows-safe
:mod:`bigdata.streaming.incremental_update` reuses the batch code and *is*
byte-identical to it.

Run modes:

* ``--once``  -> ``Trigger.AvailableNow``: process everything currently in the
  directory, then stop (great for scheduled / cron-style automatic updates).
* default     -> continuous micro-batches every ``--interval`` seconds until
  interrupted.

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
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bigdata.config import ROOT as PROJECT_ROOT  # noqa: E402
from bigdata.engine.spark_engine import SparkEngine  # noqa: E402

DEFAULT_INBOX = PROJECT_ROOT / "data" / "streaming_inbox"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "exports" / "bigdata" / "streaming_state" / "spark_structured"


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


def _make_batch_writer(output_dir: Path):
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
        (output_dir / "aggregate.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps({"batch_id": batch_id, "groups": len(aggregate),
                          "total_documents": payload["total_documents"]}), flush=True)

    return write_batch


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description="Spark Structured Streaming corpus auto-updater.")
    parser.add_argument("--inbox", default=str(DEFAULT_INBOX), help="Directory streamed as the JSONL file source.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--checkpoint", default=None, help="Checkpoint dir (default: ASCII temp).")
    parser.add_argument("--master", default=None)
    parser.add_argument("--once", action="store_true", help="Process available files once (Trigger.AvailableNow) and stop.")
    parser.add_argument("--interval", type=float, default=10.0, help="Micro-batch interval seconds (continuous mode).")
    parser.add_argument("--max-files-per-trigger", type=int, default=100)
    args = parser.parse_args(argv)

    inbox = Path(args.inbox)
    inbox.mkdir(parents=True, exist_ok=True)
    output_dir = Path(args.output_dir)
    checkpoint = args.checkpoint or str(Path(tempfile.gettempdir()) / "finportfolio_spark" / "ss_checkpoint")

    engine = SparkEngine(master=args.master, app_name="finportfolio-ir-structured-streaming")
    spark = engine.spark
    from pyspark.sql import functions as F

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
