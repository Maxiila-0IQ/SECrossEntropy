# GridWise — Campus Energy Optimization API

FastAPI service that interprets campus operator notes in natural language, turns them into
constraints, and solves a 24-hour energy arbitrage LP to minimize cost (then peak) for a
building with solar + battery + grid import.

## Architecture

```
request ─► interpret.py (LLM ──► guardrails ──► fallback parser) ──► constraints.py
                                                        └────► optimize.py (two-phase LP)
                                                                        │
                                                        replay.py (validation) ◄─┘
                                                                        │
                                                                   response
```

- **Interpretation chain** (`app/interpret.py`): one LLM call per request via Groq
  (`openai/gpt-oss-120b`), JSON mode, temperature 0, 8 s timeout, one retry appending
  guardrail errors. Degrades to the deterministic `app/fallback.py` regex parser on
  timeout / malformed output / missing API key — the service never 500s on interpretation.
- **Guardrails** (`app/guardrails.py`): validate, repair, and reconcile LLM output into
  exactly `N` `DirectiveEntry` objects; unknown/malformed entries are demoted to `no_op`.
- **Constraints** (`app/constraints.py`): per-hour effective solar (product of reduction
  factors) and minimum battery energy (max of static reserve and window reserves).
- **Optimizer** (`app/optimize.py`): PuLP + CBC (scipy `linprog` fallback). Two phases:
  min cost, then min peak holding cost within 1% of optimal. Runs in a worker thread.
- **Replay** (`app/replay.py`): verifies the emitted plan — energy balance, neutrality,
  cap compliance, battery bounds, reserve windows.

## Quickstart

```bash
# Python 3.11 + deps
conda env create -f environment.yml   # or: pip install -r requirements.txt

# solver binary (macOS): ensure cbc is on PATH or set CBC_PATH
export CBC_PATH="$(which cbc)"          # e.g. /opt/miniconda3/envs/gridwise/bin/cbc

# API key (optional — gated by growpath; without it the fallback parser is used)
export GROQ_API_KEY="gsk_..."           # see .env.example

uvicorn app.main:app --port 8000
```

## API

### `GET /health`

```json
{"status": "ok", "version": "0.1.0"}
```

### `POST /optimize-energy`

Request — 24 hours, exactly one of each hour `0..23` (order-independent), 1–3 notes:

```json
{
  "scenario_id": "demo",
  "operator_notes": ["No grid import from 10 PM until 2 AM.",
                     "Keep at least 120 kWh in reserve from 6 PM until 9 PM."],
  "hours": [{"hour": 0, "demand_kwh": 120.0, "solar_kwh": 0.0,
             "tariff_bdt_per_kwh": 6.4}, "... 24 total ..."],
  "battery": {"capacity_kwh": 200.0, "initial_energy_kwh": 100.0,
              "minimum_energy_kwh": 0.0, "max_charge_kwh_per_hour": 50.0,
              "max_discharge_kwh_per_hour": 50.0}
}
```

Response — directives, 24-hour plan, and totals:

```json
{
  "scenario_id": "demo",
  "directive_interpretation": [ {"note_index": 0, "applies": true,
     "directive_type": "max_grid_window",
     "structured_adjustment": {"hours": [0, 1, 22, 23], "max_grid_kwh": 0.0}, "explanation": "..."} ],
  "hourly_plan": [ {"hour": 0, "grid_kwh": 100.0, "solar_used_kwh": 0.0,
                    "battery_action": "discharge", "battery_kwh": 50.0,
                    "battery_energy_after_kwh": 50.0}, "... 24 total ..."],
  "total_grid_kwh": 2415.0,
  "total_cost_bdt": 35480.0,
  "peak_grid_kwh": 175.0,
  "plan_summary": "Minimized cost with a no grid import window (00:00-02:00, 22:00-24:00) and a battery reserve of 120 kWh (18:00-21:00)."
}
```

Semantics: windows are **start-inclusive, end-exclusive**; a "10 PM–2 AM" window wraps to
`[0, 1, 22, 23]`. Solar-reduction `factor` is the **fraction remaining** (80% reduction
→ `0.20`). Battery is cost-neutral (state restored by end of day).

## Tests

```bash
CBC_PATH="$(which cbc)" python -m pytest tests/ -q      # 87 tests
```

Covers the 10 public sample cases end-to-end (exact totals + peak), §15 paraphrases
through both LLM and fallback paths, guardrail repair/reconcile, replay corruption
detection, and LLM degradation (malformed / invalid JSON / timeout → fallback).

## Layout

```
agent.md              full build spec (§1–§18) and acceptance gates
app/
  config.py           settings (env-driven), CBC path auto-detect
  schemas.py          Pydantic request/response, cross-field validators
  interpret.py        LLM call, prompt, percent-of-capacity resolver, fallback chain
  fallback.py         deterministic regex parser
  guardrails.py       LLM output validation/repair
  constraints.py      DirectiveEntry → per-hour constraints
  optimize.py         two-phase LP (PuLP/CBC, scipy fallback)
  replay.py           plan validation
  baseline.py         idle-battery baseline plan
  main.py             FastAPI app + orchestration
tests/                unit + API suites, tests/cases.json public samples
```