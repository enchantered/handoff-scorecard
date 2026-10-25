import argparse
import json
import sys

from .core import ScorecardError, load_and_score, write_atomic


def main(argv=None):
    parser = argparse.ArgumentParser(prog="handoff-scorecard", description="Offline lexical handoff scorecard")
    sub = parser.add_subparsers(dest="command", required=True)
    score = sub.add_parser("score", help="score a complete task/answer set")
    score.add_argument("tasks")
    score.add_argument("answers")
    score.add_argument("--output")
    score.add_argument("--fail-on-miss", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = load_and_score(args.tasks, args.answers)
        if args.output:
            write_atomic(args.output, report, (args.tasks, args.answers))
        else:
            sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        return 1 if args.fail_on_miss and any(not case["passed"] for case in report["cases"]) else 0
    except ScorecardError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
