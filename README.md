# GridWise — Smart Campus Energy Optimizer

**BUP CSE Fest 2026 Hackathon**

Operator writes plain-English notes about campus energy constraints. An LLM interprets them, a deterministic guardrail validates and repairs the interpretation, a PuLP optimizer schedules battery and grid, and an independent replay validator recomputes everything to prove correctness.

## Architecture

```
Operator Notes (natural language)
        │
        ▼
   ┌─────────┐
   │   LLM   │  DeepSeek-V4.1-Flash via OpenAI-compatible API
   └────┬────┘
        │ structured directives (JSON)
        ▼
   ┌───────────┐
   │ Guardrails│  deterministic validation + repair
   └────┬──────┘
        │ corrected directives
        ▼
   ┌───────────┐
   │ Optimizer │  PuLP LP solver (CBC)
   └────┬──────┘
        │ 24-hour plan
        ▼
   ┌──────────┐
   │  Replay  │  independent recomputation of all constraints
   └────┬─────┘
        │ pass/fail + violations
        ▼
   Final JSON response
```

## Stack

| Layer | Technology |
|-------|-----------|
| LLM | deepseek-flash (DeepSeek API) |
| Backend | FastAPI + Uvicorn |
| Optimizer | PuLP (CBC solver) with SciPy fallback |
| Validation | Pydantic v2 + custom replay validator |
| Python | 3.11+ |

## Quick Start

```bash
git clone https://github.com/Maxiila-0IQ/SECrossEntropy.git
cd SECrossEntropy
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Set your DeepSeek API key:

```bash
export DEEPSEEK_API_KEY="sk-your-key-here"
```

Start the server:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## API

### Health Check

```bash
curl http://localhost:8000/health
```

### Optimize Energy

```bash
curl -X POST http://localhost:8000/optimize-energy \
  -H "Content-Type: application/json" \
  -d '{
    "scenario_id": "demo",
    "operator_notes": [
      "Wash solar panels from noon to 2 PM. Output drops to 25%.",
      "Keep at least 100 kWh battery reserve from 6 PM to 10 PM."
    ],
    "hours": [
      {"hour": 0, "demand_kwh": 80, "solar_kwh": 0, "tariff_bdt_per_kwh": 6.0},
      {"hour": 1, "demand_kwh": 80, "solar_kwh": 0, "tariff_bdt_per_kwh": 6.0},
      {"hour": 2, "demand_kwh": 80, "solar_kwh": 0, "tariff_bdt_per_kwh": 6.0},
      {"hour": 3, "demand_kwh": 80, "solar_kwh": 0, "tariff_bdt_per_kwh": 6.0},
      {"hour": 4, "demand_kwh": 80, "solar_kwh": 0, "tariff_bdt_per_kwh": 6.0},
      {"hour": 5, "demand_kwh": 80, "solar_kwh": 0, "tariff_bdt_per_kwh": 6.0},
      {"hour": 6, "demand_kwh": 80, "solar_kwh": 30, "tariff_bdt_per_kwh": 6.0},
      {"hour": 7, "demand_kwh": 80, "solar_kwh": 60, "tariff_bdt_per_kwh": 6.0},
      {"hour": 8, "demand_kwh": 80, "solar_kwh": 100, "tariff_bdt_per_kwh": 6.0},
      {"hour": 9, "demand_kwh": 80, "solar_kwh": 140, "tariff_bdt_per_kwh": 6.0},
      {"hour": 10, "demand_kwh": 80, "solar_kwh": 170, "tariff_bdt_per_kwh": 6.0},
      {"hour": 11, "demand_kwh": 80, "solar_kwh": 190, "tariff_bdt_per_kwh": 6.0},
      {"hour": 12, "demand_kwh": 80, "solar_kwh": 200, "tariff_bdt_per_kwh": 6.0},
      {"hour": 13, "demand_kwh": 80, "solar_kwh": 190, "tariff_bdt_per_kwh": 6.0},
      {"hour": 14, "demand_kwh": 80, "solar_kwh": 170, "tariff_bdt_per_kwh": 6.0},
      {"hour": 15, "demand_kwh": 80, "solar_kwh": 140, "tariff_bdt_per_kwh": 6.0},
      {"hour": 16, "demand_kwh": 80, "solar_kwh": 100, "tariff_bdt_per_kwh": 6.0},
      {"hour": 17, "demand_kwh": 80, "solar_kwh": 60, "tariff_bdt_per_kwh": 6.0},
      {"hour": 18, "demand_kwh": 80, "solar_kwh": 30, "tariff_bdt_per_kwh": 8.0},
      {"hour": 19, "demand_kwh": 80, "solar_kwh": 0, "tariff_bdt_per_kwh": 8.0},
      {"hour": 20, "demand_kwh": 80, "solar_kwh": 0, "tariff_bdt_per_kwh": 8.0},
      {"hour": 21, "demand_kwh": 80, "solar_kwh": 0, "tariff_bdt_per_kwh": 8.0},
      {"hour": 22, "demand_kwh": 80, "solar_kwh": 0, "tariff_bdt_per_kwh": 6.0},
      {"hour": 23, "demand_kwh": 80, "solar_kwh": 0, "tariff_bdt_per_kwh": 6.0}
    ],
    "battery": {
      "capacity_kwh": 500,
      "initial_energy_kwh": 250,
      "minimum_energy_kwh": 10,
      "max_charge_kwh_per_hour": 50,
      "max_discharge_kwh_per_hour": 50
    }
  }'
```

## Supported Directives

| Directive | structured_adjustment | Effect |
|-----------|----------------------|--------|
| `solar_reduction` | `{"hours": [...], "factor": float}` | Solar output multiplied by factor |
| `minimum_battery_reserve` | `{"hours": [...], "minimum_energy_kwh": float}` | Minimum battery energy after hour |
| `no_charge_window` | `{"hours": [...]}` | Charging disabled |
| `no_discharge_window` | `{"hours": [...]}` | Discharging disabled |
| `max_grid_window` | `{"hours": [...], "max_grid_kwh": float}` | Grid import cap per hour |
| `no_op` | `null` | No energy directive |

## Replay Validator Checks

The replay validator independently recomputes every constraint the judge checks:

- Effective solar (solar_used ≤ effective_solar per hour)
- Energy balance (grid + solar + discharge = demand + charge)
- Battery transition (E tracks charge/discharge correctly)
- Capacity limits (E ≤ capacity_kwh)
- Minimum reserve (E ≥ minimum_energy_kwh)
- Charge/discharge rate limits
- No-charge / no-discharge window compliance
- Grid caps
- End-of-day neutrality (E[23] = initial energy)
- Totals recomputation (grid, cost, peak)

## Docker

```bash
docker build -t gridwise .
docker run -p 8000:8000 -e DEEPSEEK_API_KEY="sk-your-key" gridwise
```

```bash
curl http://localhost:8000/health
```

## Reproducibility

- All constraints are deterministic (no randomness in optimizer or guardrails)
- LLM output is validated and repaired by guardrails before optimization
- Replay validator recomputes everything independently — if replay passes, the solution is correct
- Sample JSONs in `tests/samples/` provide fixed test cases
- LLM call has 8s timeout with automatic fallback to regex parser if LLM is unavailable

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DEEPSEEK_API_KEY` | (required) | DeepSeek API key |
| `LLM_MODEL` | `deepseek-flash` | Model identifier |
| `LLM_BASE_URL` | `https://api.deepseek.com` | API base URL |
| `LLM_TIMEOUT` | `8` | LLM call timeout (seconds) |
| `SOLVER_BACKEND` | `pulp` | Solver: `pulp` |
