"""Tests for the ALFRED point-in-time macro collection path.

None of these tests touch the network: the FRED CSV universe and the ALFRED
vintage lookup are both stubbed, so the assertions are about the *builder's*
logic (which value wins, which timestamp wins, what gets dropped).
"""

from datetime import date
from pathlib import Path
import json
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import features.build_official_macro_documents as builder  # noqa: E402
from features.build_official_macro_documents import (  # noqa: E402
    DEFAULT_SERIES,
    _resolve_vintage_mode,
    build_macro_record,
)
from features.fred_alfred import FirstRelease, _chunk_bounds, load_env_file  # noqa: E402

SPECS = {spec.series_id: spec for spec in DEFAULT_SERIES}


class BuildMacroRecordTests(unittest.TestCase):
    """``build_macro_record`` must stay backwards compatible without a vintage."""

    def test_without_vintage_uses_estimated_release_lag(self):
        spec = SPECS["CPIAUCSL"]  # release_lag_days = 18
        record = build_macro_record(spec, date(2010, 1, 1), 218.056)

        self.assertEqual(record["available_at"], "2010-01-19T14:00:00Z")
        self.assertEqual(record["macro_availability_source"], "estimated_release_lag")
        self.assertEqual(record["macro_value"], 218.056)
        self.assertEqual(record["macro_value_latest"], 218.056)
        self.assertEqual(record["macro_value_revision"], "")
        self.assertEqual(record["macro_first_release_date"], "")
        self.assertEqual(record["macro_actual_release_lag_days"], "")
        self.assertIn("Conservative available_at", record["body"])

    def test_vintage_overrides_value_and_timestamp(self):
        spec = SPECS["CPIAUCSL"]
        first = FirstRelease(date(2010, 1, 1), date(2010, 2, 19), 217.587)
        record = build_macro_record(spec, date(2010, 1, 1), 218.056, first)

        # The point-in-time value replaces today's revised figure ...
        self.assertEqual(record["macro_value"], 217.587)
        self.assertEqual(record["macro_value_latest"], 218.056)
        self.assertAlmostEqual(record["macro_value_revision"], 0.469, places=6)
        # ... and the real publication date replaces the estimate.
        self.assertEqual(record["available_at"], "2010-02-19T14:00:00Z")
        self.assertEqual(record["published_at"], "2010-02-19T14:00:00Z")
        self.assertEqual(record["macro_first_release_date"], "2010-02-19")
        self.assertEqual(record["macro_actual_release_lag_days"], 49)
        self.assertEqual(record["macro_availability_source"], "alfred_first_release")
        # The spec's estimate is retained for comparison, not overwritten.
        self.assertEqual(record["macro_release_lag_days"], 18)
        self.assertIn("First-release available_at", record["body"])
        self.assertIn("217.587", record["body"])

    def test_vintage_without_a_value_falls_back(self):
        """A vintage row published as '.' carries no usable number."""

        spec = SPECS["DGS10"]
        first = FirstRelease(date(2010, 1, 1), date(2010, 1, 6), None)
        record = build_macro_record(spec, date(2010, 1, 1), 3.85, first)

        self.assertEqual(record["macro_availability_source"], "estimated_release_lag")
        self.assertEqual(record["macro_value"], 3.85)

    def test_same_day_vintage_never_moves_availability_earlier(self):
        """A day-granular vintage must not out-run the conservative estimate.

        VIXCLS/DGS10/DCOILWTICO are close-based (~21:00 UTC). Stamping 14:00 UTC
        on the observation day would assert the value was readable ~7h before the
        session that produced it -- a lookahead leak worse than the estimate.
        """

        spec = SPECS["VIXCLS"]  # release_lag_days = 1
        observation = date(2012, 3, 9)
        first = FirstRelease(observation, observation, 17.11)  # same-day vintage
        record = build_macro_record(spec, observation, 17.11, first)

        estimated = build_macro_record(spec, observation, 17.11)
        self.assertEqual(record["available_at"], estimated["available_at"])
        self.assertGreaterEqual(record["available_at"], estimated["available_at"])
        self.assertEqual(record["macro_availability_source"], "alfred_floored_by_estimate")
        # The point-in-time VALUE still comes from the vintage.
        self.assertEqual(record["macro_value"], 17.11)
        self.assertEqual(record["macro_first_release_date"], "2012-03-09")

    def test_late_vintage_still_wins_over_the_estimate(self):
        spec = SPECS["PAYEMS"]  # estimate +7d, reality ~+35d
        observation = date(2010, 1, 1)
        first = FirstRelease(observation, date(2010, 2, 5), 129527.0)
        record = build_macro_record(spec, observation, 129802.0, first)

        self.assertEqual(record["available_at"], "2010-02-05T14:00:00Z")
        self.assertEqual(record["macro_availability_source"], "alfred_first_release")
        self.assertEqual(record["macro_actual_release_lag_days"], 35)

    def test_split_follows_the_real_release_date(self):
        """A release that lands after TRAIN_END belongs to the test split."""

        spec = SPECS["CPIAUCSL"]
        observation = date(2021, 9, 1)  # estimate: +18d -> 2021-09-19, still train
        estimated = build_macro_record(spec, observation, 274.31)
        self.assertEqual(estimated["split"], "train")

        first = FirstRelease(observation, date(2021, 10, 13), 274.214)
        point_in_time = build_macro_record(spec, observation, 274.31, first)
        self.assertEqual(point_in_time["split"], "test")


class VintageModeTests(unittest.TestCase):
    def test_estimate_mode_never_needs_a_key(self):
        self.assertEqual(_resolve_vintage_mode("estimate", None), (False, ""))

    def test_alfred_mode_requires_a_key(self):
        # Isolate from the developer's own .env, which would supply a real key.
        original = builder.resolve_api_key
        builder.resolve_api_key = lambda explicit=None, env_file=None: ""
        try:
            with self.assertRaises(ValueError):
                _resolve_vintage_mode("alfred", "")
            self.assertEqual(_resolve_vintage_mode("auto", ""), (False, ""))
        finally:
            builder.resolve_api_key = original

    def test_explicit_key_is_used(self):
        use_alfred, key = _resolve_vintage_mode("alfred", "abc123")
        self.assertTrue(use_alfred)
        self.assertEqual(key, "abc123")

    def test_unknown_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            _resolve_vintage_mode("vintages-please", "abc123")


class ChunkBoundsTests(unittest.TestCase):
    def test_chunks_tile_the_range_without_gaps_or_overlaps(self):
        start, end = date(2010, 1, 1), date(2023, 3, 1)
        chunks = _chunk_bounds(start, end, 2)

        self.assertEqual(chunks[0][0], start)
        self.assertEqual(chunks[-1][1], end)
        for (_, previous_end), (next_start, _) in zip(chunks, chunks[1:]):
            self.assertEqual(next_start.toordinal(), previous_end.toordinal() + 1)

    def test_single_day_range(self):
        day = date(2020, 2, 29)
        self.assertEqual(_chunk_bounds(day, day, 2), [(day, day)])


class LoadEnvFileTests(unittest.TestCase):
    def test_existing_environment_wins(self):
        import os

        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text(
                "# comment\nFRED_API_KEY=from_file\nQUOTED=\"quoted value\"\n",
                encoding="utf-8",
            )
            os.environ["FRED_API_KEY"] = "from_environment"
            os.environ.pop("QUOTED", None)
            try:
                load_env_file(env_path)
                self.assertEqual(os.environ["FRED_API_KEY"], "from_environment")
                self.assertEqual(os.environ["QUOTED"], "quoted value")
            finally:
                os.environ.pop("FRED_API_KEY", None)
                os.environ.pop("QUOTED", None)


class BuildCorpusTests(unittest.TestCase):
    """End-to-end over the builder with the network stubbed out."""

    def _run(self, tmp: Path, vintage_mode: str, csv_rows, releases, fetch=None):
        original_download = builder._download_fred_rows
        original_fetch = builder.fetch_first_releases
        builder._download_fred_rows = lambda series_id, s, e, ua: csv_rows.get(series_id, [])
        builder.fetch_first_releases = fetch or (
            lambda series_id, s, e, key, **kw: releases.get(series_id, {})
        )
        try:
            return builder.build_official_macro_documents(
                output_raw=tmp / "raw.jsonl",
                output_processed=tmp / "processed.jsonl",
                metadata=ROOT / "data/processed_documents/dow30_ticker_metadata.csv",
                source_registry=ROOT / "data/source_registry/source_registry.csv",
                summary_output=tmp / "summary.json",
                start_date="2010-01-01",
                end_date="2023-03-01",
                user_agent="test-agent",
                series_specs=(SPECS["CPIAUCSL"],),
                vintage_mode=vintage_mode,
                api_key="test-key",
            )
        finally:
            builder._download_fred_rows = original_download
            builder.fetch_first_releases = original_fetch

    def test_out_of_window_observations_are_dropped(self):
        """fredgraph.csv ignores cosd/coed for some series -- clamp must catch it."""

        csv_rows = {
            "CPIAUCSL": [
                {"DATE": "2010-01-01", "value": "218.056"},
                {"DATE": "2026-05-11", "value": "320.0"},  # after the cutoff
                {"DATE": "2009-12-01", "value": "216.0"},  # before the window
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            summary = self._run(Path(tmp), "estimate", csv_rows, {})

        self.assertEqual(summary["raw_rows"], 1)
        self.assertEqual(summary["out_of_window_dropped"], 2)

    def test_alfred_mode_reports_coverage_and_applies_vintages(self):
        csv_rows = {
            "CPIAUCSL": [
                {"DATE": "2010-01-01", "value": "218.056"},
                {"DATE": "2010-02-01", "value": "218.9"},
            ]
        }
        releases = {
            "CPIAUCSL": {
                "2010-01-01": FirstRelease(date(2010, 1, 1), date(2010, 2, 19), 217.587),
                # 2010-02-01 deliberately absent -> estimated fallback
            }
        }
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            summary = self._run(tmp_path, "alfred", csv_rows, releases)
            records = [
                json.loads(line)
                for line in (tmp_path / "raw.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

        self.assertEqual(summary["vintage_regime"], "alfred_first_release")
        self.assertEqual(summary["point_in_time_rows"], 1)
        self.assertEqual(summary["estimated_fallback_rows"], 1)

        by_date = {r["macro_observation_date"]: r for r in records}
        self.assertEqual(by_date["2010-01-01"]["macro_availability_source"], "alfred_first_release")
        self.assertEqual(by_date["2010-01-01"]["macro_value"], 217.587)
        self.assertEqual(by_date["2010-02-01"]["macro_availability_source"], "estimated_release_lag")
        self.assertEqual(by_date["2010-02-01"]["macro_value"], 218.9)

    def test_series_missing_from_alfred_degrades_to_estimates(self):
        from features.fred_alfred import AlfredUnavailable

        csv_rows = {"CPIAUCSL": [{"DATE": "2010-01-01", "value": "218.056"}]}

        def raise_unavailable(series_id, s, e, key, **kw):
            raise AlfredUnavailable(f"{series_id}: not archived")

        with tempfile.TemporaryDirectory() as tmp:
            summary = self._run(Path(tmp), "alfred", csv_rows, {}, fetch=raise_unavailable)

        self.assertEqual(summary["error_count"], 0)
        self.assertEqual(summary["raw_rows"], 1)
        self.assertEqual(summary["vintage_by_series"][0]["status"], "not_in_alfred")
        self.assertEqual(summary["estimated_fallback_rows"], 1)


if __name__ == "__main__":
    unittest.main()
