import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import make_scenario, load_cases

RESPONSE_ORDER = [
    "scenario_id",
    "directive_interpretation",
    "hourly_plan",
    "total_grid_kwh",
    "total_cost_bdt",
    "peak_grid_kwh",
    "plan_summary",
]


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


def _payload(scenario):
    return scenario.model_dump()


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_valid_request_returns_200_and_structure(client):
    demand = [50.0 if h in (0, 1, 22, 23) else 120.0 for h in range(24)]
    scenario = make_scenario(scenario_id="api-01", demand=demand,
                             notes=["No grid import from 10 PM until 2 AM."])
    r = client.post("/optimize-energy", json=_payload(scenario))
    assert r.status_code == 200, r.text
    body = r.json()
    assert list(body.keys()) == RESPONSE_ORDER
    assert body["scenario_id"] == "api-01"
    assert len(body["hourly_plan"]) == 24
    assert [p["hour"] for p in body["hourly_plan"]] == list(range(24))
    assert len(body["directive_interpretation"]) == 1
    assert body["directive_interpretation"][0]["directive_type"] == "max_grid_window"
    assert body["directive_interpretation"][0]["structured_adjustment"]["hours"] == [0, 1, 22, 23]
    for h in (0, 1, 22, 23):
        assert [p for p in body["hourly_plan"] if p["hour"] == h][0]["grid_kwh"] <= 0.01
    assert body["total_grid_kwh"] == round(sum(p["grid_kwh"] for p in body["hourly_plan"]), 4)
    t = [h.tariff_bdt_per_kwh for h in sorted(scenario.hours, key=lambda x: x.hour)]
    assert body["total_cost_bdt"] == round(sum(p["grid_kwh"] * t[p["hour"]] for p in body["hourly_plan"]), 4)
    assert abs(body["hourly_plan"][23]["battery_energy_after_kwh"]
               - scenario.battery.initial_energy_kwh) < 0.01


def test_unsorted_hours_handled(client):
    scenario = make_scenario(scenario_id="api-unsorted")
    payload = scenario.model_dump()
    payload["hours"] = [payload["hours"][i] for i in [23, 0, 5, 1, 2, 3, 4, 6, 7, 8, 9, 10,
                                                       11, 12, 13, 14, 15, 16, 17, 18, 19,
                                                       20, 21, 22]]
    r = client.post("/optimize-energy", json=payload)
    assert r.status_code == 200
    assert [p["hour"] for p in r.json()["hourly_plan"]] == list(range(24))


def test_malformed_body_400_not_422(client):
    bad = {
        "scenario_id": "bad",
        "operator_notes": [""],
        "hours": [{"hour": 0, "demand_kwh": 10, "solar_kwh": 5, "tariff_bdt_per_kwh": 12}],
        "battery": {"capacity_kwh": 100},
    }
    r = client.post("/optimize-energy", json=bad)
    assert r.status_code == 400
    assert "error" in r.json()


def test_duplicate_hours_400(client):
    scenario = make_scenario()
    payload = _payload(scenario)
    payload["hours"].append(dict(payload["hours"][0]))
    r = client.post("/optimize-energy", json=payload)
    assert r.status_code == 400


def test_battery_bounds_400(client):
    scenario = make_scenario()
    payload = _payload(scenario)
    payload["battery"]["minimum_energy_kwh"] = 500.0
    payload["battery"]["initial_energy_kwh"] = 100.0
    r = client.post("/optimize-energy", json=payload)
    assert r.status_code == 400


def test_idle_action_zero_kwh(client):
    scenario = make_scenario(scenario_id="idle")
    r = client.post("/optimize-energy", json=_payload(scenario))
    body = r.json()
    for p in body["hourly_plan"]:
        if p["battery_action"] == "idle":
            assert p["battery_kwh"] == 0.0
        assert p["battery_kwh"] >= 0.0


def test_full_paraphrase_roundtrip(client):
    demand = [50.0 if h in (0, 1, 22, 23) else 120.0 for h in range(24)]
    solar = [20.0] * 24
    scenario = make_scenario(
        scenario_id="par", demand=demand, solar=solar,
        notes=[
            "Expect an 80% reduction in rooftop solar during the 1-3 PM maintenance window.",
            "The battery must not be drawn down between 6 and 9 PM.",
            "No grid import from 10 PM until 2 AM.",
        ],
    )
    r = client.post("/optimize-energy", json=_payload(scenario))
    assert r.status_code == 200
    body = r.json()
    types = [e["directive_type"] for e in body["directive_interpretation"]]
    assert types == ["solar_reduction", "no_discharge_window", "max_grid_window"]
    assert all(p["battery_action"] != "discharge" for p in body["hourly_plan"] if p["hour"] in (18, 19, 20))


PUBLIC_CASES = load_cases()


@pytest.mark.skipif(not PUBLIC_CASES, reason="tests/cases.json not present")
@pytest.mark.parametrize("case", PUBLIC_CASES, ids=lambda c: c["id"])
def test_public_cases_end_to_end(client, case):
    payload = dict(case["input"])
    r = client.post("/optimize-energy", json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scenario_id"] == payload["scenario_id"]
    assert len(body["hourly_plan"]) == 24
    assert len(body["directive_interpretation"]) == len(payload["operator_notes"])
    exp = case["expected_output"]
    ref_cost = exp.get("total_cost_bdt")
    if ref_cost is not None:
        assert body["total_cost_bdt"] <= float(ref_cost) + 0.01
        assert body["total_cost_bdt"] >= float(ref_cost) - 0.01
