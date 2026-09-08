"""The inverted-index job written against Spark's DataFrame/SQL API.

Why this exists
---------------
The RDD path (:mod:`bigdata.run_inverted_index`) ships a Python function to every
task, so on Windows -- which has no ``fork`` -- Spark must start a fresh
``python.exe`` per task. That, not "Windows is slow", is what makes the RDD path
expensive there (see ``docs/BIG_DATA_INFRASTRUCTURE.md`` section 11).

Expressing the same job in DataFrame/SQL removes the Python worker entirely: the
tokenisation, the shuffle and the aggregation all execute inside the JVM. This
module exists so that claim is **reproducible** rather than merely asserted, and
so the timing comparison in the report can be re-run on demand.

It is deliberately NOT the production path. Its tokeniser is written in SQL, not
``finportfolio_ir.text_utils.tokenize``, so its vocabulary differs from the
reference index -- and by a lot, in either direction depending on which SQL
tokenizer is chosen (see :data:`TOKENIZERS`). It demonstrates the mechanism; the
RDD path is what is verified byte-identical to ``indexing/build_sparse_index.py``.

Requires PySpark, which the project pins to 3.5.x -- on this machine that lives
in the ``tensorflow`` conda environment, not the default interpreter.

Examples
--------
    python -m bigdata.run_sql_inverted_index --corpus macro --partitions 4
    python -m bigdata.run_sql_inverted_index --corpus macro --partitions 12
    python -m bigdata.run_sql_inverted_index --corpus macro --tokenizer alnum
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bigdata.config import NAMED_CORPORA, resolve_corpus  # noqa: E402
from bigdata.engine.spark_engine import SparkEngine, _ascii_scratch_dir  # noqa: E402

#: Two SQL tokenizers, neither of which is the project tokenizer.
#: ``whitespace`` keeps "2010-01-01" and "217.587" whole, so on the macro corpus
#: -- where every document carries a unique date and value -- the vocabulary
#: balloons to ~18k. ``alnum`` splits those apart and collapses it to ~900.
#: The gap between them is itself the point: tokenisation, not scale, decides
#: the vocabulary, which is why the production path reuses one shared tokenizer.
TOKENIZERS = {
    "whitespace": None,          # split on runs of whitespace
    "alnum": "[^A-Za-z0-9]+",    # every non-alphanumeric run is a separator
}


def _readable_by_spark(corpus_path: Path) -> Path:
    """Return a path Spark's Hadoop layer can actually open.

    The repository lives under a Cyrillic OneDrive directory, which Hadoop's
    local ``Path`` parser mishandles on Windows. The RDD path sidesteps this by
    reading driver-side, but a DataFrame read has to go through Hadoop -- so a
    non-ASCII corpus is staged into an ASCII scratch directory first. The copy
    happens outside the timed region.
    """

    if corpus_path.as_posix().isascii():
        return corpus_path
    staged = Path(_ascii_scratch_dir()) / "corpora" / corpus_path.name
    staged.parent.mkdir(parents=True, exist_ok=True)
    if not staged.exists() or staged.stat().st_size != corpus_path.stat().st_size:
        print(f"staging corpus into an ASCII path: {staged}")
        shutil.copyfile(corpus_path, staged)
    return staged


def build_frames(spark, corpus_uri: str, partitions: int, tokenizer: str = "whitespace"):
    """Return ``(document_frequency, document_length)`` DataFrames.

    Mirrors the RDD job: a term contributes to a document's *length* every time
    it occurs, but to a term's *document frequency* only once per document.
    """

    from pyspark.sql import functions as F

    lines = spark.read.text(corpus_uri).repartition(partitions)
    documents = lines.select(
        F.get_json_object("value", "$.doc_id").alias("doc_id"),
        F.concat_ws(
            " ",
            F.coalesce(F.get_json_object("value", "$.title"), F.lit("")),
            F.coalesce(F.get_json_object("value", "$.body"), F.lit("")),
        ).alias("text"),
    )
    separators = TOKENIZERS[tokenizer]
    if separators is None:
        split_column = F.split(F.lower(F.col("text")), r"\s+")
    else:
        split_column = F.split(F.lower(F.regexp_replace("text", separators, " ")), " +")
    terms = documents.select(
        "doc_id", F.explode(split_column).alias("term")
    ).filter(F.col("term") != "")

    document_frequency = terms.select("doc_id", "term").distinct().groupBy("term").count()
    document_length = terms.groupBy("doc_id").count()
    return document_frequency, document_length


def run_once(spark, corpus_uri: str, partitions: int, tokenizer: str) -> dict:
    """Execute the job and return its statistics plus wall-clock seconds."""

    from pyspark.sql import functions as F

    started = time.perf_counter()
    document_frequency, document_length = build_frames(
        spark, corpus_uri, partitions, tokenizer
    )
    vocabulary = document_frequency.count()
    totals = document_length.agg(
        F.count("*").alias("n_docs"), F.sum("count").alias("total_tokens")
    ).first()
    elapsed = time.perf_counter() - started

    n_docs = int(totals["n_docs"] or 0)
    total_tokens = int(totals["total_tokens"] or 0)
    return {
        "seconds": round(elapsed, 2),
        "vocabulary": vocabulary,
        "n_docs": n_docs,
        "total_tokens": total_tokens,
        "avgdl": round(total_tokens / n_docs, 4) if n_docs else 0.0,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--corpus", default="repo_demo",
        help=f"Named corpus or path. Named: {', '.join(sorted(NAMED_CORPORA))}.",
    )
    parser.add_argument("--master", default="local[*]",
                        help="Spark master URL (local[*] or spark://host:7077).")
    parser.add_argument(
        "--engine", choices=("spark",), default="spark",
        help="Accepted for symmetry with the other bigdata CLIs (and so "
             "deploy/spark_cluster/submit.ps1 can forward its arguments "
             "unchanged). DataFrame/SQL only runs on Spark.",
    )
    parser.add_argument("--partitions", type=int, default=4,
                        help="Shuffle/read partitions to use.")
    parser.add_argument(
        "--tokenizer", choices=sorted(TOKENIZERS), default="whitespace",
        help="whitespace: split on whitespace only. alnum: also split on "
             "punctuation and digits' separators. Neither is the project tokenizer.",
    )
    parser.add_argument(
        "--repeat", type=int, default=2,
        help="Runs to perform. The FIRST is discarded as JVM/JIT warm-up, so the "
             "default of 2 reports one clean measurement.",
    )
    args = parser.parse_args(argv)

    corpus_path = resolve_corpus(args.corpus)
    if not corpus_path.exists():
        parser.error(f"Corpus not found: {corpus_path}")
    if args.repeat < 1:
        parser.error("--repeat must be at least 1")

    corpus_uri = _readable_by_spark(corpus_path).as_uri()
    engine = SparkEngine(
        master=args.master,
        app_name="finportfolio-ir-bigdata-sql",
        extra_conf={"spark.sql.shuffle.partitions": str(args.partitions)},
    )
    try:
        runs = []
        for attempt in range(args.repeat):
            result = run_once(engine.spark, corpus_uri, args.partitions, args.tokenizer)
            label = "warm-up (discarded)" if attempt == 0 and args.repeat > 1 else "measured"
            print(f"  run {attempt + 1}/{args.repeat}: {result['seconds']:>6.2f} s  [{label}]")
            runs.append(result)
    finally:
        engine.stop()

    measured = runs[1:] if len(runs) > 1 else runs
    best = min(measured, key=lambda r: r["seconds"])
    summary = {
        "api": "dataframe_sql",
        "tokenizer": args.tokenizer,
        "master": args.master,
        "partitions": args.partitions,
        "corpus": str(corpus_path),
        "seconds": best["seconds"],
        "all_measured_seconds": [r["seconds"] for r in measured],
        "vocabulary": best["vocabulary"],
        "n_docs": best["n_docs"],
        "total_tokens": best["total_tokens"],
        "avgdl": best["avgdl"],
        "note": (
            f"SQL {args.tokenizer} tokenizer, not finportfolio_ir.text_utils.tokenize "
            "-- the vocabulary therefore differs from the reference index."
        ),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
