import tempfile
import unittest
from pathlib import Path

from tools.skillopt_ir.run_ir_skill_loops import run_skill_loops, score_skill_text, build_loop_specs, load_project_context


class IRSkillOptLoopTests(unittest.TestCase):
    def test_five_loops_create_valid_agent_skill(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp)
            skill_path = project_root / "agent_skill.md"
            output_dir = project_root / "exports"

            result = run_skill_loops(project_root, skill_path, output_dir, loops=5, restart=True)
            text = skill_path.read_text(encoding="utf-8")

            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["loops_run"], 5)
            self.assertEqual(result["accepted_loops"], 5)
            self.assertIn("Stage 1 Skill: Source And Crawler Discipline", text)
            self.assertIn("Stage 5 Skill: FinGPT And CHRL Handoff Discipline", text)
            self.assertTrue((output_dir / "loop_summary.csv").exists())
            self.assertTrue((output_dir / "best_agent_skill.md").exists())

    def test_score_improves_when_required_stage_is_added(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp)
            context = load_project_context(project_root)
            specs = build_loop_specs(context)
            base = "# FinPortfolio IR Agent Skill\n\nNo source without provenance.\n"
            before = score_skill_text(base, specs)
            after = score_skill_text(base + "\n" + specs[0].section, specs)

            self.assertGreater(after["score"], before["score"])
            self.assertIn("stage:2:heading", after["failed_checks"])


if __name__ == "__main__":
    unittest.main()
