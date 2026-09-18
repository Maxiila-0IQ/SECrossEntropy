"""Stress test runner for GridWise LLM interpreter."""

import argparse
import json
import logging
import sys

from llm import DeepSeekClient, LLMInterpreter
from tests.stress_tests import STRESS_TESTS

NUMERIC_TOLERANCE = 0.01


def adjustment_equal(actual: dict | None, expected: dict | None, tol: float = NUMERIC_TOLERANCE) -> bool:
    if expected is None:
        return actual is None
    if actual is None:
        return False
    for key, exp_val in expected.items():
        got_val = actual.get(key)
        if isinstance(exp_val, float):
            if got_val is None or abs(got_val - exp_val) > tol:
                return False
        elif isinstance(exp_val, list) and exp_val and isinstance(exp_val[0], (int, float)):
            if got_val is None or got_val != exp_val:
                return False
        else:
            if got_val != exp_val:
                return False
    return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run stress tests against the LLM interpreter.")
    parser.add_argument("--deepseek", action="store_true", help="Use DeepSeek API.")
    parser.add_argument("--model", default=None, help="Model name override.")
    parser.add_argument("--base-url", default=None, help="API base URL override.")
    parser.add_argument("--category", type=int, default=None, help="Run only this category (1-7).")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show DEBUG logs.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    log_level = logging.DEBUG if args.verbose else logging.WARNING
    logging.basicConfig(
        level=log_level,
        format="%(name)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )

    if args.deepseek:
        import os
        api_key = os.environ.get("DEEPSEEK_API_KEY", "")
        base_url = args.base_url or os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
        model = args.model or os.environ.get("DEEPSEEK_MODEL", "deepseek-flash")
        if not api_key:
            print("ERROR: Set DEEPSEEK_API_KEY", file=sys.stderr)
            sys.exit(1)
    else:
        api_key = "not-needed"
        base_url = args.base_url or "http://0.0.0.0:8080/v1"
        model = args.model or "/models/Qwen3-14B-Q5_K_M.gguf"

    client = DeepSeekClient(api_key=api_key, base_url=base_url, model=model, timeout=30.0)
    interpreter = LLMInterpreter(client=client, max_retries=1, max_tokens=1200)

    tests = STRESS_TESTS
    if args.category is not None:
        category_ranges = {
            1: (0, 29),   # Paraphrase families (29 tests)
            2: (29, 35),  # Time edge cases (6 tests)
            3: (35, 43),  # Solar factor traps (8 tests)
            4: (43, 49),  # Charge vs discharge (6 tests)
            5: (49, 55),  # Vague/insufficient (6 tests)
            6: (55, 60),  # Adversarial (5 tests)
            7: (60, 62),  # Multi-note (2 tests)
        }
        start, end = category_ranges.get(args.category, (0, len(STRESS_TESTS)))
        tests = STRESS_TESTS[start:end]

    total = len(tests)
    passed = 0
    failed = 0
    failures = []

    # Error categories
    classification_errors = 0
    hour_errors = 0
    numeric_errors = 0
    mapping_errors = 0

    print(f"Model: {model}", file=sys.stderr)
    print(f"Base URL: {base_url}", file=sys.stderr)
    print(f"Tests: {total}", file=sys.stderr)
    print(file=sys.stderr)

    for scenario_id, notes, expected in tests:
        result = interpreter.interpret(notes, scenario_id=scenario_id)
        ok = True

        if len(result.directive_interpretation) != len(expected):
            failures.append(f"{scenario_id}: entry count={len(result.directive_interpretation)} expected={len(expected)}")
            ok = False
            mapping_errors += 1
        else:
            for i, (exp_type, exp_applies, exp_adj) in enumerate(expected):
                e = result.directive_interpretation[i]
                if e.directive_type != exp_type:
                    failures.append(f"{scenario_id}[{i}]: type={e.directive_type} expected={exp_type}")
                    ok = False
                    classification_errors += 1
                elif e.applies != exp_applies:
                    failures.append(f"{scenario_id}[{i}]: applies={e.applies} expected={exp_applies}")
                    ok = False
                    classification_errors += 1
                elif exp_adj is not None and not adjustment_equal(e.structured_adjustment, exp_adj):
                    # Determine error subtype
                    if e.structured_adjustment and exp_adj:
                        got_hours = set(e.structured_adjustment.get("hours", []))
                        exp_hours = set(exp_adj.get("hours", []))
                        if got_hours != exp_hours:
                            failures.append(f"{scenario_id}[{i}]: hours={sorted(got_hours)} expected={sorted(exp_hours)}")
                            hour_errors += 1
                        else:
                            failures.append(f"{scenario_id}[{i}]: adj={e.structured_adjustment} expected={exp_adj}")
                            numeric_errors += 1
                    else:
                        failures.append(f"{scenario_id}[{i}]: adj={e.structured_adjustment} expected={exp_adj}")
                    ok = False

        if ok:
            passed += 1
            print(f"  PASS {scenario_id}", file=sys.stderr)
        else:
            failed += 1
            print(f"  FAIL {scenario_id}", file=sys.stderr)

    print(file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)
    print(f"RESULTS: {passed}/{total} passed ({100*passed/total:.1f}%)", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)

    if failures:
        print(file=sys.stderr)
        print("FAILURES:", file=sys.stderr)
        for f in failures:
            print(f"  {f}", file=sys.stderr)

    print(file=sys.stderr)
    print("ERROR BREAKDOWN:", file=sys.stderr)
    print(f"  classification: {classification_errors}", file=sys.stderr)
    print(f"  hours:          {hour_errors}", file=sys.stderr)
    print(f"  numeric:        {numeric_errors}", file=sys.stderr)
    print(f"  mapping:        {mapping_errors}", file=sys.stderr)

    summary = {
        "total": total,
        "passed": passed,
        "failed": failed,
        "pass_rate": round(100 * passed / total, 1),
        "errors": {
            "classification": classification_errors,
            "hours": hour_errors,
            "numeric": numeric_errors,
            "mapping": mapping_errors,
        },
        "failures": failures,
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
