import pytest

from app.schemas import ScenarioRequest, DirectiveEntry, HourPlan
from app.constraints import build_constraints
from app.optimize import solve_and_build
from app.replay import validate_plan
from tests.conftest import make_scenario, load_cases


def solve_request(scenario, directives=None):
    directives = directives or []
    cons = build_constraints(scenario, directives)
    result = solve_and_build(scenario, cons)
    return cons, result


def test_analytic_arbitrage_optimal_cost():
    tariff = [5.0 if h < 8 else 15.0 for h in range(24)]
    scenario = make_scenario(scenario_id="arb",
                             demand=[100.0] * 24, solar=[0.0] * 24,
                             tariff=tariff,
                             battery=dict(capacity_kwh=200.0, initial_energy_kwh=50.0,
                                          minimum_energy_kwh=0.0,
                                          max_charge_kwh_per_hour=50.0,
                                          max_discharge_kwh_per_hour=50.0))
    cons, result = solve_request(scenario)
    assert result is not None
    violations = validate_plan(scenario, cons, result.plan, result.totals)
    assert violations == []
    assert result.totals["total_cost_bdt"] == pytest.approx(26500.0, abs=0.01)
    assert result.totals["total_grid_kwh"] == pytest.approx(2400.0, abs=0.01)
    # 150 kWh of charging must be spread across the 8 cheap hours at 18.75/h -> min peak
    assert result.totals["peak_grid_kwh"] == pytest.approx(118.75, abs=0.01)


def test_grid_cap_honored():
    scenario = make_scenario()
    d = DirectiveEntry(note_index=0, applies=True, directive_type="max_grid_window",
                       structured_adjustment={"hours": [6], "max_grid_kwh": 80.0},
                       explanation="cap")
    cons, result = solve_request(scenario, [d])
    assert result is not None
    h6 = [p for p in result.plan if p.hour == 6][0]
    assert h6.grid_kwh <= 80.0 + 0.01
    assert validate_plan(scenario, cons, result.plan, result.totals) == []


def test_solar_reduction_honored():
    scenario = make_scenario(solar=[20.0] * 24)
    d = DirectiveEntry(note_index=0, applies=True, directive_type="solar_reduction",
                       structured_adjustment={"hours": [13, 14], "factor": 0.2},
                       explanation="reduce")
    cons, result = solve_request(scenario, [d])
    assert result is not None
    for p in result.plan:
        if p.hour in (13, 14):
            assert p.solar_used_kwh <= 4.0 + 0.01
    assert validate_plan(scenario, cons, result.plan, result.totals) == []


def test_multiple_solar_reductions_stack():
    scenario = make_scenario(solar=[20.0] * 24)
    d1 = DirectiveEntry(note_index=0, applies=True, directive_type="solar_reduction",
                        structured_adjustment={"hours": [13], "factor": 0.5}, explanation="a")
    d2 = DirectiveEntry(note_index=1, applies=True, directive_type="solar_reduction",
                        structured_adjustment={"hours": [13], "factor": 0.5}, explanation="b")
    cons, result = solve_request(scenario, [d1, d2])
    assert result is not None
    h13 = [p for p in result.plan if p.hour == 13][0]
    assert h13.solar_used_kwh <= 5.0 + 0.01  # 20 * 0.5 * 0.5


def test_reserve_honored():
    scenario = make_scenario()
    d = DirectiveEntry(note_index=0, applies=True, directive_type="minimum_battery_reserve",
                       structured_adjustment={"hours": [19, 20, 21], "minimum_energy_kwh": 130.0},
                       explanation="reserve")
    cons, result = solve_request(scenario, [d])
    assert result is not None
    for h in (19, 20, 21):
        p = [p for p in result.plan if p.hour == h][0]
        assert p.battery_energy_after_kwh >= 130.0 - 0.01
    assert validate_plan(scenario, cons, result.plan, result.totals) == []


def test_no_discharge_window_honored():
    scenario = make_scenario()
    # expensive late hours to entice discharge
    tariff = [12.0 if h < 20 else 40.0 for h in range(24)]
    scenario = make_scenario(scenario_id="ndw", tariff=tariff)
    d = DirectiveEntry(note_index=0, applies=True, directive_type="no_discharge_window",
                       structured_adjustment={"hours": [21, 22]}, explanation="no disp")
    cons, result = solve_request(scenario, [d])
    assert result is not None
    for h in (21, 22):
        p = [p for p in result.plan if p.hour == h][0]
        assert p.battery_action != "discharge"
    assert validate_plan(scenario, cons, result.plan, result.totals) == []


def test_no_simultaneous_charge_discharge_and_neutrality():
    scenario = make_scenario()
    _, result = solve_request(scenario)
    assert result is not None
    for p in result.plan:
        if p.battery_action == "idle":
            assert p.battery_kwh == 0.0
    assert abs(result.plan[23].battery_energy_after_kwh - 80.0) < 0.01


def test_deterministic_repeatable():
    scenario = make_scenario()
    _, r1 = solve_request(scenario)
    _, r2 = solve_request(scenario)
    assert [(p.hour, p.grid_kwh, p.battery_kwh) for p in r1.plan] == \
           [(p.hour, p.grid_kwh, p.battery_kwh) for p in r2.plan]


def test_baseline_is_valid_when_solver_cannot():
    scenario = make_scenario()
    # force an impossible grid cap on every hour: cap 0 but demand 120, solar 20
    dummy_hours = list(range(24))
    d = DirectiveEntry(note_index=0, applies=True, directive_type="max_grid_window",
                       structured_adjustment={"hours": dummy_hours, "max_grid_kwh": 0.0},
                       explanation="impossible cap")
    cons = build_constraints(scenario, [d])
    result = solve_and_build(scenario, cons)
    if result is None:
        from app.baseline import build_baseline
        base = build_baseline(scenario, cons.eff_solar)
        violations = validate_plan(scenario, cons, base.hourly_plan,
                                   {"total_grid_kwh": base.total_grid_kwh,
                                    "total_cost_bdt": base.total_cost_bdt,
                                    "peak_grid_kwh": base.peak_grid_kwh})
        # the reserve-less baseline violates the cap but stays structurally valid
        assert all(not v.startswith("neg") and "missing" not in v for v in violations)


PUBLIC_CASES = load_cases()


def _to_request(case):
    return ScenarioRequest(**case["input"])


@pytest.mark.skipif(not PUBLIC_CASES, reason="tests/cases.json not present")
@pytest.mark.parametrize("case", PUBLIC_CASES, ids=lambda c: c["id"])
def test_public_cases_reproduce_reference_cost(case):
    scenario = _to_request(case)
    ref_directives = [DirectiveEntry(**e) for e in case["expected_output"]["directive_interpretation"]]
    cons = build_constraints(scenario, ref_directives)
    result = solve_and_build(scenario, cons)
    assert result is not None, "solver must succeed on public case"
    violations = validate_plan(scenario, cons, result.plan, result.totals)
    assert violations == []
    exp = case["expected_output"]
    ref_cost = exp.get("total_cost_bdt")
    if ref_cost is not None:
        assert result.totals["total_cost_bdt"] <= float(ref_cost) + 0.01, \
            f"cost {result.totals['total_cost_bdt']} exceeds reference {ref_cost}"
        assert result.totals["total_cost_bdt"] >= float(ref_cost) - 0.01, \
            f"cost {result.totals['total_cost_bdt']} below reference {ref_cost}"
    ref_grid = exp.get("total_grid_kwh")
    if ref_grid is not None:
        assert result.totals["total_grid_kwh"] == pytest.approx(float(ref_grid), abs=0.01)
    ref_peak = exp.get("peak_grid_kwh")
    if ref_peak is not None:
        assert result.totals["peak_grid_kwh"] == pytest.approx(float(ref_peak), abs=0.01)