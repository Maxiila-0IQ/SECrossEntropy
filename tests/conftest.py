import json
from pathlib import Path

from app.schemas import BatteryInput, HourInput, ScenarioRequest

TESTS_DIR = Path(__file__).parent


def make_scenario(
    scenario_id="t",
    demand=None,
    solar=None,
    tariff=None,
    battery=None,
    notes=None,
):
    demand = demand or [120.0] * 24
    solar = solar or [20.0] * 24
    tariff = tariff or [12.0] * 24
    notes = notes or ["No schedule change."]
    battery = battery or {
        "capacity_kwh": 200.0,
        "initial_energy_kwh": 80.0,
        "minimum_energy_kwh": 10.0,
        "max_charge_kwh_per_hour": 50.0,
        "max_discharge_kwh_per_hour": 50.0,
    }
    hours = [
        HourInput(hour=h, demand_kwh=demand[h], solar_kwh=solar[h], tariff_bdt_per_kwh=tariff[h])
        for h in range(24)
    ]
    return ScenarioRequest(
        scenario_id=scenario_id,
        operator_notes=notes,
        hours=hours,
        battery=BatteryInput(**battery),
    )


def load_cases():
    path = TESTS_DIR / "cases.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    raw = data.get("cases", [])
    cases = []
    for c in raw:
        case = dict(c)
        case["id"] = c.get("id", c.get("scenario_id",
                          c.get("input", {}).get("scenario_id", "?")))
        case["input"] = c.get("input", c)
        case["expected_output"] = c.get("expected_output", {})
        cases.append(case)
    return cases
