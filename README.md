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
  client.py            # DeepSeekClient — OpenAI-compatible API client
  interpreter.py       # LLMInterpreter — retry, backoff, safe no_op fallback
tests/
  stress_tests.py      # 62 stress tests across 7 categories
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

## Usage in FastAPI

```python
from llm import LLMInterpreter, DeepSeekClient

# One-time setup
client = DeepSeekClient(
    api_key="your-deepseek-api-key",
    base_url="https://api.deepseek.com",
    model="deepseek-flash",
    timeout=30.0,
)
interpreter = LLMInterpreter(client=client, max_retries=1, max_tokens=1200)

# In your endpoint
operator_notes = ["Solar output drops to 20% from 1 PM to 3 PM."]
result = interpreter.interpret(operator_notes, scenario_id="REQ-001")

# Return as JSON
response = result.model_dump()           # dict
response_json = result.model_dump_json() # JSON string
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

- `note_index` matches the input order (0..N-1)
- `applies` is `false` only for `no_op`
- `structured_adjustment` is `null` only for `no_op`
- Errors automatically fall back to `no_op` — the endpoint never crashes

## CLI Testing

```bash
# Single note
python run_llm_test.py -n "No charging from 2 PM to 5 PM."

# Multiple notes
python run_llm_test.py -n "Solar drops to 20% from 1 PM to 3 PM." -n "Battery reserve at least 100 kWh."

# Use DeepSeek API
python run_llm_test.py -n "Do not charge from 2 PM to 5 PM." --deepseek
```

## Stress Tests

```bash
# Run all 62 tests
python run_stress_tests.py

# Run specific category (1-7)
python run_stress_tests.py --category 3

# Use DeepSeek API
python run_stress_tests.py --deepseek
```

### Categories

1. Paraphrase families (29 tests)
2. Time edge cases (6 tests)
3. Solar factor traps (8 tests)
4. Charge vs discharge ambiguity (6 tests)
5. Vague/insufficient notes (6 tests)
6. Adversarial / injection (5 tests)
7. Multi-note requests (2 tests)
