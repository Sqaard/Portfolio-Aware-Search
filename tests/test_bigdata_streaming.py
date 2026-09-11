"""Incremental streaming updater: idempotency and lossless accumulation."""

from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bigdata.engine import LocalEngine
from bigdata.jobs import corpus_analytics
from bigdata.streaming.incremental_update import run_tick

SAMPLE = ROOT / "data" / "processed_documents" / "documents.jsonl"


class IncrementalUpdateTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.inbox = base / "inbox"
        self.state_dir = base / "state"
        self.inbox.mkdir(parents=True, exist_ok=True)
        self.lines = [l for l in SAMPLE.read_text(encoding="utf-8").split("\n") if l.strip()]
        self.half = len(self.lines) // 2

    def tearDown(self):
        self._tmp.cleanup()

    def _tick(self):
        return run_tick(
            inbox=self.inbox,
            glob="*.jsonl",
            state_dir=self.state_dir,
            engine_name="local",
        )

    def test_incremental_accumulation_and_idempotency(self):
        (self.inbox / "batch1.jsonl").write_text("\n".join(self.lines[: self.half]) + "\n", encoding="utf-8")
        first = self._tick()
        self.assertEqual(first["new_files"], 1)
        self.assertEqual(first["new_documents"], self.half)
        self.assertEqual(first["total_documents"], self.half)

        # No new files -> nothing reprocessed, total unchanged (idempotent).
        second = self._tick()
        self.assertEqual(second["new_files"], 0)
        self.assertEqual(second["total_documents"], self.half)

        # New batch arrives -> only it is processed, totals accumulate.
        (self.inbox / "batch2.jsonl").write_text("\n".join(self.lines[self.half:]) + "\n", encoding="utf-8")
        third = self._tick()
        self.assertEqual(third["new_files"], 1)
        self.assertEqual(third["new_documents"], len(self.lines) - self.half)
        self.assertEqual(third["total_documents"], len(self.lines))

    def test_append_to_existing_file_is_not_double_counted(self):
        # The crawler appends to a live JSONL. Offset tracking must count each
        # appended line exactly once, never re-count the earlier lines.
        live = self.inbox / "live.jsonl"
        live.write_text("\n".join(self.lines[: self.half]) + "\n", encoding="utf-8")
        first = self._tick()
        self.assertEqual(first["total_documents"], self.half)

        with live.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(self.lines[self.half:]) + "\n")
        second = self._tick()
        self.assertEqual(second["new_documents"], len(self.lines) - self.half)
        self.assertEqual(second["total_documents"], len(self.lines))  # not half + full

        # No further growth -> nothing reprocessed.
        third = self._tick()
        self.assertEqual(third["new_files"], 0)
        self.assertEqual(third["total_documents"], len(self.lines))

    def test_incomplete_state_file_is_repaired(self):
        # A valid-but-incomplete state.json must not crash run_tick.
        import json
        self.state_dir.mkdir(parents=True, exist_ok=True)
        (self.state_dir / "state.json").write_text(json.dumps({"metrics": {}}), encoding="utf-8")
        (self.inbox / "b.jsonl").write_text("\n".join(self.lines[:5]) + "\n", encoding="utf-8")
        result = self._tick()  # must not raise KeyError
        self.assertEqual(result["total_documents"], 5)

    def test_streamed_report_equals_batch_report(self):
        (self.inbox / "a.jsonl").write_text("\n".join(self.lines[: self.half]) + "\n", encoding="utf-8")
        self._tick()
        (self.inbox / "b.jsonl").write_text("\n".join(self.lines[self.half:]) + "\n", encoding="utf-8")
        self._tick()

        engine = LocalEngine(num_workers=1)
        try:
            batch_report = corpus_analytics.compute_analytics(engine.text_file(str(SAMPLE)))
        finally:
            engine.stop()

        import json
        streamed = json.loads((self.state_dir / "analytics.json").read_text(encoding="utf-8"))
        self.assertEqual(streamed["total_documents"], batch_report["total_documents"])
        self.assertEqual(streamed["by_source_family"], batch_report["by_source_family"])
        self.assertEqual(streamed["by_year"], batch_report["by_year"])


if __name__ == "__main__":
    unittest.main()
