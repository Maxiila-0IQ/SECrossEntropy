"""Batch runner: process sample JSONs through the full llm/ -> app/ pipeline."""

import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

from app.config import settings
from app.schemas import ScenarioRequest
from app.interpret import interpret_notes
from app.constraints import build_constraints
from app.optimize import solve_and_build
from app.replay import validate_plan
from app.baseline import build_baseline

logger = logging.getLogger("samples")


def load_sample(path: Path) -> dict:
    with open(path) as f:
        return json.load(f)


def dict_to_scenario(data: dict) -> ScenarioRequest:
    return ScenarioRequest(**data)


async def run_one(path: Path, verbose: bool = False) -> dict:
    data = load_sample(path)
    scenario = dict_to_scenario(data)
    t0 = time.perf_counter()

    entries, interp_path = await interpret_notes(scenario.operator_notes, scenario.battery)
    interp_ms = round((time.perf_counter() - t0) * 1000, 1)

    cons = build_constraints(scenario, entries)
    t1 = time.perf_counter()
    result = await asyncio.to_thread(solve_and_build, scenario, cons)
    solve_ms = round((time.perf_counter() - t1) * 1000, 1)

    violations = None
    replays_clean = False
    if result is not None:
        violations = validate_plan(scenario, cons, result.plan, result.totals)
        replays_clean = not violations and abs(
            result.plan[-1].battery_energy_after_kwh - scenario.battery.initial_energy_kwh
        ) < 0.01

    total_ms = round((time.perf_counter() - t0) * 1000, 1)

    if result is not None and replays_clean:
        plan = result.plan
        t = result.totals
        n_charged = sum(1 for p in plan if p.battery_action == "charge")
        n_discharged = sum(1 for p in plan if p.battery_action == "discharge")
        summary = (
            f"Optimized: grid={t['total_grid_kwh']} kWh, "
            f"cost={t['total_cost_bdt']} BDT, "
            f"peak={t['peak_grid_kwh']} kWh, "
            f"charged={n_charged}h, discharged={n_discharged}h"
        )
        status = "optimal"
    else:
        base = build_baseline(scenario, cons.eff_solar)
        plan = base.hourly_plan
        summary = f"Baseline fallback (violations={violations})"
        status = "baseline"

    entry_dicts = [
        {
            "note_index": e.note_index,
            "applies": e.applies,
            "directive_type": e.directive_type,
            "structured_adjustment": e.structured_adjustment,
            "explanation": e.explanation,
        }
        for e in entries
    ]

    return {
        "scenario_id": scenario.scenario_id,
        "status": status,
        "interp_path": interp_path,
        "interp_ms": interp_ms,
        "solve_ms": solve_ms,
        "total_ms": total_ms,
        "directive_interpretation": entry_dicts,
        "plan_summary": summary,
        "replay_pass": replays_clean,
        "violations": violations,
    }


async def main():
    samples_dir = Path("tests/samples")
    sample_files = sorted(samples_dir.glob("SAMPLE-*.json"))

    if not sample_files:
        print("No sample files found in tests/samples/", file=sys.stderr)
        sys.exit(1)

    log_level = logging.DEBUG if "--verbose" in sys.argv else logging.WARNING
    logging.basicConfig(
        level=log_level,
        format="%(name)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )

    api_key = os.environ.get("GROQ_API_KEY", os.environ.get("OPENAI_API_KEY", ""))
    if api_key:
        settings.GROQ_API_KEY = api_key
    else:
        logger.info("No GROQ_API_KEY; interpret_notes will use fallback parser")

    print(f"Processing {len(sample_files)} samples...", file=sys.stderr)
    print(file=sys.stderr)

    results = []
    for path in sample_files:
        print(f"  {path.name}...", file=sys.stderr, end=" ", flush=True)
        r = await run_one(path, verbose="--verbose" in sys.argv)
        results.append(r)
        print(f"{r['status']} ({r['total_ms']}ms)", file=sys.stderr)

    print(file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)
    pass_count = sum(1 for r in results if r["replay_pass"])
    print(f"RESULTS: {pass_count}/{len(results)} passed replay validation", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)

    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
