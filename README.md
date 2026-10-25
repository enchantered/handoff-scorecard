# handoff-scorecard

An offline, standard-library-only scorecard for developers manually comparing how coding agents resume interrupted tasks with repository-only context, ordinary notes, or a structured handoff packet. It pairs a reproducible exact-phrase proxy with a small human-reviewed outcome checklist. It is a portfolio experiment and local QA aid—not a benchmark of factual correctness or general agent capability.

## Requirements and setup

Python 3.11 or newer. There are no third-party dependencies, network calls, model calls, or installation step:

```sh
cd handoff-scorecard
python3 --version
```

## Run the synthetic example

```sh
python3 -m handoff_scorecard score examples/tasks.json examples/answers.jsonl
```

The example has 3 synthetic tasks and 9 task-condition rows. Its repository-only condition has a 0.333 lexical pass rate, 0.0 human-review pass rate, and 0.0 composite pass rate; notes and packet each score 1.0 on all three measures. Overall lexical, human-review, and composite pass rates are approximately 0.778, 0.667, and 0.667. These deliberately varied synthetic outcomes are for demonstrating the report only; they are not evidence that a context condition performs better.

Save the report atomically to a new or existing regular file:

```sh
python3 -m handoff_scorecard score examples/tasks.json examples/answers.jsonl --output report.json
```

Use `--fail-on-miss` to return exit status 1 if any composite case fails; malformed input returns status 2. Without `--output`, JSON is written to stdout. Reports and errors do not include supplied prompts, contexts, or answer text; case rows do include task and condition IDs plus review flags and counts.

## Input schema

Tasks are UTF-8 JSON with exactly these root fields:

```json
{"schema_version":1,"conditions":["repository","notes","packet"],"tasks":[{"id":"task-1","prompt":"Synthetic prompt","contexts":{"repository":"...","notes":"...","packet":"..."},"required_terms":["expected phrase"],"forbidden_terms":[]}]}
```

Use 2–8 unique safe IDs for conditions and 1–1000 tasks. Task IDs are unique and IDs match `[a-z0-9][a-z0-9._-]{0,63}`. Each task has exactly one context for each condition. Prompt, context, and terms are length-bounded; required terms number 1–30 and forbidden terms 0–30. Normalized terms must be unique, non-empty, and non-contradictory. See `examples/tasks.json` for a complete synthetic file.

Answers are UTF-8 JSON Lines, exactly one object per task-condition pair, with exactly `task_id`, `condition`, `answer`, and `human_review` fields. The review object is `{"goal_met":true,"critical_context_missing":false,"unsupported_claim_count":0}`. `goal_met` and `critical_context_missing` are booleans; `unsupported_claim_count` is an integer from 0 to 1000. Unknown, duplicate, or missing task-condition pairs are errors. See `examples/answers.jsonl`.

## Scoring

Terms and answers are tokenized as Unicode word/punctuation sequences, casefolded, then compared as exact contiguous token sequences. Every required term must occur and no forbidden term may occur for a lexical pass. Required-term recall is the fraction of required phrases matched.

A human-review pass requires `goal_met=true`, `critical_context_missing=false`, and `unsupported_claim_count=0`. The composite `passed` value requires both a lexical pass and a human-review pass. The program checks the review fields' types and ranges, but **does not verify the reviewer's judgment**. Reviewers should see the prompt and actual answer, not only the summary, and should mark the same criteria across conditions. If practical, hide condition labels from the reviewer, randomize review order, and use a second reviewer on a sample to estimate disagreement.

Aggregate rates are simple, unweighted means across cases. Context and answer “word counts” are whitespace-delimited word counts, **not model tokens**. Exact phrase recall is a **lexical proxy, not semantic evaluation**: paraphrases can miss, and a matched phrase can still be wrong or misleading. Human review is subjective and can be biased.

## Gathering agent responses outside this tool

For each task, manually prepare the same prompt and context conditions. In separate agent sessions, provide only the context intended for that condition and ask for a response; keep model/version, settings, tool permissions, and task protocol consistent as far as possible. Copy answers and human judgments into the JSONL file. Review transcripts for protocol deviations, randomize the review order, and record run metadata outside the scorecard. This program never launches agents, collects transcripts, or checks that an agent received only the declared context.

## Code map

- `handoff_scorecard/core.py` validates bounded input files, matches rubric phrases, combines human-review labels, aggregates metrics, and writes reports safely.
- `handoff_scorecard/__main__.py` provides the `score` command and exit codes.
- `examples/` contains only synthetic tasks and answer records.
- `tests/` covers input/schema boundaries, lexical and human scoring, privacy of output, and CLI behavior.
- `docs/` records the dated evidence and the project's place in the portfolio arc.

## Limitations and threat model

This is local/offline software that treats input as inert text and never evaluates or executes supplied content. Input files must be regular non-symlink files, are bounded to 2 MiB combined, decoded as UTF-8, and schema-checked. Errors are concise and avoid echoing supplied text. Output rejects input overwrite, symlinks, and non-regular files and uses a same-directory temporary file plus atomic replacement. As with ordinary local tools, it does not defend against a concurrently malicious process changing filesystem paths, hostile filesystem/kernel behavior, resource exhaustion beyond the input bounds, or confidentiality risks in the user's own environment. Do not put secrets in prompts, answers, or contexts; inputs stay local, but users control their filesystem and backups. Output reports contain IDs, counts, and the structured human-review fields—not raw text.

Keep real evaluation inputs and transcripts outside the Git repository or use the ignored private-run filenames/directories in `.gitignore`; review `git status` before committing. The tracked `examples/answers.jsonl` is synthetic and intentionally unignored.

This first version does not measure task completion by execution, tool-use compliance, time, cost, repeated-run variance, or inter-rater agreement. It should not be used to claim general task success or factual correctness.

## Project context and reuse

See [`docs/2026-10-25-decision.md`](docs/2026-10-25-decision.md) for dated research, competition, and scope rationale; [`docs/consumer-behavior-log.md`](docs/consumer-behavior-log.md) for continuity; and [`docs/portfolio-arc.md`](docs/portfolio-arc.md) for the adaptive portfolio sequence. The repository currently has no license file; public availability should not be read as a license grant. Examples are synthetic and no third-party code/assets are bundled.
