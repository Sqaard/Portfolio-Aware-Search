"""BM25 inverted-index statistics as native Spark SQL, exact to the reference.

The RDD job (:func:`bigdata.jobs.inverted_index.build_bm25_index`) hands every
line to a Python worker: parse, ``FinancialDocument``, ``tokenize``, count. Here
the same computation is one Catalyst job with no Python worker at all:

    scan -> from_json + exactness verdict        (sql_analytics.classify)
         -> tokens = regexp_extract_all(lower(text_for_indexing), LOWER_TOKEN)
         -> per document: its DISTINCT terms, one length key, one document key
         -> explode -> groupBy(key).sum -> collect (~vocabulary + documents rows)

and the driver assembles exactly the artifact the RDD job returns. Records the
SQL cannot reproduce exactly are carried through the same aggregation as their
raw line and handed to the reference :func:`doc_term_counts` on the driver.

Exactness of the tokens. ``tokenize`` is ``[t.lower().strip(".$'") for t in
TOKEN_RE.findall(text) if t.strip(".$'")]``; the port avoids any per-token work:

* Lower-casing the whole text first finds the same tokens, because every token
  character lower-cases to a token character and no other character does -- with
  a handful of exceptions (``İ`` -> ``i̇``, the Kelvin sign -> ``k``, computed below
  from Python's own ``str.lower``). A document containing one keeps the per-token
  path: extract from the original text, then lower-case each token.
* ``strip(".$'")`` only ever removes trailing characters (a token starts with a
  letter or a digit), and a trailing run of ``.$'`` cannot start the next token.
  So ``[a-z](?:[a-z0-9_.$'-]*[a-z0-9_-])?`` -- the letter branch that backs off any
  trailing ``.$'`` -- matches exactly the stripped tokens, at the same places. Digit
  tokens end with a digit and are unchanged. No token is ever empty.
* Digits are Python's ``str.isdecimal`` set, spelled out (``sql_analytics.DIGIT``).

Like the RDD job, the statistics are those of ``BM25Index`` for a corpus of unique
``doc_id``\\s. A repeated ``doc_id`` makes the RDD job keep an arbitrary one of the
lengths (``collectAsMap``); this job sums them and reports ``duplicate_doc_ids``.
"""

from __future__ import annotations

import math
import shutil
import string
import sys
from functools import lru_cache
from pathlib import Path
from typing import Optional

from ..config import BM25_B, BM25_K1
from ..engine.spark_engine import _as_uri, _ascii_scratch_dir
from ..streaming import sql_analytics as S
from .inverted_index import doc_term_counts

# Aggregation keys. A term starts with a letter or a digit, so these never collide.
LENGTH_KEY = chr(0) + "len" + chr(0)     # + doc_id  -> the document's length
DOCUMENTS_KEY = chr(0) + "docs"          #           -> 1 per kept document
RAW_KEY = chr(1)                         # + line    -> a line for the Python reference

# TOKEN on lower-cased text, with tokenize()'s trailing strip folded in (Python
# syntax; a test fuzzes it against tokenize), and its Java form.
LOWER_TOKEN_PATTERN = r"[A-Za-z](?:[A-Za-z0-9_.$'-]*[A-Za-z0-9_-])?|\d+(?:\.\d+)?"
LOWER_TOKEN = LOWER_TOKEN_PATTERN.replace(r"\d", S.DIGIT)

_TOKEN_CHARS = set(string.ascii_letters + string.digits + "_.$'-")


def _is_token_char(c: str) -> bool:
    return c in _TOKEN_CHARS or c.isdecimal()


@lru_cache(maxsize=1)
def case_hazards() -> tuple:
    """Characters that are not token characters but lower-case into one."""

    return tuple(c for c in map(chr, range(sys.maxunicode + 1))
                 if not _is_token_char(c) and any(_is_token_char(x) for x in c.lower()))


def token_array(text):
    """``tokenize(text)`` as a Spark array expression (see the module docstring)."""

    from pyspark.sql import functions as F

    hazard = None
    for c in case_hazards():
        found = F.instr(text, c) > 0
        hazard = found if hazard is None else hazard | found
    per_token = F.transform(F.regexp_extract_all(text, F.lit(S.TOKEN), F.lit(0)),
                            lambda t: F.regexp_replace(F.lower(t), "[.$']+$", ""))
    whole_text = F.regexp_extract_all(F.lower(text), F.lit(LOWER_TOKEN), F.lit(0))
    return whole_text if hazard is None else F.when(hazard, per_token).otherwise(whole_text)


def entries(lines_df):
    """``value`` lines -> ``(k, n)`` rows: each document's contribution, pre-aggregation."""

    from pyspark.sql import functions as F

    flagged = S.classify(lines_df)
    fast, drop = F.col("fast"), F.col("drop")
    # Each stage materialises what the next reads more than once.
    texts = flagged.select(
        F.when(~fast & ~drop, F.col("value")).alias("raw"),
        F.when(fast, S.field("doc_id")).alias("doc_id"),
        F.when(fast, S.indexing_text()).alias("text"),
    )
    tokens = texts.select("raw", "doc_id", F.when(F.col("text").isNotNull(), token_array(F.col("text")))
                          .alias("tokens"))
    keys = (
        F.when(F.col("doc_id").isNotNull(), F.concat(
            F.array_distinct(F.col("tokens")),
            F.array(F.concat(F.lit(LENGTH_KEY), F.col("doc_id")), F.lit(DOCUMENTS_KEY)),
        ))
        .when(F.col("raw").isNotNull(), F.array(F.concat(F.lit(RAW_KEY), F.col("raw"))))
    )
    exploded = tokens.select(F.explode(keys).alias("k"), F.size(F.col("tokens")).alias("length"))
    amount = F.when(F.col("k").startswith(LENGTH_KEY), F.col("length")).otherwise(F.lit(1)).cast("long")
    return exploded.select("k", amount.alias("n"))


def build_bm25_index(lines_df, *, top_terms: int = 50) -> dict:
    """The artifact of :func:`inverted_index.build_bm25_index`, from a ``value`` DataFrame."""

    from pyspark.sql import functions as F

    rows = entries(lines_df).groupBy("k").agg(F.sum("n").alias("n"), F.count("*").alias("c")).collect()

    document_frequencies: dict = {}
    document_lengths: dict = {}
    n_docs = duplicates = fallback = 0
    for key, amount, occurrences in rows:
        if key.startswith(RAW_KEY):
            for _ in range(int(occurrences)):
                fallback += 1
                entry = doc_term_counts(key[len(RAW_KEY):])
                if entry is None:
                    continue
                doc_id, counts, length = entry
                n_docs += 1
                duplicates += doc_id in document_lengths
                document_lengths[doc_id] = document_lengths.get(doc_id, 0) + length
                for term in counts:
                    document_frequencies[term] = document_frequencies.get(term, 0) + 1
        elif key == DOCUMENTS_KEY:
            n_docs += int(amount)
        elif key.startswith(LENGTH_KEY):
            doc_id = key[len(LENGTH_KEY):]
            duplicates += int(occurrences) - 1 + (doc_id in document_lengths)
            document_lengths[doc_id] = document_lengths.get(doc_id, 0) + int(amount)
        else:
            document_frequencies[key] = document_frequencies.get(key, 0) + int(amount)

    unique_docs = len(document_lengths)
    total_tokens = sum(document_lengths.values())
    avgdl = (total_tokens / unique_docs) if unique_docs else 0.0
    leaders = sorted(document_frequencies.items(), key=lambda kv: (-kv[1], kv[0]))[:top_terms]
    return {
        "k1": BM25_K1,
        "b": BM25_B,
        "n_docs": n_docs,
        "unique_doc_ids": unique_docs,
        "vocabulary_size": len(document_frequencies),
        "total_tokens": total_tokens,
        "average_document_length": avgdl,
        "document_frequencies": document_frequencies,
        "document_lengths": document_lengths,
        "top_terms_by_document_frequency": [
            {"term": term, "document_frequency": df} for term, df in leaders
        ],
        "sql": {"fallback_lines": fallback, "duplicate_doc_ids": duplicates},
    }


def read_lines(spark, corpus_path: Path, *, partitions: Optional[int], limit: int = 0):
    """The corpus as a ``value`` DataFrame split into ``partitions`` scan tasks.

    Hadoop cannot open the project's Cyrillic checkout path, so a non-ASCII corpus
    is staged into an ASCII scratch directory first (local runs only: executors on
    other nodes cannot see the driver's disk -- cluster corpora live on a shared,
    ASCII volume). ``limit`` > 0 stages the first N non-blank lines beside the
    corpus: the skeleton run that measures fixed overhead, mirroring the RDD path's
    ``--limit`` -- same plan, same task count, almost no data. Its name must not
    start with '.' or '_', which Spark's file source treats as hidden.

    With ``partitions`` the scan gets exactly that many tasks (file split size) and
    so does the shuffle; AQE's partition coalescing is switched off so the skeleton
    and the full run -- and every shape -- run the same 2 x ``partitions`` tasks.
    """

    path = Path(corpus_path).resolve()
    if not path.as_posix().isascii():
        staged = Path(_ascii_scratch_dir()) / "corpora" / path.name
        staged.parent.mkdir(parents=True, exist_ok=True)
        if not staged.exists() or staged.stat().st_size != path.stat().st_size:
            shutil.copyfile(path, staged)
        path = staged
    if limit and limit > 0:
        skeleton = path.parent / f"skeleton{limit}-{path.name}"
        kept = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    kept.append(line.rstrip("\n"))
                if len(kept) >= limit:
                    break
        skeleton.write_text("\n".join(kept) + "\n", encoding="utf-8")
        path = skeleton
    if partitions:
        split = max(math.ceil(path.stat().st_size / partitions), 1)
        spark.conf.set("spark.sql.files.maxPartitionBytes", str(split))
        spark.conf.set("spark.sql.shuffle.partitions", str(partitions))
        spark.conf.set("spark.sql.adaptive.coalescePartitions.enabled", "false")
    return spark.read.text(_as_uri(str(path)))
