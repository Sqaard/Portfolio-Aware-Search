"""The native Spark SQL analytics port == ``analytics_doc``, record by record.

Every fixture is one JSONL line that exercises a rule of ``FinancialDocument.from_dict``
/ ``analytics_doc`` the SQL must reproduce, or an input it must hand to the Python
fallback. For each line the extracted-and-aggregated result must equal the
reference; where the route matters (the fast path must actually be exercised,
malformed lines must not be counted) it is asserted too.

    docker compose -f deploy/spark_cluster/docker-compose.yml exec -T spark-master \\
        python3 -m unittest tests.test_sql_analytics -v
"""

from pathlib import Path
import copy
import importlib.util
import json
import math
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

HAVE_SPARK = importlib.util.find_spec("pyspark") is not None

BASE = {
    "doc_id": "d1",
    "title": "Apple 10-K: revenue up 5.2% for O'Brien's $AAPL unit (U.S.)",
    # dotted capital I, fi ligature, sharp s, Arabic-Indic digits (Unicode \d),
    # a line separator, and tokens with $ ' . - inside
    "body": "Body with İstanbul, ﬁnance, Straße, Arabic digits ١٢٣, "
            "3.14, v1.2.3, x-ray and $cash's next",
    "source": "sec_edgar_api",
    "source_type": "sec_filing_10k",
    "url": "https://www.sec.gov/x",
    "canonical_url": "https://www.sec.gov/x",
    "event_type": "earnings",
    "source_reliability_tier": "primary_regulatory",
    "language": "en",
    "published_at": "2022-03-04T14:05:00Z",
    "available_at": "2022-03-04T15:00:00Z",
    "first_seen_at": "2022-03-04T15:00:00Z",
    "ingested_at": "2022-03-04T15:30:00Z",
    "last_url_check_at": "2022-03-05T00:00:00Z",
    "tickers_detected": ["aapl", "MSFT"],
    "matched_tickers": ["aapl", " ", "msft", "AAPL"],
    "matched_holdings": ["AAPL"],
    "company_names_detected": ["Apple Inc."],
    "sectors_detected": ["Information Technology"],
    "sector_tags": ["tech"],
    # " ", U+3000 and U+001C strip to nothing in Python (U+001C is not Java's \s)
    "event_tags": ["earnings", "earnings", " ", "　", "guidance"],
    "risk_terms": ["supply chain", "\x1c", "China demand"],
    "source_credibility": 0.95,
    "sentiment_score": -0.25,
    "is_revision": False,
    "sec": {"form": "10-K", "items": [1, 2, {"deep": ["x"]}]},
}
DELETE = object()


def variant(**changes) -> str:
    record = copy.deepcopy(BASE)
    for key, value in changes.items():
        if value is DELETE:
            record.pop(key, None)
        else:
            record[key] = value
    return json.dumps(record, ensure_ascii=False)


CLEAN = variant()

# (name, line, route) -- route: fast | fallback | drop | any
FIXTURES = [
    ("clean", CLEAN, "fast"),
    ("ascii escapes", json.dumps(BASE, ensure_ascii=True), "fast"),
    ("no optional keys", json.dumps({"doc_id": "m", "published_at": "2021-01-01T00:00:00Z"}), "fast"),
    ("missing title/body", variant(title=DELETE, body=DELETE), "fast"),
    ("missing source_type, sample source", variant(source_type=DELETE, source="sample_feed"), "fast"),
    ("missing source_type", variant(source_type=DELETE), "fast"),
    ("empty source_type", variant(source_type=""), "fast"),
    ("missing language", variant(language=DELETE), "fast"),
    ("empty language", variant(language=""), "fast"),
    ("missing tier", variant(source_reliability_tier=DELETE), "fast"),
    ("empty tier", variant(source_reliability_tier=""), "fast"),
    ("empty canonical_url, fred url", variant(canonical_url="", url="https://fred.stlouisfed.org/x",
                                              source_type="macro"), "fast"),
    ("missing canonical_url", variant(canonical_url=DELETE), "fast"),
    ("family company", variant(source_type="company_ir_release", url="https://ir.example.com",
                               canonical_url="https://ir.example.com", source="ir"), "fast"),
    ("family edgar via source", variant(source_type="filing", url="https://x.com", canonical_url="",
                                        source="EDGAR full text"), "fast"),
    ("family other", variant(source_type="blog", url="https://x.com", canonical_url="", source="x"), "fast"),
    ("uppercase source_type", variant(source_type="OFFICIAL_MACRO_series"), "fast"),
    ("missing matched_tickers", variant(matched_tickers=DELETE), "fast"),
    ("missing event_tags", variant(event_tags=DELETE), "fast"),
    ("missing event_tags and event_type", variant(event_tags=DELETE, event_type=DELETE), "fast"),
    ("missing lists", variant(tickers_detected=DELETE, matched_tickers=DELETE, company_names_detected=DELETE,
                              sectors_detected=DELETE, event_tags=DELETE, risk_terms=DELETE), "fast"),
    ("missing credibility", variant(source_credibility=DELETE), "fast"),
    ("zero credibility", variant(source_credibility=0), "fast"),
    ("negative zero credibility",
     '{"doc_id":"z","published_at":"2021-01-01T00:00:00Z","source_credibility":-0.0}', "fast"),
    ("integer credibility", variant(source_credibility=1), "fast"),
    ("missing available_at", variant(available_at=DELETE, first_seen_at=DELETE, ingested_at=DELETE), "fast"),
    ("empty available_at", variant(available_at=""), "fast"),
    ("leap day", variant(published_at="2024-02-29T00:00:00Z", available_at="2024-02-29T00:00:00Z"), "fast"),
    ("evidence_unit_index int", variant(evidence_unit_index=5), "fast"),
    ("evidence_unit_index str", variant(evidence_unit_index="7"), "fast"),
    ("duplicate key", CLEAN[:-1] + ', "title": "second title wins"}', "fast"),
    ("escaped key", CLEAN.replace('"title"', '"ti\\u0074le"'), "fast"),
    ("trailing json space", CLEAN + " \r", "fast"),
    ("leading json space", "  \t" + CLEAN, "fallback"),
    ("title looks like a number", variant(title="2022"), "fallback"),
    ("title starting with a bracket", variant(title="[draft] note"), "fast"),
    ("title that is a bracketed list", variant(title="[draft]"), "fallback"),
    # null semantics: str(None) == "None", key present
    ("null title", variant(title=None), "fallback"),
    ("null source_type", variant(source_type=None), "fallback"),
    ("null language", variant(language=None), "fallback"),
    ("null doc_id", variant(doc_id=None), "fallback"),
    ("numeric doc_id", variant(doc_id=5), "fallback"),
    ("null credibility", variant(source_credibility=None), "fallback"),
    ("null in list", variant(matched_tickers=["AAPL", None]), "fallback"),
    # a null outside the schema fields is never read by from_dict; "null" in text is text
    ("nested null", variant(sec={"form": None}), "fast"),
    ("null-looking text in body", variant(body='config {"x": null, "y": 1}'), "fast"),
    # non-string JSON values where from_dict applies str()
    ("numeric title", variant(title=5), "fallback"),
    ("boolean title", variant(title=True), "fallback"),
    ("float title", variant(title=5.10), "fallback"),
    ("object source_type", variant(source_type={"a": 1}), "fallback"),
    ("list language", variant(language=["en"]), "fallback"),
    ("numeric event_type", variant(event_type=7, event_tags=DELETE), "fallback"),
    ("numeric ticker", variant(tickers_detected=["A", 1]), "fallback"),
    ("boolean risk", variant(risk_terms=[True]), "fallback"),
    ("string list", variant(tickers_detected="AAPL", matched_tickers=DELETE), "fallback"),
    ("string credibility", variant(source_credibility="0.8"), "fallback"),
    ("junk credibility", variant(source_credibility="n/a"), "fallback"),
    ("empty credibility", variant(source_credibility=""), "fallback"),
    ("nan credibility", CLEAN.replace('"source_credibility": 0.95', '"source_credibility": NaN'), "fallback"),
    ("huge credibility", CLEAN.replace('"source_credibility": 0.95', '"source_credibility": 1e400'), "fallback"),
    ("evidence_unit_index float", variant(evidence_unit_index=5.5), "fallback"),
    ("non-ascii ticker", variant(tickers_detected=["ßx"], matched_tickers=DELETE), "fallback"),
    ("kelvin sign in source_type", variant(source_type="K_company"), "fallback"),
    ("nesting 9 deep", variant(sec=[[[[[[[["x"]]]]]]]]), "fast"),
    # beyond the depth margin: Python still parses it, the SQL hands it over
    ("nesting 920 deep", CLEAN[:-1] + ', "deep": ' + "[" * 920 + "]" * 920 + "}", "fallback"),
    # timestamps: Python normalises or rejects
    ("offset timestamp", variant(available_at="2022-03-04T20:00:00+05:00"), "fallback"),
    ("fractional seconds", variant(available_at="2022-03-04T20:00:00.5Z"), "fallback"),
    ("date-only published", variant(published_at="2022-03-04"), "any"),
    ("feb 30", variant(published_at="2022-02-30T00:00:00Z"), "any"),
    ("hour 24", variant(available_at="2022-03-04T24:00:00Z"), "any"),
    ("second 60", variant(ingested_at="2022-03-04T23:59:60Z"), "any"),
    ("year 0000", variant(first_seen_at="0000-01-01T00:00:00Z"), "any"),
    ("bad last_url_check_at", variant(last_url_check_at="yesterday"), "any"),
    ("junk evidence_unit_index", variant(evidence_unit_index="abc"), "any"),
    ("dash evidence_unit_index", variant(evidence_unit_index="-"), "any"),
    # int() refuses more than 4300 digits (Python >= 3.10.7): the record drops
    ("640-digit evidence_unit_index", variant(evidence_unit_index="1" * 640), "fast"),
    ("4301-digit evidence_unit_index", variant(evidence_unit_index="1" * 4301), "fallback"),
    ("whitespace available_at", variant(available_at=" "), "any"),
    # json.loads refuses integers over 4300 digits (Python >= 3.10.7): the whole line drops
    ("5000-digit integer", CLEAN[:-1] + ', "big": 1' + "0" * 5000 + "}", "fallback"),
    # float(2e308 as int) raises OverflowError, which from_dict does not catch
    ("overflowing score", CLEAN[:-1] + ', "uncertainty_score": 2' + "0" * 308 + "}", "crash"),
    # an unpaired surrogate escape (as JSON text); with no stored hashes from_dict
    # computes them, .encode("utf-8") fails and the record drops
    ("lone surrogate, no hashes", json.dumps({**BASE, "title": "x\ud800y"}), "fallback"),
    ("lone surrogate, stored hashes",
     json.dumps({**BASE, "title": "x\ud800y", "document_hash": "h", "duplicate_cluster_id": "c"}), "fallback"),
    # Python drops the record
    ("missing published_at", variant(published_at=DELETE), "drop"),
    ("empty published_at", variant(published_at=""), "drop"),
    ("missing doc_id", variant(doc_id=DELETE), "any"),
    ("empty object", "{}", "drop"),
    ("blank line", "", "drop"),
    ("space line", "   \t", "drop"),
    ("trailing garbage", CLEAN + " xyz", "any"),
    ("two objects", CLEAN + CLEAN, "any"),
    ("trailing bracket", CLEAN + "]}", "any"),
    ("single quotes", "{'doc_id': 'a', 'published_at': '2021-01-01T00:00:00Z'}", "any"),
    ("bom", "﻿" + CLEAN, "any"),
    ("nbsp padding", " " + CLEAN + " ", "any"),
    ("array root", "[" + CLEAN + "]", "any"),
    ("garbage", "not json", "any"),
]


@unittest.skipUnless(HAVE_SPARK, "pyspark not installed")
class SqlAnalyticsExactnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from pyspark.sql import SparkSession

        cls.spark = (SparkSession.builder.master("local[2]").appName("test-sql-analytics")
                     .config("spark.ui.enabled", "false").config("spark.sql.session.timeZone", "UTC")
                     .getOrCreate())
        cls.spark.sparkContext.setLogLevel("ERROR")

    @classmethod
    def tearDownClass(cls):
        cls.spark.stop()

    def extract_line(self, line):
        from bigdata.streaming import sql_analytics

        rows = sql_analytics.extract(self.spark.createDataFrame([(line,)], "value string")).collect()
        self.assertEqual(len(rows), 1)
        return rows

    def test_token_regex_is_the_reference_regex(self):
        from bigdata.streaming import sql_analytics
        from finportfolio_ir.text_utils import TOKEN_RE

        self.assertEqual(sql_analytics.TOKEN_PATTERN, TOKEN_RE.pattern)

    def test_java_digits_are_pythons_digits(self):
        # Every Unicode decimal digit Python knows, plus numerics that are NOT
        # decimal (superscripts, fractions, roman numerals): Spark must count the
        # tokens tokenize() finds, whatever the JDK's Unicode version.
        import sys as _sys
        from pyspark.sql import functions as F
        from bigdata.streaming import sql_analytics
        from finportfolio_ir.text_utils import tokenize

        digits = [chr(i) for i in range(_sys.maxunicode + 1) if chr(i).isdecimal()]
        others = ["²", "½", "Ⅻ", "൰", "௰", "〇"]
        text = " ".join(digits + others) + " x" + "".join(digits[:50]) + " " + "".join(digits[-50:])
        frame = self.spark.createDataFrame([(text,)], "t string")
        got = frame.select(sql_analytics._token_count(F.col("t")).alias("n")).first()["n"]
        self.assertEqual(got, len(tokenize(text)))

    def test_parse_runs_once_per_line(self):
        from bigdata.streaming import sql_analytics

        frame = self.spark.createDataFrame([(CLEAN,)], "value string")
        plan = sql_analytics.extract(frame)._jdf.queryExecution().optimizedPlan().toString()
        self.assertEqual(plan.count("from_json("), 1, plan)
        # the token regex runs once (sentinel count), and no token array is built
        self.assertEqual(plan.count(sql_analytics.TOKEN_COUNT_PATTERN), 1)
        self.assertEqual(plan.count("regexp_extract_all("), 0)

    def test_every_fixture_matches_analytics_doc(self):
        from bigdata.jobs.corpus_analytics import analytics_doc, emit_metrics
        from bigdata.streaming import sql_analytics

        for name, line, route in FIXTURES:
            with self.subTest(name):
                if route == "crash":
                    # the reference raises: the port must route to it and raise too
                    with self.assertRaises(Exception) as caught:
                        analytics_doc(line)
                    rows = self.extract_line(line)
                    self.assertEqual(sql_analytics.route(rows[0]), "fallback")
                    with self.assertRaises(type(caught.exception)):
                        sql_analytics.aggregate(rows)
                    continue
                expected = analytics_doc(line)
                rows = self.extract_line(line)
                taken = sql_analytics.route(rows[0])
                if route != "any":
                    self.assertEqual(taken, route)
                if taken == "fast":
                    got = sql_analytics.row_meta(rows[0])
                    self.assertIsNotNone(expected)
                    self.assertEqual(got, expected)
                    self.assertEqual(math.copysign(1.0, got["credibility"]),
                                     math.copysign(1.0, expected["credibility"]))
                if taken == "drop":
                    self.assertIsNone(expected)
                metrics, low, high, documents, _ = sql_analytics.aggregate(rows)
                reference = {}
                if expected is not None:
                    for key, amount in emit_metrics(expected):
                        reference[key] = reference.get(key, 0) + amount
                # json: NaN credibility (a Python-accepted literal) compares equal as "NaN"
                self.assertEqual(json.dumps(metrics, sort_keys=True), json.dumps(reference, sort_keys=True))
                self.assertEqual(documents, int(expected is not None))
                self.assertEqual(low, expected["available_at"] if expected else "")

    def test_python_crash_is_preserved_not_hidden(self):
        # from_dict iterates matched_holdings; a number there raises TypeError in the
        # reference. The port must not quietly count the document instead.
        from bigdata.jobs.corpus_analytics import analytics_doc
        from bigdata.streaming import sql_analytics

        line = variant(matched_holdings=5)
        with self.assertRaises(TypeError):
            analytics_doc(line)
        rows = self.extract_line(line)
        self.assertEqual(sql_analytics.route(rows[0]), "fallback")
        with self.assertRaises(TypeError):
            sql_analytics.aggregate(rows)

    def test_many_lines_in_one_frame(self):
        # Partitioned input, mixed routes: the aggregate equals raw_metrics.
        from bigdata.engine import LocalEngine
        from bigdata.jobs import corpus_analytics
        from bigdata.streaming import sql_analytics

        lines = [line for _, line, route in FIXTURES if route != "crash"] * 3
        frame = self.spark.createDataFrame([(line,) for line in lines], "value string").repartition(3)
        rows = sql_analytics.extract(frame).collect()
        self.assertEqual(len(rows), len(lines))
        metrics, low, high, documents, fallback = sql_analytics.aggregate(rows)
        engine = LocalEngine(num_workers=1)
        try:
            ref_metrics, ref_low, ref_high, ref_documents = corpus_analytics.raw_metrics(
                engine.parallelize(lines))
        finally:
            engine.stop()
        # Float sums (credsum) may differ in the last bit with summation order;
        # the report rounds them, and everything else must be exact.
        self.assertEqual((low, high, documents), (ref_low, ref_high, ref_documents))
        self.assertEqual(set(metrics), set(ref_metrics))
        for key, amount in ref_metrics.items():
            both_nan = math.isnan(metrics[key]) and math.isnan(amount)
            self.assertTrue(both_nan or math.isclose(metrics[key], amount, rel_tol=1e-12), key)
        self.assertEqual(json.dumps(corpus_analytics.finalize_report(metrics, low, high), sort_keys=True),
                         json.dumps(corpus_analytics.finalize_report(ref_metrics, ref_low, ref_high),
                                    sort_keys=True))
        self.assertGreater(fallback, 0)
        self.assertLess(fallback, len(lines))


if __name__ == "__main__":
    unittest.main()
