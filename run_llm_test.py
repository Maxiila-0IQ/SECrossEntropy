"""LLM Interpreter CLI — test operator-note interpretation from the terminal."""

import argparse
import logging
import os
import sys

from llm import LLMClient, LLMInterpreter


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Interpret operator notes into energy directives using an LLM.",
    )
    parser.add_argument(
        "-n", "--note",
        action="append",
        required=True,
        metavar="NOTE",
        help="Operator note to interpret (repeatable, 1-3 notes).",
    )
    parser.add_argument(
        "-s", "--scenario-id",
        default="CLI-001",
        metavar="ID",
        help="Scenario identifier (default: CLI-001).",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Model name override.",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="API base URL override.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="API timeout in seconds (default: 30).",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=1,
        help="Max retry attempts (default: 1).",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=1200,
        help="Max output tokens (default: 1200).",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Show DEBUG logs.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress all logs (JSON to stdout only).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.quiet:
        log_level = logging.CRITICAL
    elif args.verbose:
        log_level = logging.DEBUG
    else:
        log_level = logging.INFO

    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )

    api_key = os.environ.get("GROQ_API_KEY", os.environ.get("OPENAI_API_KEY", ""))
    if not api_key:
        print("ERROR: Set GROQ_API_KEY env var", file=sys.stderr)
        sys.exit(1)

    base_url = args.base_url or os.environ.get("LLM_BASE_URL", "https://api.groq.com/openai/v1")
    model = args.model or os.environ.get("LLM_MODEL", "openai/gpt-oss-20b")

    client = LLMClient(
        api_key=api_key,
        base_url=base_url,
        model=model,
        timeout=args.timeout,
    )
    interpreter = LLMInterpreter(
        client=client,
        max_retries=args.max_retries,
        max_tokens=args.max_tokens,
    )

    notes = args.note

    print(f"Model: {model}", file=sys.stderr)
    print(f"Base URL: {base_url}", file=sys.stderr)
    print(f"Notes ({len(notes)}):", file=sys.stderr)
    for i, note in enumerate(notes):
        print(f"  [{i}] {note}", file=sys.stderr)
    print(file=sys.stderr)

    result = interpreter.interpret(notes, scenario_id=args.scenario_id)

    print(result.model_dump_json(indent=2))

    print(file=sys.stderr)
    print("--- Interpretation Summary ---", file=sys.stderr)
    for entry in result.directive_interpretation:
        adj = entry.structured_adjustment
        status = "APPLIES" if entry.applies else "no_op"
        print(
            f"  Note {entry.note_index}: {entry.directive_type} ({status}) "
            f"adjustment={adj}",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
