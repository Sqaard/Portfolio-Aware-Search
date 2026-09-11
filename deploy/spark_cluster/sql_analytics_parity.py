"""Exactness check for the native Spark SQL analytics port, on a whole corpus.

Two levels:

1. per document -- every field ``bigdata.streaming.sql_analytics.extract`` derives
   in Catalyst on its fast path is compared with what the reference Python
   ``analytics_doc`` returns for the same line; the first mismatches of each field
   are printed. This pins down WHICH rule of the port is wrong. Rows routed to the
   Python fallback are exact by construction and only counted.
2. per report -- the corpus-analytics report built from the SQL rows with the
   reference ``emit_metrics`` must equal ``compute_analytics`` exactly.

Runs inside a Spark container:

    docker compose -f deploy/spark_cluster/docker-compose.yml exec -T spark-master \\
        python3 deploy/spark_cluster/sql_analytics_parity.py [corpus.jsonl]
"""

from __future__ import annotations

import json
import math
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT = ROOT / "data" / "processed_documents" / "sec_macro_company_ir_ppo_2010_2023_documents.jsonl"
FIELDS = ("family", "source_type", "tier", "year", "lang", "tickers", "events", "risks",
          "length", "credibility", "available_at")


def same(field: str, a, b) -> bool:
    if field == "credibility":
        return a == b and math.copysign(1.0, a) == math.copysign(1.0, b)
    return a == b


def main() -> int:
    corpus = (Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT).resolve()
    from pyspark.sql import SparkSession

    from bigdata.engine import LocalEngine
    from bigdata.jobs import corpus_analytics
    from bigdata.jobs.corpus_analytics import analytics_doc
    from bigdata.jobs.mapping import parse_json_line
    from bigdata.streaming import sql_analytics

    # The whole 364 MiB corpus is collected to the driver for the Python reference.
    spark = (SparkSession.builder.master("local[4]").appName("sql-analytics-parity")
             .config("spark.driver.memory", "6g").config("spark.ui.enabled", "false")
             .config("spark.sql.session.timeZone", "UTC").getOrCreate())
    spark.sparkContext.setLogLevel("ERROR")

    # The same lines the streaming job sees: Spark's text source.
    lines_df = spark.read.text(corpus.as_uri())
    lines = [r.value for r in lines_df.collect()]
    print(f"corpus {corpus.name}: {len(lines):,} lines")

    # --- reference, pure Python -----------------------------------------------------
    t = time.perf_counter()
    reference: dict = {}
    for line in lines:
        meta = analytics_doc(line)
        if meta is not None:
            reference.setdefault(str(parse_json_line(line)["doc_id"]), []).append(meta)
    print(f"python analytics_doc: {sum(map(len, reference.values())):,} docs kept, "
          f"{time.perf_counter() - t:.1f}s")

    # --- SQL port ---------------------------------------------------------------------
    extracted = sql_analytics.extract(lines_df, with_doc_id=True)
    plan = extracted._jdf.queryExecution().optimizedPlan().toString()
    print(f"optimized plan: from_json x{plan.count('from_json(')}, "
          f"regexp_extract_all x{plan.count('regexp_extract_all(')}")
    t = time.perf_counter()
    rows = extracted.collect()
    print(f"sql extract: {len(rows):,} rows, {time.perf_counter() - t:.1f}s")

    # --- level 1: per document -------------------------------------------------------------
    got: dict = {}
    routes = Counter()
    for row in rows:
        taken = sql_analytics.route(row)
        routes[taken] += 1
        if taken == "fallback":
            meta = analytics_doc(row["raw"])
            if meta is not None:
                got.setdefault(str(parse_json_line(row["raw"])["doc_id"]), []).append(meta)
        elif taken == "fast":
            got.setdefault(str(row["doc_id"]), []).append(sql_analytics.row_meta(row))
    print(f"routes: {dict(routes)}")

    only_ref = sorted(set(reference) - set(got))
    only_sql = sorted(set(got) - set(reference))
    print(f"doc ids only in python: {len(only_ref)}  only in sql: {len(only_sql)}")
    for name, ids in (("python-only", only_ref), ("sql-only", only_sql)):
        for doc_id in ids[:5]:
            print(f"   {name}: {doc_id!r}")

    mismatch = Counter()
    examples: dict = {}
    for doc_id in set(reference) & set(got):
        expected_list, got_list = reference[doc_id], got[doc_id]
        if len(expected_list) != len(got_list):
            mismatch["<multiplicity>"] += 1
            continue
        for expected, meta in zip(expected_list, got_list):
            for name in FIELDS:
                if not same(name, expected[name], meta[name]):
                    mismatch[name] += 1
                    examples.setdefault(name, []).append((doc_id, expected[name], meta[name]))
    if mismatch:
        print("PER-DOCUMENT MISMATCHES:")
        for name, count in mismatch.most_common():
            print(f"  {name}: {count}")
            for doc_id, a, b in examples.get(name, [])[:4]:
                print(f"     {doc_id[:60]!r}: python={str(a)[:100]!r}  sql={str(b)[:100]!r}")
    else:
        print("per document: every field of every document identical")

    # --- level 2: the report ----------------------------------------------------------------
    metrics, low, high, documents, _ = sql_analytics.aggregate(rows)
    streamed = corpus_analytics.finalize_report(metrics, low, high)
    engine = LocalEngine(num_workers=4)
    try:
        expected_report = corpus_analytics.compute_analytics(engine.parallelize(lines))
    finally:
        engine.stop()
    identical = streamed == expected_report
    if identical:
        print(f"report: IDENTICAL to compute_analytics ({documents:,} documents)")
    else:
        print("report: DIFFERS")
        for key in expected_report:
            if streamed.get(key) != expected_report[key]:
                print(f"  {key}: python={json.dumps(expected_report[key])[:300]}")
                print(f"  {' ' * len(key)}  sql   ={json.dumps(streamed.get(key))[:300]}")
    spark.stop()
    return 0 if identical and not mismatch and not only_ref and not only_sql else 1


if __name__ == "__main__":
    raise SystemExit(main())
