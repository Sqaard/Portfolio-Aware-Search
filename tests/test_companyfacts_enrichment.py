from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from features.build_companyfacts_enrichment_v1 import load_success_events, safe_restart, write_status_csv
from finportfolio_ir.io_utils import write_jsonl


class CompanyfactsEnrichmentTests(unittest.TestCase):
    def test_load_success_events_uses_only_successful_checkpoints(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            good_path = root / "AAPL.events.jsonl"
            failed_path = root / "MSFT.events.jsonl"
            event = {"event_id": "evt1", "ticker": "AAPL", "event_type": "fundamental_fact"}
            write_jsonl(good_path, [event])
            write_jsonl(failed_path, [{"event_id": "evt2", "ticker": "MSFT"}])

            rows = [
                {"ticker": "AAPL", "status": "success", "checkpoint_path": str(good_path)},
                {"ticker": "MSFT", "status": "timed_out", "checkpoint_path": str(failed_path)},
            ]

            loaded = load_success_events(rows)

        self.assertEqual(loaded, [event])

    def test_write_status_csv_keeps_retry_fields_stable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "status.csv"
            write_status_csv(
                path,
                [
                    {
                        "ticker": "AAPL",
                        "status": "success",
                        "attempts": 1,
                        "event_count": 12,
                        "elapsed_seconds": 0.5,
                        "checkpoint_path": "checkpoints/AAPL.events.jsonl",
                        "error": "",
                    }
                ],
            )
            with path.open("r", encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))

        self.assertEqual(rows[0]["ticker"], "AAPL")
        self.assertEqual(rows[0]["status"], "success")
        self.assertIn("checkpoint_path", rows[0])

    def test_safe_restart_refuses_paths_outside_expected_output_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError):
                safe_restart(Path(tmp))


if __name__ == "__main__":
    unittest.main()
