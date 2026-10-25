"""Validation and scoring primitives. Supplied text is treated strictly as data."""
from __future__ import annotations

import json
import os
import re
import stat
import tempfile
from pathlib import Path
from typing import Any

MAX_INPUT_BYTES = 2 * 1024 * 1024
MAX_PROMPT_CHARS = 20_000
MAX_CONTEXT_CHARS = 100_000
MAX_TERM_CHARS = 500
MAX_ANSWER_CHARS = 100_000
SAFE_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z", re.ASCII)
TOKEN_RE = re.compile(r"[^\W_]+|[^\w\s]", re.UNICODE)


class ScorecardError(Exception):
    """A concise, safe-to-display input or output error."""


def _pairs_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ScorecardError("JSON object contains a duplicate key")
        result[key] = value
    return result


def _reject_constant(_: str) -> None:
    raise ScorecardError("JSON contains a non-standard numeric constant")


def _read_regular(path: str | os.PathLike[str], remaining: int) -> tuple[bytes, os.stat_result]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
    except (OSError, ValueError):
        raise ScorecardError("input must be a readable regular file (symlinks are not allowed)") from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ScorecardError("input must be a regular file")
        if info.st_size > remaining:
            raise ScorecardError("combined input size exceeds 2 MiB")
        chunks: list[bytes] = []
        total = 0
        while True:
            block = os.read(fd, min(65536, remaining + 1 - total))
            if not block:
                break
            chunks.append(block)
            total += len(block)
            if total > remaining:
                raise ScorecardError("combined input size exceeds 2 MiB")
        return b"".join(chunks), info
    finally:
        os.close(fd)


def _decode_json(data: bytes, label: str) -> Any:
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=_pairs_no_duplicates,
                          parse_constant=_reject_constant)
    except ScorecardError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise ScorecardError(f"{label} is not valid UTF-8 JSON") from None


def _object(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ScorecardError(f"{label} must be an object with exactly the required fields")
    return value


def _text(value: Any, label: str, limit: int, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value) or len(value) > limit:
        raise ScorecardError(f"{label} must be text within its length limit")
    return value


def _id(value: Any, label: str) -> str:
    if not isinstance(value, str) or not SAFE_ID.fullmatch(value):
        raise ScorecardError(f"{label} must be a safe lowercase ASCII identifier")
    return value


def tokens(text: str) -> list[str]:
    """Casefolded Unicode word/punctuation tokens; underscore is a separator."""
    return [m.group(0).casefold() for m in TOKEN_RE.finditer(text)]


def word_count(text: str) -> int:
    return sum(1 for part in re.split(r"\s+", text.strip()) if part)


def contains_exact_phrase(haystack: list[str], phrase: list[str]) -> bool:
    if not phrase or len(phrase) > len(haystack):
        return False
    width = len(phrase)
    return any(haystack[i:i + width] == phrase for i in range(len(haystack) - width + 1))


def load_and_score(tasks_path: str, answers_path: str) -> dict[str, Any]:
    task_data, _ = _read_regular(tasks_path, MAX_INPUT_BYTES)
    answer_data, _ = _read_regular(answers_path, MAX_INPUT_BYTES - len(task_data))
    tasks_root = _decode_json(task_data, "tasks file")
    root = _object(tasks_root, {"schema_version", "conditions", "tasks"}, "tasks root")
    if type(root["schema_version"]) is not int or root["schema_version"] != 1:
        raise ScorecardError("schema_version must be integer 1")
    conditions = root["conditions"]
    if not isinstance(conditions, list) or not 2 <= len(conditions) <= 8:
        raise ScorecardError("conditions must contain 2 to 8 identifiers")
    conditions = [_id(c, "condition") for c in conditions]
    if len(set(conditions)) != len(conditions):
        raise ScorecardError("condition identifiers must be unique")
    task_list = root["tasks"]
    if not isinstance(task_list, list) or not 1 <= len(task_list) <= 1000:
        raise ScorecardError("tasks must contain 1 to 1000 entries")
    task_map: dict[str, dict[str, Any]] = {}
    for raw in task_list:
        item = _object(raw, {"id", "prompt", "contexts", "required_terms", "forbidden_terms"}, "task")
        tid = _id(item["id"], "task id")
        if tid in task_map:
            raise ScorecardError("task identifiers must be unique")
        _text(item["prompt"], "prompt", MAX_PROMPT_CHARS)
        contexts = _object(item["contexts"], set(conditions), "task contexts")
        for cond, context in contexts.items():
            _text(context, f"context for {cond}", MAX_CONTEXT_CHARS, allow_empty=True)
        required = item["required_terms"]
        forbidden = item["forbidden_terms"]
        if not isinstance(required, list) or not 1 <= len(required) <= 30:
            raise ScorecardError("required_terms must contain 1 to 30 terms")
        if not isinstance(forbidden, list) or not 0 <= len(forbidden) <= 30:
            raise ScorecardError("forbidden_terms must contain 0 to 30 terms")
        for term in required:
            _text(term, "required term", MAX_TERM_CHARS)
        for term in forbidden:
            _text(term, "forbidden term", MAX_TERM_CHARS)
        required_tokens = [tokens(x) for x in required]
        forbidden_tokens = [tokens(x) for x in forbidden]
        if any(not term for term in required_tokens + forbidden_tokens):
            raise ScorecardError("terms must contain at least one non-whitespace token")
        if len({tuple(term) for term in required_tokens}) != len(required_tokens):
            raise ScorecardError("required terms must be unique after normalization")
        if len({tuple(term) for term in forbidden_tokens}) != len(forbidden_tokens):
            raise ScorecardError("forbidden terms must be unique after normalization")
        if {tuple(term) for term in required_tokens} & {tuple(term) for term in forbidden_tokens}:
            raise ScorecardError("required and forbidden terms must not overlap")
        task_map[tid] = {"prompt": item["prompt"], "contexts": contexts,
                         "required": required_tokens, "forbidden": forbidden_tokens}
    expected = {(tid, cond) for tid in task_map for cond in conditions}
    answers: dict[tuple[str, str], dict[str, Any]] = {}
    for line in answer_data.splitlines():
        if not line.strip():
            raise ScorecardError("answers file contains a blank JSONL row")
        row = _object(_decode_json(line, "answers row"),
                      {"task_id", "condition", "answer", "human_review"}, "answer row")
        tid, cond = _id(row["task_id"], "task_id"), _id(row["condition"], "condition")
        if (tid, cond) not in expected:
            raise ScorecardError("answers contain an unknown task-condition pair")
        if (tid, cond) in answers:
            raise ScorecardError("answers contain a duplicate task-condition pair")
        answer = _text(row["answer"], "answer", MAX_ANSWER_CHARS, allow_empty=True)
        review = _object(row["human_review"],
                         {"goal_met", "critical_context_missing", "unsupported_claim_count"},
                         "human_review")
        if type(review["goal_met"]) is not bool or type(review["critical_context_missing"]) is not bool:
            raise ScorecardError("human review decisions must be true or false")
        unsupported = review["unsupported_claim_count"]
        if type(unsupported) is not int or not 0 <= unsupported <= 1000:
            raise ScorecardError("unsupported_claim_count must be an integer from 0 to 1000")
        answers[(tid, cond)] = {"answer": answer, "review": review}
    if set(answers) != expected:
        raise ScorecardError("answers are missing one or more task-condition rows")

    cases: list[dict[str, Any]] = []
    for tid in task_map:
        task = task_map[tid]
        for cond in conditions:
            recorded = answers[(tid, cond)]
            ans = recorded["answer"]
            review = recorded["review"]
            ans_tokens = tokens(ans)
            required_hits = [contains_exact_phrase(ans_tokens, phrase) for phrase in task["required"]]
            forbidden_hit = any(contains_exact_phrase(ans_tokens, phrase) for phrase in task["forbidden"])
            recall = sum(required_hits) / len(required_hits)
            lexical_passed = all(required_hits) and not forbidden_hit
            human_passed = (review["goal_met"] and not review["critical_context_missing"]
                            and review["unsupported_claim_count"] == 0)
            cases.append({"task_id": tid, "condition": cond,
                          "passed": lexical_passed and human_passed,
                          "lexical_passed": lexical_passed, "human_review_passed": human_passed,
                          "required_term_recall": recall, "forbidden_term_matched": forbidden_hit,
                          "goal_met": review["goal_met"],
                          "critical_context_missing": review["critical_context_missing"],
                          "unsupported_claim_count": review["unsupported_claim_count"],
                          "input_context_word_count": word_count(task["contexts"][cond]),
                          "answer_word_count": word_count(ans)})
    groups: dict[str, list[dict[str, Any]]] = {c: [x for x in cases if x["condition"] == c] for c in conditions}
    groups["overall"] = cases
    aggregate = {name: {"case_count": len(rows), "pass_rate": sum(bool(x["passed"]) for x in rows) / len(rows),
                        "lexical_pass_rate": sum(bool(x["lexical_passed"]) for x in rows) / len(rows),
                        "human_review_pass_rate": sum(bool(x["human_review_passed"]) for x in rows) / len(rows),
                        "mean_required_term_recall": sum(x["required_term_recall"] for x in rows) / len(rows),
                        "forbidden_case_rate": sum(bool(x["forbidden_term_matched"]) for x in rows) / len(rows),
                        "goal_met_rate": sum(bool(x["goal_met"]) for x in rows) / len(rows),
                        "critical_context_missing_rate": sum(bool(x["critical_context_missing"]) for x in rows) / len(rows),
                        "mean_unsupported_claim_count": sum(x["unsupported_claim_count"] for x in rows) / len(rows),
                        "mean_input_context_word_count": sum(x["input_context_word_count"] for x in rows) / len(rows),
                        "mean_answer_word_count": sum(x["answer_word_count"] for x in rows) / len(rows)}
                  for name, rows in groups.items()}
    return {"schema_version": 1, "conditions": conditions, "cases": cases, "aggregates": aggregate,
            "methodology": {"matching": "casefolded exact normalized token-sequence matches; Unicode words and punctuation",
                            "word_counts": "Whitespace-delimited word counts; these are not model tokens.",
                            "human_review": "Human-supplied goal_met, critical_context_missing, and unsupported_claim_count; the tool does not verify reviewer judgments.",
                            "limitation": "Exact phrase recall is a lexical proxy, not semantic evaluation or factual verification. Human review is subjective and should be blinded or independently checked."}}


def write_atomic(path: str, report: dict[str, Any], input_paths: tuple[str, str]) -> None:
    target = Path(path)
    try:
        absolute = target.absolute()
        for source in input_paths:
            if absolute == Path(source).absolute():
                raise ScorecardError("output must not overwrite an input")
            try:
                if os.path.exists(target) and os.path.samefile(target, source):
                    raise ScorecardError("output must not overwrite an input")
            except OSError:
                pass
        try:
            mode = os.lstat(target).st_mode
        except FileNotFoundError:
            mode = None
        if mode is not None and (stat.S_ISLNK(mode) or not stat.S_ISREG(mode)):
            raise ScorecardError("output must be a regular file, not a symlink or special file")
        payload = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        fd, temp_name = tempfile.mkstemp(prefix=".handoff-scorecard-", dir=str(target.parent or Path(".")))
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, target)
        except BaseException:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise
    except ScorecardError:
        raise
    except OSError:
        raise ScorecardError("could not write output file") from None
