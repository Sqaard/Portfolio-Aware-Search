"""Structured Streaming ``--analytics full`` == the batch corpus-analytics report.

Needs Hadoop native IO for the file source and checkpoint, so it runs on Linux /
the Docker cluster and is skipped on Windows (see the module docstring of
``bigdata.streaming.spark_structured_streaming``). Run it inside a container:

    docker compose -f deploy/spark_cluster/docker-compose.yml exec -T spark-master \\
        python3 -m pytest tests/test_structured_streaming_full.py -q
"""

from pathlib import Path
import importlib.util
import json
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SAMPLE = ROOT / "data" / "processed_documents" / "documents.jsonl"
HAVE_SPARK = importlib.util.find_spec("pyspark") is not None


@unittest.skipIf(sys.platform.startswith("win"), "Structured Streaming needs Hadoop native IO (Linux / Docker)")
@unittest.skipUnless(HAVE_SPARK, "pyspark not installed")
class StructuredStreamingFullAnalyticsTests(unittest.TestCase):
    def test_micro_batched_report_equals_batch_report(self):
        self.assert_micro_batched_report_equals_batch_report("full")

    def test_sql_mode_report_equals_batch_report(self):
        # The Catalyst port: same state layout, same report, no Python workers.
        state = self.assert_micro_batched_report_equals_batch_report("sql")
        self.assertTrue(all(b["analytics"] == "sql" for b in state["batches"]))
        self.assertEqual(sum(b["fallback_documents"] for b in state["batches"]), 0)

    def assert_micro_batched_report_equals_batch_report(self, mode):
        from bigdata.engine import LocalEngine
        from bigdata.jobs import corpus_analytics
        from bigdata.streaming.spark_structured_streaming import main

        lines = [l for l in SAMPLE.read_text(encoding="utf-8").split("\n") if l.strip()]
        half = len(lines) // 2
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            inbox, out = base / "inbox", base / "out"
            inbox.mkdir()
            (inbox / "a.jsonl").write_text("\n".join(lines[:half]) + "\n", encoding="utf-8")
            (inbox / "b.jsonl").write_text("\n".join(lines[half:]) + "\n", encoding="utf-8")

            # one file per trigger -> two micro-batches, so the additive merge is exercised
            main([
                "--inbox", str(inbox), "--output-dir", str(out),
                "--checkpoint", str(base / "ckpt"), "--master", "local[2]",
                "--analytics", mode, "--once", "--max-files-per-trigger", "1",
            ])

            state = json.loads((out / "state.json").read_text(encoding="utf-8"))
            streamed = json.loads((out / "analytics.json").read_text(encoding="utf-8"))
            progress_path = out / "progress.jsonl"
            progress = [json.loads(l) for l in progress_path.read_text(encoding="utf-8").split("\n")
                        if l.strip()] if progress_path.exists() else []

        # Spark's own trigger timing is recorded by the listener. Listener events are
        # asynchronous, so the last one may still be in flight at shutdown -- require
        # at least one, with a real duration.
        self.assertTrue(progress, "StreamingQueryListener wrote no progress rows")
        self.assertTrue(all((row.get("trigger_ms") or 0) > 0 for row in progress))

        engine = LocalEngine(num_workers=1)
        try:
            expected = corpus_analytics.compute_analytics(engine.text_file(str(SAMPLE)))
        finally:
            engine.stop()

        self.assertEqual(len(state["batches"]), 2)
        self.assertEqual(state["total_documents"], len(lines))
        streamed.pop("streaming", None)
        self.assertEqual(streamed, expected)
        return state

    def test_checkpoint_reset_without_state_reset_is_refused(self):
        # Batch ids restart at 0 with a new checkpoint; merged state would then
        # swallow every batch as a replay. The job must refuse, not skip silently.
        from bigdata.streaming.spark_structured_streaming import main

        lines = [l for l in SAMPLE.read_text(encoding="utf-8").split("\n") if l.strip()]
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            inbox, out, ckpt = base / "inbox", base / "out", base / "ckpt"
            inbox.mkdir()
            (inbox / "a.jsonl").write_text("\n".join(lines[:5]) + "\n", encoding="utf-8")
            argv = ["--inbox", str(inbox), "--output-dir", str(out), "--checkpoint", str(ckpt),
                    "--master", "local[2]", "--analytics", "full", "--once"]
            main(argv)
            import shutil
            shutil.rmtree(ckpt)
            with self.assertRaises(SystemExit):
                main(argv)

    def test_empty_first_run_does_not_block_later_runs(self):
        # A cron-style --once over an empty inbox constructs the query -- Spark writes
        # its checkpoint metadata -- but merges nothing. The next run must proceed.
        from bigdata.streaming.spark_structured_streaming import main

        lines = [l for l in SAMPLE.read_text(encoding="utf-8").split("\n") if l.strip()]
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            inbox, out, ckpt = base / "inbox", base / "out", base / "ckpt"
            inbox.mkdir()
            argv = ["--inbox", str(inbox), "--output-dir", str(out), "--checkpoint", str(ckpt),
                    "--master", "local[2]", "--analytics", "full", "--once"]
            main(argv)
            (inbox / "a.jsonl").write_text("\n".join(lines[:5]) + "\n", encoding="utf-8")
            main(argv)
            state = json.loads((out / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["total_documents"], 5)

    def test_state_written_by_another_consumer_is_refused(self):
        # incremental_update writes the same state layout but no last_batch_id.
        # Merging into it would double-count documents both consumers have seen.
        from bigdata.streaming.spark_structured_streaming import main

        lines = [l for l in SAMPLE.read_text(encoding="utf-8").split("\n") if l.strip()]
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            inbox, out, ckpt = base / "inbox", base / "out", base / "ckpt"
            inbox.mkdir()
            out.mkdir()
            (out / "state.json").write_text(
                json.dumps({"metrics": {"docs\ttotal": 3}, "total_documents": 3}), encoding="utf-8"
            )
            (inbox / "a.jsonl").write_text("\n".join(lines[:5]) + "\n", encoding="utf-8")
            with self.assertRaises(SystemExit):
                main(["--inbox", str(inbox), "--output-dir", str(out), "--checkpoint", str(ckpt),
                      "--master", "local[2]", "--analytics", "full", "--once"])


if __name__ == "__main__":
    unittest.main()
