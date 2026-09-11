"""Per-document corpus analytics as native Spark SQL, exact to the reference.

The reference path (:func:`bigdata.jobs.corpus_analytics.raw_metrics` over an
RDD) ships every document through a Python worker: four Spark jobs and dozens of
Python tasks per micro-batch, a fixed toll of seconds even for 12 documents. Here
the per-document work -- JSON parsing, field defaults, source family, token count --
runs as Catalyst expressions in ONE scan inside the JVM. The driver receives one
small row per document (a few strings and three short arrays) and aggregates them
with the reference :func:`emit_metrics`, so counting, de-duplication and report
shaping are the reference code, not a re-implementation.

Build the extraction ONCE, into the streaming query (``extract(readStream...)``),
not inside ``foreachBatch``: constructing and analysing this expression tree costs
~0.7 s, which the streaming engine then pays once per query instead of once per
micro-batch (each batch only re-optimises the resolved plan, ~40 ms).

:func:`classify` (the parse and the exactness verdict) and :func:`indexing_text`
are shared with :mod:`bigdata.jobs.sql_inverted_index`, the BM25 build.

Exactness contract
------------------
For every line :func:`extract` returns one row whose :func:`route` is

* ``fast``     -- the fields :func:`analytics_doc` would return, computed in SQL;
* ``drop``     -- ``analytics_doc`` certainly returns ``None`` (blank line; a valid
  JSON object without ``published_at``);
* ``fallback`` -- the raw line, which the driver hands to ``analytics_doc`` itself
  (Python's behaviour, crashes included, is preserved verbatim).

The fast path is taken only when every assumption the SQL relies on is checked:

* the line is exactly ONE JSON object, starting at its first character, nested
  less than ~900 deep. Jackson's leniencies (single quotes, NaN/Infinity) are off;
  Jackson stops after the first value where Python's ``json.loads`` rejects
  trailing content, so two cheap Jackson passes over wrapped copies of the line
  prove there is none (see ``one_object`` below). Numbers over 1000 digits are
  refused by Jackson itself and go to Python, which refuses those over 4300;
* no schema field is JSON ``null``: absent and null differ in ``from_dict``
  (``str(None) == "None"``), and the key list tells them apart;
* every string the port reads was a JSON *string*: Spark renders any other JSON
  value into a string column as JSON text (``true``, ``5.1``, ``{"k":1}``), which
  would disagree with Python's ``str()``; such text is recognised and routed out;
* timestamps are canonical UTC ``YYYY-MM-DDTHH:MM:SSZ`` and real calendar dates
  (Python's ``to_utc_iso`` is then the identity, and the year is the prefix);
* tickers and the source-family inputs are ASCII (Python and Java case mapping
  agree on ASCII by definition), numbers are finite, ``evidence_unit_index`` is an
  integer of at most 640 digits (Python's ``int()`` would otherwise drop or crash);
* digits are Python's: the Java patterns spell out ``str.isdecimal``'s set instead
  of trusting the JDK's Unicode tables;
* no surrogate escape: Spark cannot hold an unpaired surrogate (it becomes ``?``),
  and Python's hashing drops such a record.

Everything else is rare in practice (0 of 26,368 documents in the PPO corpus) and
costs only speed, never correctness. ``deploy/spark_cluster/sql_analytics_parity.py``
checks the contract field by field on a whole corpus; ``tests/test_sql_analytics.py``
pins every routing rule with hand-made edge cases.

Speed note: a live batch usually holds one very long document (up to 700 KB),
and every pass over that line runs inside a single task, so the guards avoid
full-line regex scans; only the token count, the intrinsic work, reads it by regex.
"""

from __future__ import annotations

import sys
from functools import lru_cache
from typing import Iterable

from ..jobs.corpus_analytics import _nonempty_max, _nonempty_min, analytics_doc, emit_metrics

# finportfolio_ir.text_utils.TOKEN_RE, verbatim (a test pins the equality).
# tokenize() also drops tokens that strip(".$'") to nothing -- impossible here,
# every match starts with a letter or a digit -- so the count is the match count.
TOKEN_PATTERN = r"[A-Za-z][A-Za-z0-9_.$'-]*|\d+(?:\.\d+)?"


def _decimal_ranges() -> str:
    """Python's ``\\d`` beyond ASCII (``str.isdecimal``) as Java class ranges.

    Java's ``\\d`` (under UNICODE_CHARACTER_CLASS) follows the JDK's Unicode
    version, Python's the interpreter's; they agree only by coincidence (Java 17
    and Python 3.10 are both Unicode 13). Spelling the set out makes Java match
    exactly what this Python matches.
    """

    codes = [i for i in range(0x80, sys.maxunicode + 1) if chr(i).isdecimal()]
    ranges, start = [], codes[0]
    for a, b in zip(codes, codes[1:] + [None]):
        if b != a + 1:
            ranges.append(f"\\x{{{start:X}}}-\\x{{{a:X}}}")
            start = b
    return "".join(ranges)


_WIDE_DIGITS = _decimal_ranges()
# Single character classes, so a run is a plain loop (no regex recursion). The
# non-ASCII part is intersected with the non-ASCII range, so an ASCII character is
# decided by the cheap ASCII part alone.
DIGIT = "[0-9[\\x{80}-\\x{10FFFF}&&[" + _WIDE_DIGITS + "]]]"
NON_TOKEN_START = "[[\\x{0}-\\x{7F}&&[^A-Za-z0-9]][\\x{80}-\\x{10FFFF}&&[^" + _WIDE_DIGITS + "]]]"
# TOKEN_PATTERN for Java: the same pattern with the digit class spelled out.
TOKEN = TOKEN_PATTERN.replace(r"\d", DIGIT)
# Tokens are counted in one regex pass that leaves ~one character per token:
# each token is matched TOGETHER with the characters after it that cannot start a
# token (a token starts only at an ASCII letter or a Unicode digit), and the whole
# match becomes one SENTINEL. Those characters could never begin a TOKEN match, so
# the matches -- and their number -- are exactly TOKEN's. The text is prefixed
# with "a " so nothing precedes the first match; the result is count + 1 SENTINELs.
# (Materialising the tokens instead would build ~900K strings per 200 documents.)
TOKEN_COUNT_PATTERN = "(?:" + TOKEN + ")" + NON_TOKEN_START + "*"
SENTINEL = chr(1)

_STRINGS = (
    "doc_id", "title", "body", "source", "source_type", "url", "canonical_url", "event_type",
    "source_reliability_tier", "language", "published_at", "first_seen_at", "available_at",
    "ingested_at", "last_url_check_at", "evidence_unit_index",
)
# Every list from_dict iterates: a non-list there is a Python crash or a
# character-by-character iteration, so all of them are type-checked, not just the
# six the metrics read.
_LISTS = (
    "tickers_detected", "matched_tickers", "matched_holdings", "company_names_detected",
    "sectors_detected", "sector_tags", "event_tags", "risk_terms",
)
_NUMBERS = (
    "source_credibility", "sentiment_score", "uncertainty_score", "source_authority_score",
    "source_timeliness_score", "source_legal_liability_score", "source_numeric_density_score",
    "source_promotion_risk_score",
)
SCHEMA = ", ".join(
    [f"{name} string" for name in _STRINGS]
    + [f"{name} array<string>" for name in _LISTS]
    + [f"{name} double" for name in _NUMBERS]
    + ["_corrupt_record string"]
)
JSON_OPTIONS = {
    "mode": "PERMISSIVE",
    "columnNameOfCorruptRecord": "_corrupt_record",
    "allowSingleQuotes": "false",        # Spark's default is true; json.loads rejects them
    "allowNonNumericNumbers": "false",   # NaN / Infinity lines go to Python, which accepts them
}

# What Spark writes into a string column for a JSON value that is NOT a string:
# numbers (re-serialised: 1e5 -> 100000.0, 1e400 -> "Infinity"), booleans, and
# compact objects / arrays. A genuine string that looks like one is routed out too.
NON_STRING_TEXT = (
    r'(?s)^(?:-?[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?|true|false'
    r'|"?-?Infinity"?|"?NaN"?|\{.*\}|\[.*\])$'
)
# ...and every such text starts with one of these. Checking the first character
# before the regex keeps rlike's find() from probing every offset of a long body.
NON_STRING_FIRST = list('-0123456789tf"{[IN')
SURROGATE_ESCAPE = r"\\u[dD][89a-fA-F]"
CANONICAL_TS = (
    r"^(?!0000)[0-9]{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])"
    r"T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]Z$"
)
ASCII = r"^[\x00-\x7F]*$"
JSON_SPACE = [" ", "\t", "\n", "\r"]
JSON_SPACE_ONLY = r"^[ \t\n\r]*$"
# Jackson refuses nesting beyond 1000 levels. Wrapping the line in this many more
# keeps the fast path below ~900, where Python's recursive json.loads never fails.
_DEPTH_MARGIN = 100


@lru_cache(maxsize=1)
def _nonblank() -> str:
    """Java class matching one character that Python's ``str.strip()`` keeps."""

    spaces = (c for c in map(chr, range(sys.maxunicode + 1)) if c.isspace())
    return "[^" + "".join(f"\\x{{{ord(c):X}}}" for c in spaces) + "]"


def field(name):
    """A field of the parsed record ``j`` (valid on :func:`classify`'s output)."""

    from pyspark.sql import functions as F

    return F.col("j." + name)


def _empty():
    from pyspark.sql import functions as F

    return F.array().cast("array<string>")


def _token_count(text):
    """Number of TOKEN matches in ``text``: one regex pass, see TOKEN_COUNT_PATTERN."""

    from pyspark.sql import functions as F

    marks = F.regexp_replace(F.concat(F.lit("a "), text), TOKEN_COUNT_PATTERN, SENTINEL)
    return F.length(marks) - 1


def indexing_text():
    """``FinancialDocument.text_for_indexing()`` over ``j`` (meaningful on fast rows).

    ``f"{title} {body} {' '.join(TD + CN + SD)} {event_type}"`` with the upper-cased
    ``tickers_detected`` -- not ``matched_tickers`` -- as ``from_dict`` builds them.
    """

    from pyspark.sql import functions as F

    empty = _empty()
    tickers_up = F.transform(F.coalesce(field("tickers_detected"), empty), F.upper)
    return F.concat_ws(
        " ",
        F.coalesce(field("title"), F.lit("")),
        F.coalesce(field("body"), F.lit("")),
        F.array_join(F.concat(tickers_up, F.coalesce(field("company_names_detected"), empty),
                              F.coalesce(field("sectors_detected"), empty)), " "),
        F.coalesce(field("event_type"), F.lit("")),
    )


def classify(lines_df):
    """``value`` lines -> ``value, j, fast, drop, available``: the parse and the verdict.

    ``j`` is the parsed record, ``fast`` the exactness verdict, ``drop`` a certain
    drop, ``available`` the effective ``available_at``. One row per line, no Filter
    (see below). Works on a batch or a streaming DataFrame.
    """

    from pyspark.sql import functions as F

    def json_text(column):                   # could be Spark's rendering of a non-string
        return F.substring(column, 1, 1).isin(NON_STRING_FIRST) & column.rlike(NON_STRING_TEXT)

    def plain(column):                       # absent, or a genuine JSON string
        return column.isNull() | ~json_text(column)

    def plain_items(column):                 # absent, or strings only (no nulls)
        return column.isNull() | ~F.exists(column, lambda x: x.isNull() | json_text(x))

    def ascii_text(column):
        return column.isNull() | column.rlike(ASCII)

    def ascii_items(column):
        return column.isNull() | ~F.exists(column, lambda x: ~x.rlike(ASCII))

    def blank(column):                        # absent or "" -- Python's falsy for a str
        return column.isNull() | (column == "")

    def valid_ts(column):
        year = F.substring(column, 1, 4).cast("int")
        month = F.substring(column, 6, 2).cast("int")
        day = F.substring(column, 9, 2).cast("int")
        last_day = F.dayofmonth(F.last_day(F.make_date(year, month, F.lit(1))))
        # CASE WHEN is lazy: the casts only ever see a string the regex has vetted.
        return F.when(column.rlike(CANONICAL_TS), day <= last_day).otherwise(F.lit(False))

    value = F.col("value")

    # One Jackson parse into the schema, one skip-parse for the key list. Both are
    # materialised here so the many references below reuse them.
    parsed = lines_df.select(
        value,
        F.from_json(value, SCHEMA, JSON_OPTIONS).alias("j"),
        F.json_object_keys(value).alias("keys"),
    )

    # Exactly one value, nothing after it. Jackson ignores what follows the root,
    # so the line is parsed twice more, wrapped: as the only element of an array
    # (fails on trailing content unless it starts with ']' or is a further element)
    # and as the innermost value of nested objects (fails on trailing ']'). Only
    # trailing whitespace survives both. The object nesting doubles as the depth
    # bound. Both are skip-parses: no strings are copied.
    as_element = F.json_array_length(F.concat(F.lit("["), value, F.lit("]"))) == 1
    as_member = F.json_object_keys(F.concat(
        F.repeat(F.lit('{"a":'), _DEPTH_MARGIN), value, F.repeat(F.lit("}"), _DEPTH_MARGIN))).isNotNull()
    # A line with leading whitespace is valid JSON too, but it goes to Python:
    # testing the first character is free, an anchored regex is not.
    one_object = (
        F.col("j").isNotNull() & field("_corrupt_record").isNull() & value.startswith("{")
        & as_element & as_member
    )
    surrogate = F.when(F.instr(value, "\\u") > 0, value.rlike(SURROGATE_ESCAPE)).otherwise(F.lit(False))

    published = field("published_at")
    available = F.when(blank(field("available_at")), published).otherwise(field("available_at"))
    first_seen = F.when(blank(field("first_seen_at")), available).otherwise(field("first_seen_at"))
    ingested = F.when(blank(field("ingested_at")), available).otherwise(field("ingested_at"))
    url_check = field("last_url_check_at")

    fast = one_object & ~surrogate & ~blank(published) & field("doc_id").isNotNull()
    for name in _STRINGS + _LISTS + _NUMBERS:  # present but null: Python's str(None) etc.
        fast = fast & ~(field(name).isNull() & F.array_contains(F.col("keys"), name))
    fast = fast & valid_ts(published) & valid_ts(available) & valid_ts(first_seen) & valid_ts(ingested)
    fast = fast & (blank(url_check) | valid_ts(url_check))
    # doc_id keys the BM25 document lengths: str(doc_id) must be the JSON string.
    for name in ("doc_id", "title", "body", "source", "source_type", "url", "canonical_url", "event_type",
                 "source_reliability_tier", "language"):
        fast = fast & plain(field(name))
    for name in ("tickers_detected", "matched_tickers", "company_names_detected",
                 "sectors_detected", "event_tags", "risk_terms"):
        fast = fast & plain_items(field(name))
    for name in ("source", "source_type", "url", "canonical_url"):
        fast = fast & ascii_text(field(name))
    fast = fast & ascii_items(field("tickers_detected")) & ascii_items(field("matched_tickers"))
    for name in _NUMBERS:
        fast = fast & (field(name).isNull() | (~F.isnan(field(name)) & (F.abs(field(name)) != float("inf"))))
    # int() refuses digit strings beyond sys.get_int_max_str_digits() (4300 by
    # default, never below 640): a longer one goes to Python, whatever the setting.
    fast = fast & (field("evidence_unit_index").isNull()
                   | field("evidence_unit_index").rlike(r"^-?[0-9]{1,640}$"))
    # Blank: only a line that starts with JSON whitespace (or is empty) can be one.
    blank_line = (value == "") | (F.substring(value, 1, 1).isin(JSON_SPACE) & value.rlike(JSON_SPACE_ONLY))
    # published_at is the first thing from_dict reads: absent, null or "" drops.
    drop = blank_line | (one_object & blank(published))

    # This stage materialises the parse and the verdict; callers derive fields in
    # the next. Each is referenced many times, so Catalyst keeps the projections
    # apart (CollapseProject never duplicates a non-cheap expression): one parse
    # per line. No Filter on purpose: a predicate on the verdict would be pushed
    # below the parse and re-run from_json once per reference. Dropped lines come
    # back as rows too, and cost nothing downstream.
    return parsed.select(value, "j", fast.alias("fast"), drop.alias("drop"), available.alias("available"))


def extract(lines_df, *, with_doc_id: bool = False):
    """``value`` lines -> exactly one row per line; see :func:`route`.

    Works on a batch or a streaming DataFrame; for a stream, call it once on
    ``readStream`` and hand the result to ``foreachBatch``.
    """

    from pyspark.sql import functions as F

    empty = _empty()
    nonblank = _nonblank()
    value = F.col("value")
    flagged = classify(lines_df)

    source = F.coalesce(field("source"), F.lit(""))
    source_type = field("source_type")
    blank_canonical = field("canonical_url").isNull() | (field("canonical_url") == "")
    url = F.lower(F.when(blank_canonical, F.coalesce(field("url"), F.lit(""))).otherwise(field("canonical_url")))
    type_lower = F.lower(F.coalesce(source_type, F.lit("")))
    family = (
        F.when(type_lower.startswith("official_macro") | url.contains("fred.stlouisfed.org"), "official_macro")
        .when(type_lower.startswith("sec_filing") | url.contains("sec.gov") | F.lower(source).contains("edgar"),
              "sec_edgar")
        .when(type_lower.startswith("company_"), "company_ir")
        .when(type_lower == "sample", "sample")
        .otherwise("other")
    )
    type_out = (
        F.when(source_type.isNull(), F.when(source.startswith("sample"), "sample").otherwise("unknown"))
        .when(source_type == "", "unknown")
        .otherwise(source_type)
    )
    tier_field = field("source_reliability_tier")
    tier = F.when(tier_field.isNull() | (tier_field == ""), "unknown").otherwise(tier_field)
    language = F.when(field("language").isNull(), "en").when(field("language") == "", "unknown").otherwise(
        field("language"))

    tickers_up = F.transform(F.coalesce(field("tickers_detected"), empty), F.upper)
    matched = F.when(field("matched_tickers").isNull(), tickers_up).otherwise(
        F.transform(field("matched_tickers"), F.upper))
    event_type = F.coalesce(field("event_type"), F.lit(""))
    events = (
        F.when(field("event_tags").isNotNull(), field("event_tags"))
        .when(event_type != "", F.array(event_type))
        .otherwise(empty)
    )
    tokens = _token_count(indexing_text())
    credibility = F.coalesce(field("source_credibility"), F.lit(0.5))

    def kept(items):                           # analytics_doc: `if str(x).strip()`
        return F.filter(items, lambda x: x.rlike(nonblank))

    fast_col = F.col("fast")
    columns = [
        F.when(~fast_col & ~F.col("drop"), value).alias("raw"),
        F.when(fast_col, family).alias("family"),
        F.when(fast_col, type_out).alias("source_type"),
        F.when(fast_col, tier).alias("tier"),
        F.when(fast_col, F.substring(F.col("available"), 1, 4)).alias("year"),
        F.when(fast_col, language).alias("lang"),
        F.when(fast_col, kept(matched)).alias("tickers"),
        F.when(fast_col, kept(events)).alias("events"),
        F.when(fast_col, kept(F.coalesce(field("risk_terms"), empty))).alias("risks"),
        # The only heavy expression; CASE WHEN keeps fallback rows from paying it.
        F.when(fast_col, tokens).alias("length"),
        F.when(fast_col, F.when(credibility == 0, F.lit(0.0)).otherwise(credibility)).alias("credibility"),
        F.when(fast_col, F.col("available")).alias("available_at"),
    ]
    if with_doc_id:
        columns.append(F.when(fast_col, field("doc_id")).alias("doc_id"))
    return flagged.select(*columns)


def route(row) -> str:
    """``fast`` | ``fallback`` | ``drop`` for one row of :func:`extract`."""

    if row["raw"] is not None:
        return "fallback"
    return "drop" if row["family"] is None else "fast"


def row_meta(row) -> dict:
    """A fast-path row as the dict :func:`analytics_doc` returns."""

    return {
        "family": row["family"],
        "source_type": row["source_type"],
        "tier": row["tier"],
        "year": row["year"],
        "lang": row["lang"],
        "tickers": list(row["tickers"]),
        "events": list(row["events"]),
        "risks": list(row["risks"]),
        "length": int(row["length"]),
        "credibility": float(row["credibility"]),
        "available_at": row["available_at"],
    }


def aggregate(rows: Iterable) -> tuple:
    """Extracted rows -> ``(metrics, min_available, max_available, documents, fallback)``.

    The first four are exactly what :func:`corpus_analytics.raw_metrics` returns
    for the same lines; ``fallback`` counts the rows processed by ``analytics_doc``.
    """

    metrics: dict = {}
    min_available = max_available = ""
    documents = fallback = 0
    for row in rows:
        taken = route(row)
        if taken == "drop":
            continue
        if taken == "fallback":
            fallback += 1
            meta = analytics_doc(row["raw"])
            if meta is None:
                continue
        else:
            meta = row_meta(row)
        documents += 1
        for key, amount in emit_metrics(meta):
            metrics[key] = metrics.get(key, 0) + amount
        min_available = _nonempty_min(min_available, meta["available_at"])
        max_available = _nonempty_max(max_available, meta["available_at"])
    return metrics, min_available, max_available, documents, fallback
