from copy import deepcopy

from app.schemas import DirectiveEntry, HourPlan
from app.constraints import build_constraints
from app.optimize import solve_and_build
from app.replay import validate_plan
from tests.conftest import make_scenario


def test_valid_plan_replays_clean():
    scenario = make_scenario()
    cons = build_constraints(scenario, [])
    result = solve_and_build(scenario, cons)
    violations = validate_plan(scenario, cons, result.plan, result.totals)
    assert violations == []
    assert abs(result.plan[23].battery_energy_after_kwh - 80.0) < 0.01


def test_corrupt_final_energy_caught():
    scenario = make_scenario()
    cons = build_constraints(scenario, [])
    result = solve_and_build(scenario, cons)
    plan = deepcopy(result.plan)
    plan[23] = HourPlan(hour=23, grid_kwh=plan[23].grid_kwh,
                        solar_used_kwh=plan[23].solar_used_kwh,
                        battery_action=plan[23].battery_action,
                        battery_kwh=plan[23].battery_kwh,
                        battery_energy_after_kwh=plan[23].battery_energy_after_kwh + 5.0)
    violations = validate_plan(scenario, cons, plan, result.totals)
    assert any("reported" in v or "neutrality" in v for v in violations)


def test_corrupt_grid_kwh_caught():
    scenario = make_scenario()
    cons = build_constraints(scenario, [])
    result = solve_and_build(scenario, cons)
    plan = deepcopy(result.plan)
    plan[5] = HourPlan(hour=5, grid_kwh=plan[5].grid_kwh + 1.0,
                       solar_used_kwh=plan[5].solar_used_kwh,
                       battery_action=plan[5].battery_action,
                       battery_kwh=plan[5].battery_kwh,
                       battery_energy_after_kwh=plan[5].battery_energy_after_kwh)
    violations = validate_plan(scenario, cons, plan, result.totals)
    assert any("balance" in v for v in violations)


def test_insert_charge_in_no_charge_window_caught():
    scenario = make_scenario()
    d = DirectiveEntry(note_index=0, applies=True, directive_type="no_charge_window",
                       structured_adjustment={"hours": [2, 3, 4]}, explanation="t")
    cons = build_constraints(scenario, [d])
    result = solve_and_build(scenario, cons)
    assert all(p.battery_action != "charge" for p in result.plan if p.hour in (2, 3, 4))
    plan = deepcopy(result.plan)
    plan[3] = HourPlan(hour=3, grid_kwh=plan[3].grid_kwh + 10.0,
                       solar_used_kwh=plan[3].solar_used_kwh,
                       battery_action="charge", battery_kwh=10.0,
                       battery_energy_after_kwh=plan[3].battery_energy_after_kwh + 10.0)
    violations = validate_plan(scenario, cons, plan, result.totals)
    assert any("no_charge_window" in v for v in violations)


def test_negative_grid_caught():
    scenario = make_scenario()
    cons = build_constraints(scenario, [])
    result = solve_and_build(scenario, cons)
    plan = deepcopy(result.plan)
    plan[2] = HourPlan(hour=2, grid_kwh=-1.0, solar_used_kwh=plan[2].solar_used_kwh,
                       battery_action=plan[2].battery_action,
                       battery_kwh=plan[2].battery_kwh,
                       battery_energy_after_kwh=plan[2].battery_energy_after_kwh)
    violations = validate_plan(scenario, cons, plan, result.totals)
    assert any("negative" in v for v in violations)


def test_missing_hour_caught():
    scenario = make_scenario()
    cons = build_constraints(scenario, [])
    result = solve_and_build(scenario, cons)
    plan = [p for p in result.plan if p.hour != 7]
    violations = validate_plan(scenario, cons, plan, result.totals)
    assert any("missing" in v for v in violations)


def test_idle_with_movement_caught():
    scenario = make_scenario()
    cons = build_constraints(scenario, [])
    result = solve_and_build(scenario, cons)
    plan = deepcopy(result.plan)
    plan[5] = HourPlan(hour=5, grid_kwh=plan[5].grid_kwh,
                       solar_used_kwh=plan[5].solar_used_kwh,
                       battery_action="idle", battery_kwh=2.0,
                       battery_energy_after_kwh=plan[5].battery_energy_after_kwh)
    violations = validate_plan(scenario, cons, plan, result.totals)
    assert any("idle" in v for v in violations)


def test_corrupt_totals_caught():
    scenario = make_scenario()
    cons = build_constraints(scenario, [])
    result = solve_and_build(scenario, cons)
    bogus_totals = dict(result.totals)
    bogus_totals["total_grid_kwh"] = result.totals["total_grid_kwh"] + 100.0
    violations = validate_plan(scenario, cons, result.plan, bogus_totals)
    assert any("total_grid_kwh" in v for v in violations)
