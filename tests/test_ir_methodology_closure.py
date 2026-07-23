import tempfile
import unittest
from pathlib import Path

from evaluation.build_ir_methodology_closure import build_closure_report, build_markdown, PhaseStatus


class IRMethodologyClosureTests(unittest.TestCase):
    def test_build_markdown_records_stage_loop_protocol(self):
        phase = PhaseStatus(
            phase_id="11",
            title="Self-Improvement Loop",
            status="closed",
            metric="accepted_loops=5",
            artifact="agent_skill.md",
            evidence="loop passed",
            risk="none",
            next_action="rerun loops",
        )
        text = build_markdown(
            [phase],
            {
                "generated_at_utc": "2026-01-01T00:00:00Z",
                "status": "closed",
                "closed_phases": 1,
                "phase_count": 1,
            },
        )

        self.assertIn("SkillOpt", text)
        self.assertIn("after every methodology stage", text)

    def test_empty_project_report_is_open_and_writes_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "out"
            summary = build_closure_report(root, output)

            self.assertEqual(summary["status"], "open")
            self.assertEqual(summary["phase_count"], 11)
            self.assertTrue((output / "phase_status.csv").exists())
            self.assertTrue((output / "methodology_closure.md").exists())


if __name__ == "__main__":
    unittest.main()
