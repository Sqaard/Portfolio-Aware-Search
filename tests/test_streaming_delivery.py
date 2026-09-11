"""Live batch delivery: atomic new-file publishing and the simulated fetch path."""

from pathlib import Path
import json
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bigdata.engine import LocalEngine
from bigdata.jobs import corpus_analytics
from bigdata.streaming.incremental_update import run_tick
from crawler.streaming_delivery import (
    append_unique_records,
    emit_streaming_batch,
    sample_documents,
    simulate_live_fetch,
)

SAMPLE = ROOT / "data" / "processed_documents" / "documents.jsonl"


class StreamingDeliveryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.inbox = self.base / "inbox"

    def tearDown(self):
        self._tmp.cleanup()

    def test_emit_publishes_one_complete_visible_file(self):
        records = [{"doc_id": f"d{i}", "body": "x"} for i in range(5)]
        path = emit_streaming_batch(self.inbox, records)
        self.assertIsNotNone(path)
        visible = sorted(self.inbox.glob("*.jsonl"))
        self.assertEqual(visible, [path])
        self.assertEqual(len(path.read_text(encoding="utf-8").splitlines()), 5)
        # the temp file must be gone, and must never have matched the consumer glob
        self.assertEqual([p for p in self.inbox.iterdir() if p.name.startswith(".")], [])

    def test_emit_nothing_writes_nothing(self):
        self.assertIsNone(emit_streaming_batch(self.inbox, []))
        self.assertFalse(self.inbox.exists())

    def test_sample_is_reproducible_unique_and_rehashed(self):
        first = sample_documents(SAMPLE, 6, seed=11)
        again = sample_documents(SAMPLE, 6, seed=11)
        other = sample_documents(SAMPLE, 6, seed=12)
        self.assertEqual([r["doc_id"] for r in first], [r["doc_id"] for r in again])
        self.assertNotEqual([r["doc_id"] for r in first], [r["doc_id"] for r in other])
        self.assertEqual(len({r["doc_id"] for r in first}), 6)
        self.assertEqual(len({r["document_hash"] for r in first}), 6)
        self.assertTrue(all("#sim-11-" in r["doc_id"] for r in first))

    def test_append_unique_returns_only_new_records(self):
        live = self.base / "live.jsonl"
        a = [{"doc_id": "a"}, {"doc_id": "b"}]
        self.assertEqual(len(append_unique_records(live, a)), 2)
        self.assertEqual([r["doc_id"] for r in append_unique_records(live, [{"doc_id": "b"}, {"doc_id": "c"}])], ["c"])
        self.assertEqual(len(live.read_text(encoding="utf-8").splitlines()), 3)

    def test_simulated_batches_reach_the_incremental_updater_intact(self):
        # Two simulated fetches land as two new files; the updater must count every
        # document exactly once and produce the batch report for the same lines.
        state_dir = self.base / "state"
        live = self.base / "live.jsonl"
        first = simulate_live_fetch(corpus=SAMPLE, count=7, seed=1,
                                    processed_output=live, streaming_inbox=self.inbox)
        run_tick(inbox=self.inbox, glob="*.jsonl", state_dir=state_dir, engine_name="local")
        second = simulate_live_fetch(corpus=SAMPLE, count=9, seed=2,
                                     processed_output=live, streaming_inbox=self.inbox)
        tick = run_tick(inbox=self.inbox, glob="*.jsonl", state_dir=state_dir, engine_name="local")

        self.assertEqual(first["processed_appended"], 7)
        self.assertEqual(second["processed_appended"], 9)
        self.assertEqual(tick["total_documents"], 16)

        lines = []
        for path in sorted(self.inbox.glob("*.jsonl")):
            # frame on "\n" only, exactly as Spark's LineRecordReader and read_jsonl do
            lines.extend(l for l in path.read_text(encoding="utf-8").split("\n") if l.strip())
        engine = LocalEngine(num_workers=1)
        try:
            expected = corpus_analytics.compute_analytics(engine.parallelize(lines))
        finally:
            engine.stop()
        streamed = json.loads((state_dir / "analytics.json").read_text(encoding="utf-8"))
        streamed.pop("streaming", None)
        self.assertEqual(streamed, expected)

    def test_line_separator_characters_inside_a_record_do_not_split_it(self):
        # json.dumps(ensure_ascii=False) writes U+2028, U+2029 and U+0085 raw inside
        # strings. Spark and read_jsonl frame records on "\n" only; str.splitlines()
        # also breaks on those three, shattering the record into invalid fragments
        # that are then silently dropped.
        record = json.loads(SAMPLE.read_text(encoding="utf-8").split("\n")[0])
        record["body"] = "before middle afterend " + str(record.get("body", ""))
        path = emit_streaming_batch(self.inbox, [record])
        self.assertEqual(len([l for l in path.read_text(encoding="utf-8").split("\n") if l.strip()]), 1)

        tick = run_tick(inbox=self.inbox, glob="*.jsonl", state_dir=self.base / "state", engine_name="local")
        self.assertEqual(tick["new_documents"], 1)

        sampled = sample_documents(path, 1, seed=3)
        self.assertEqual(len(sampled), 1)
        self.assertIn(" ", sampled[0]["body"])


if __name__ == "__main__":
    unittest.main()
