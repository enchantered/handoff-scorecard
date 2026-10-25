import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.tasks = self.root / "tasks.json"
        self.answers = self.root / "answers.jsonl"
        task_data = {
            "schema_version": 1,
            "conditions": ["repo", "packet"],
            "tasks": [{
                "id": "case-1",
                "prompt": "PRIVATE PROMPT",
                "contexts": {"repo": "PRIVATE CONTEXT", "packet": "PRIVATE CONTEXT"},
                "required_terms": ["blue widget"],
                "forbidden_terms": [],
            }],
        }
        self.tasks.write_text(json.dumps(task_data), encoding="utf-8")
        self.rows = [
            {"task_id": "case-1", "condition": condition, "answer": "blue widget",
             "human_review": {"goal_met": True, "critical_context_missing": False,
                              "unsupported_claim_count": 0}}
            for condition in ("repo", "packet")
        ]
        self.write_rows()

    def tearDown(self):
        self.tmp.cleanup()

    def write_rows(self, rows=None):
        rows = self.rows if rows is None else rows
        self.answers.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    def run_cli(self, *args):
        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return subprocess.run(
            [sys.executable, "-m", "handoff_scorecard", "score",
             str(self.tasks), str(self.answers), *args],
            cwd=ROOT, env=env, capture_output=True, text=True, check=False,
        )

    def test_stdout_json_success_omits_raw_input(self):
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["aggregates"]["overall"]["pass_rate"], 1)
        self.assertNotIn("PRIVATE PROMPT", result.stdout)
        self.assertNotIn("PRIVATE CONTEXT", result.stdout)

    def test_fail_on_miss_returns_one_after_report(self):
        self.write_rows([dict(row, answer="missing") for row in self.rows])
        result = self.run_cli("--fail-on-miss")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)["aggregates"]["overall"]["pass_rate"], 0)

    def test_malformed_input_returns_two_without_traceback(self):
        self.answers.write_text("not-json\n", encoding="utf-8")
        result = self.run_cli()
        self.assertEqual(result.returncode, 2)
        self.assertIn("error:", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_output_file_contains_report(self):
        output = self.root / "report.json"
        result = self.run_cli("--output", str(output))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["schema_version"], 1)


if __name__ == "__main__":
    unittest.main()
