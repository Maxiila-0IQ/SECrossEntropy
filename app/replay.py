import math

from app.schemas import BatteryInput, ScenarioRequest
from app.constraints import Constraints

VALID_ACTIONS = {"charge", "discharge", "idle"}
VALID_DIRECTIVE_TYPES = {
    "solar_reduction",
    "minimum_battery_reserve",
    "no_charge_window",
    "no_discharge_window",
    "max_grid_window",
    "no_op",
}

TOL = 0.01


def validate_plan(scenario: ScenarioRequest, cons: Constraints, plan, totals=None) -> list[str]:
    """Return a list of violation strings. Empty list == pass."""
    violations: list[str] = []
    battery: BatteryInput = scenario.battery
    by_hour = {h.hour: h for h in plan}

    if not plan:
        violations.append("plan is empty")
        return violations
    missing = [h for h in range(24) if h not in by_hour]
    if len(plan) != 24 or missing:
        if len(plan) != 24:
            violations.append(f"plan has {len(plan)} entries, expected 24")
        if missing:
            violations.append(f"plan missing hours {missing}")
        return violations

    demand = {h.hour: h.demand_kwh for h in scenario.hours}
    tariff = {h.hour: h.tariff_bdt_per_kwh for h in scenario.hours}

    for h in range(24):
        p = by_hour[h]
        if p.grid_kwh < -TOL or p.solar_used_kwh < -TOL or p.battery_kwh < -TOL:
            violations.append(f"hour {h}: negative quantity in plan")
        if not (math.isfinite(p.grid_kwh) and math.isfinite(p.solar_used_kwh)):
            violations.append(f"hour {h}: non-finite value")
        if p.battery_action not in VALID_ACTIONS:
            violations.append(f"hour {h}: bad battery_action {p.battery_action}")
        if p.battery_action == "idle" and abs(p.battery_kwh) > TOL:
            violations.append(f"hour {h}: idle but battery_kwh != 0")
        if not cons.can_charge[h] and p.battery_action == "charge" and p.battery_kwh > TOL:
            violations.append(f"hour {h}: charge in no_charge_window")
        if not cons.can_discharge[h] and p.battery_action == "discharge" and p.battery_kwh > TOL:
            violations.append(f"hour {h}: discharge in no_discharge_window")

        ch = p.battery_kwh if p.battery_action == "charge" else 0.0
        dh = p.battery_kwh if p.battery_action == "discharge" else 0.0

        lhs = p.grid_kwh + p.solar_used_kwh + dh
        rhs = demand[h] + ch
        if abs(lhs - rhs) > TOL:
            violations.append(
                f"hour {h}: balance violated grid+solar+discharge={lhs:.4f} != "
                f"demand+charge={rhs:.4f}"
            )

        if p.solar_used_kwh > cons.eff_solar[h] + TOL:
            violations.append(f"hour {h}: solar_used exceeds eff_solar")
        if ch > battery.max_charge_kwh_per_hour + TOL:
            violations.append(f"hour {h}: charge exceeds rate limit")
        if dh > battery.max_discharge_kwh_per_hour + TOL:
            violations.append(f"hour {h}: discharge exceeds rate limit")

        cap = cons.grid_cap[h]
        if cap is not None and p.grid_kwh > cap + TOL:
            violations.append(f"hour {h}: grid exceeds cap {cap}")

    # battery state simulation
    e = battery.initial_energy_kwh
    for h in range(24):
        p = by_hour[h]
        ch = p.battery_kwh if p.battery_action == "charge" else 0.0
        dh = p.battery_kwh if p.battery_action == "discharge" else 0.0
        e = e + ch - dh
        if abs(e - p.battery_energy_after_kwh) > TOL:
            violations.append(
                f"hour {h}: reported E_after {p.battery_energy_after_kwh} != simulated {e:.4f}"
            )
        if e < cons.min_energy[h] - TOL:
            violations.append(f"hour {h}: E below min_energy {cons.min_energy[h]}")
        if e > battery.capacity_kwh + TOL:
            violations.append(f"hour {h}: E above capacity")

    if abs(e - battery.initial_energy_kwh) > TOL:
        violations.append(f"neutrality violated: E[23]={e:.4f} != initial {battery.initial_energy_kwh}")

    # totals
    recomputed_grid = round(sum(p.grid_kwh for p in plan), 4)
    recomputed_cost = round(sum(p.grid_kwh * tariff[p.hour] for p in plan), 4)
    recomputed_peak = round(max(p.grid_kwh for p in plan), 4)
    if totals is not None:
        if abs(totals["total_grid_kwh"] - recomputed_grid) > TOL:
            violations.append("total_grid_kwh mismatch with recomputation")
        if abs(totals["total_cost_bdt"] - recomputed_cost) > TOL:
            violations.append("total_cost_bdt mismatch with recomputation")
        if abs(totals["peak_grid_kwh"] - recomputed_peak) > TOL:
            violations.append("peak_grid_kwh mismatch with recomputation")

    return violations
