# GridWise — LLM-Assisted Smart Campus Energy Optimization

LLM interpreter module for the BUP CSE Fest 2026 hackathon challenge.

Converts natural-language operator notes into structured energy directives using an LLM.

## Project Structure

```
llm/
  __init__.py          # Public exports
  models.py            # Pydantic models (DirectiveType, DirectiveInterpretationResponse)
  errors.py            # Error hierarchy (LLMError, RetryableLLMError, etc.)
  prompts.py           # System prompt (gridwise-llm-v3) and user prompt builder
  client.py            # LLMClient — OpenAI-compatible API client
  interpreter.py       # LLMInterpreter — retry, backoff, safe no_op fallback
app/
  main.py              # FastAPI endpoints (/health, /optimize-energy)
  interpret.py         # Bridges llm/ module to app/ guardrails
  guardrails.py        # Validates LLM output against constraints
  constraints.py       # Builds optimization constraints from directives
  optimize.py          # LP solver (PuLP)
  schemas.py           # Pydantic request/response models
  config.py            # Settings (Groq API, solver config)
tests/
  stress_tests.py      # 62 stress tests across 7 categories
  samples/             # 10 sample JSONs for end-to-end testing
run_samples.py         # Batch runner: sample JSONs through full pipeline
run_llm_test.py        # CLI for single-note testing
run_stress_tests.py    # Stress test runner
```

## Supported Directives

| Directive | structured_adjustment | Description |
|---|---|---|
| `solar_reduction` | `{"hours": [...], "factor": float}` | Usable solar multiplied by factor |
| `minimum_battery_reserve` | `{"hours": [...], "minimum_energy_kwh": float}` | Min battery energy after listed hours |
| `no_charge_window` | `{"hours": [...]}` | Battery charging = 0 |
| `no_discharge_window` | `{"hours": [...]}` | Battery discharging = 0 |
| `max_grid_window` | `{"hours": [...], "max_grid_kwh": float}` | Grid import cap per hour |
| `no_op` | `null` | No supported energy directive |

## Environment Variables

```bash
export GROQ_API_KEY="your-groq-api-key"
# Optional overrides:
# export LLM_MODEL="openai/gpt-oss-20b"
# export LLM_BASE_URL="https://api.groq.com/openai/v1"
# export LLM_TIMEOUT="8"
```

## Usage in FastAPI

```python
from llm import LLMInterpreter, LLMClient

client = LLMClient(
    api_key="your-groq-api-key",
    base_url="https://api.groq.com/openai/v1",
    model="openai/gpt-oss-20b",
    timeout=30.0,
)
interpreter = LLMInterpreter(client=client, max_retries=1, max_tokens=1200)

result = interpreter.interpret(["Solar drops to 20% from 1 PM to 3 PM."])
print(result.model_dump())
```

### Response Format

```json
{
  "directive_interpretation": [
    {
      "note_index": 0,
      "applies": true,
      "directive_type": "solar_reduction",
      "structured_adjustment": {"hours": [13, 14], "factor": 0.2},
      "explanation": "Solar output reduced to 20% during hours 13 and 14."
    }
  ]
}
```

## Sample Pipeline

Process all 10 sample JSONs through the full pipeline (LLM → guardrails → optimizer):

```bash
python run_samples.py
```

Each sample is a complete `ScenarioRequest` with operator notes, 24-hour demand/solar/tariff data, and battery config. The runner outputs optimization results and replay validation status.

## CLI Testing

```bash
# Single note
python run_llm_test.py -n "No charging from 2 PM to 5 PM."

# Multiple notes
python run_llm_test.py -n "Solar drops to 20%." -n "Battery reserve at least 100 kWh."
```

## Stress Tests

```bash
# Run all 62 tests
python run_stress_tests.py

# Run specific category (1-7)
python run_stress_tests.py --category 3
```

### Categories

1. Paraphrase families (29 tests)
2. Time edge cases (6 tests)
3. Solar factor traps (8 tests)
4. Charge vs discharge ambiguity (6 tests)
5. Vague/insufficient notes (6 tests)
6. Adversarial / injection (5 tests)
7. Multi-note requests (2 tests)
