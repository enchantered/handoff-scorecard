import json
import os
import tempfile
import unittest
from pathlib import Path

from handoff_scorecard.core import ScorecardError, contains_exact_phrase, load_and_score, tokens, write_atomic


class ScorecardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.tasks = self.root / "tasks.json"
        self.answers = self.root / "answers.jsonl"
        self.set_case()

    def tearDown(self):
        self.tmp.cleanup()

    def set_case(self, *, condition_ids=None, task_id="one", context="the blue widget"):
        conditions = condition_ids or ["repo", "packet"]
        tasks = {"schema_version": 1, "conditions": conditions, "tasks": [{
            "id": task_id, "prompt": "Say blue widget", "contexts": {c: context for c in conditions},
            "required_terms": ["blue widget"], "forbidden_terms": ["red widget"]}]}
        self.tasks.write_text(json.dumps(tasks), encoding="utf-8")
        review = {"goal_met": True, "critical_context_missing": False, "unsupported_claim_count": 0}
        self.rows = [{"task_id": task_id, "condition": c, "answer": "A BLUE widget.",
                      "human_review": review} for c in conditions]
        self.write_rows()

    def write_rows(self, rows=None):
        rows = self.rows if rows is None else rows
        self.answers.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    def error(self):
        with self.assertRaises(ScorecardError):
            load_and_score(str(self.tasks), str(self.answers))

    def test_happy_case_and_aggregates(self):
        report = load_and_score(str(self.tasks), str(self.answers))
        self.assertEqual(len(report["cases"]), 2)
        self.assertTrue(all(row["passed"] for row in report["cases"]))
        self.assertEqual(report["aggregates"]["overall"]["pass_rate"], 1)

    def test_casefold_exact_phrase(self):
        self.assertTrue(contains_exact_phrase(tokens("A BLUE Widget!"), tokens("blue widget")))

    def test_phrase_not_substring(self):
        self.assertFalse(contains_exact_phrase(tokens("blue-widget"), tokens("blue widget")))

    def test_phrase_requires_contiguous_tokens(self):
        self.assertFalse(contains_exact_phrase(tokens("blue very widget"), tokens("blue widget")))

    def test_punctuation_is_tokenized(self):
        self.assertEqual(tokens("hi, there!"), ["hi", ",", "there", "!"])

    def test_forbidden_term_fails(self):
        self.write_rows([dict(r, answer="blue widget; red widget") for r in self.rows])
        report = load_and_score(str(self.tasks), str(self.answers))
        self.assertTrue(all(x["forbidden_term_matched"] and not x["passed"] for x in report["cases"]))

    def test_human_review_failure_is_separate_from_lexical_pass(self):
        rows = [dict(r, human_review={"goal_met": True, "critical_context_missing": True,
                                     "unsupported_claim_count": 1}) for r in self.rows]
        self.write_rows(rows)
        report = load_and_score(str(self.tasks), str(self.answers))
        self.assertTrue(all(x["lexical_passed"] for x in report["cases"]))
        self.assertTrue(all(not x["human_review_passed"] and not x["passed"] for x in report["cases"]))
        self.assertEqual(report["aggregates"]["overall"]["human_review_pass_rate"], 0)

    def test_human_review_requires_boolean_decisions(self):
        rows = [dict(r, human_review={"goal_met": 1, "critical_context_missing": False,
                                     "unsupported_claim_count": 0}) for r in self.rows]
        self.write_rows(rows)
        self.error()

    def test_human_review_rejects_boolean_as_count(self):
        rows = [dict(r, human_review={"goal_met": True, "critical_context_missing": False,
                                     "unsupported_claim_count": True}) for r in self.rows]
        self.write_rows(rows)
        self.error()

    def test_whitespace_only_term_rejected(self):
        data = json.loads(self.tasks.read_text())
        data["tasks"][0]["required_terms"] = ["   "]
        self.tasks.write_text(json.dumps(data))
        self.error()

    def test_duplicate_terms_after_normalization_rejected(self):
        data = json.loads(self.tasks.read_text())
        data["tasks"][0]["required_terms"] = ["blue widget", "BLUE  widget"]
        self.tasks.write_text(json.dumps(data))
        self.error()

    def test_overlapping_required_and_forbidden_terms_rejected(self):
        data = json.loads(self.tasks.read_text())
        data["tasks"][0]["forbidden_terms"] = ["BLUE  WIDGET"]
        self.tasks.write_text(json.dumps(data))
        self.error()

    def test_missing_row(self):
        self.write_rows(self.rows[:-1])
        self.error()

    def test_duplicate_row(self):
        self.write_rows(self.rows + [self.rows[0]])
        self.error()

    def test_unknown_pair(self):
        self.write_rows([dict(self.rows[0], task_id="other"), self.rows[1]])
        self.error()

    def test_blank_jsonl_row(self):
        self.answers.write_text(self.answers.read_text() + "\n", encoding="utf-8")
        self.error()

    def test_duplicate_json_key_rejected(self):
        self.answers.write_text('{"task_id":"one","task_id":"one","condition":"repo","answer":"x"}\n', encoding="utf-8")
        self.error()

    def test_wrong_root_fields(self):
        data = json.loads(self.tasks.read_text())
        data["extra"] = "x"
        self.tasks.write_text(json.dumps(data))
        self.error()

    def test_task_id_validation(self):
        self.set_case(task_id="Upper-case")
        self.error()

    def test_condition_count_bounds(self):
        self.set_case(condition_ids=["only"])
        self.error()

    def test_required_terms_bounds(self):
        data = json.loads(self.tasks.read_text())
        data["tasks"][0]["required_terms"] = []
        self.tasks.write_text(json.dumps(data))
        self.error()

    def test_prompt_character_bound(self):
        data = json.loads(self.tasks.read_text())
        data["tasks"][0]["prompt"] = "x" * 20001
        self.tasks.write_text(json.dumps(data))
        self.error()

    def test_combined_file_size_bound(self):
        self.tasks.write_text(" " * (2 * 1024 * 1024))
        self.error()

    def test_symlink_input_rejected(self):
        alias = self.root / "alias.json"
        alias.symlink_to(self.tasks)
        with self.assertRaises(ScorecardError):
            load_and_score(str(alias), str(self.answers))

    def test_directory_input_rejected(self):
        with self.assertRaises(ScorecardError):
            load_and_score(str(self.root), str(self.answers))

    @unittest.skipUnless(hasattr(os, "mkfifo"), "requires FIFO support")
    def test_fifo_input_rejected_without_blocking(self):
        fifo = self.root / "answers.fifo"
        os.mkfifo(fifo)
        with self.assertRaises(ScorecardError):
            load_and_score(str(self.tasks), str(fifo))

    def test_report_does_not_echo_prompt_context_or_answer(self):
        data = json.loads(self.tasks.read_text())
        data["tasks"][0]["prompt"] = "PRIVATE-PROMPT-991"
        data["tasks"][0]["contexts"] = {c: "PRIVATE-CONTEXT-992" for c in data["conditions"]}
        self.tasks.write_text(json.dumps(data))
        self.write_rows([dict(r, answer="PRIVATE-ANSWER-993") for r in self.rows])
        serialized = json.dumps(load_and_score(str(self.tasks), str(self.answers)))
        for private_text in ("PRIVATE-PROMPT-991", "PRIVATE-CONTEXT-992", "PRIVATE-ANSWER-993"):
            self.assertNotIn(private_text, serialized)

    def test_output_atomic_and_valid_json(self):
        report = load_and_score(str(self.tasks), str(self.answers))
        output = self.root / "report.json"
        write_atomic(str(output), report, (str(self.tasks), str(self.answers)))
        self.assertEqual(json.loads(output.read_text())["schema_version"], 1)
        self.assertFalse(list(self.root.glob(".handoff-scorecard-*")))

    def test_output_must_not_overwrite_input(self):
        report = load_and_score(str(self.tasks), str(self.answers))
        with self.assertRaises(ScorecardError):
            write_atomic(str(self.tasks), report, (str(self.tasks), str(self.answers)))

    def test_output_hardlink_to_input_must_not_overwrite_input(self):
        alias = self.root / "tasks-hardlink.json"
        os.link(self.tasks, alias)
        report = load_and_score(str(self.tasks), str(self.answers))
        with self.assertRaises(ScorecardError):
            write_atomic(str(alias), report, (str(self.tasks), str(self.answers)))

    def test_output_symlink_rejected(self):
        report = load_and_score(str(self.tasks), str(self.answers))
        output = self.root / "link.json"
        output.symlink_to(self.root / "missing")
        with self.assertRaises(ScorecardError):
            write_atomic(str(output), report, (str(self.tasks), str(self.answers)))

    def test_output_directory_rejected(self):
        report = load_and_score(str(self.tasks), str(self.answers))
        with self.assertRaises(ScorecardError):
            write_atomic(str(self.root), report, (str(self.tasks), str(self.answers)))


if __name__ == "__main__":
    unittest.main()
