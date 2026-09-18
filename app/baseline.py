from app.schemas import HourPlan, OptimizeResponse, ScenarioRequest

R = lambda x: round(x, 4)


def build_plan_from_arrays(
    scenario: ScenarioRequest,
    demand: list[float],
    eff_solar: list[float],
    nets: list[float],
) -> list[HourPlan]:
    plan: list[HourPlan] = []
    initial = scenario.battery.initial_energy_kwh
    e_prev = initial
    for h in range(24):
        net = nets[h]
        e = round(e_prev + net, 6)
        need = demand[h] + net
        solar_used = min(eff_solar[h], max(need, 0.0))
        grid = max(need - solar_used, 0.0)
        if net > 1e-9:
            action, mag = "charge", net
        elif net < -1e-9:
            action, mag = "discharge", -net
        else:
            action, mag = "idle", 0.0
        plan.append(
            HourPlan(
                hour=h,
                grid_kwh=R(grid),
                solar_used_kwh=R(solar_used),
                battery_action=action,
                battery_kwh=R(mag),
                battery_energy_after_kwh=R(e),
            )
        )
        e_prev = e
    return plan


def totals_from_plan(plan: list[HourPlan], tariff: list[float]) -> dict:
    total_grid = round(sum(p.grid_kwh for p in plan), 4)
    total_cost = round(sum(p.grid_kwh * tariff[p.hour] for p in plan), 4)
    peak = round(max(p.grid_kwh for p in plan), 4)
    return {
        "total_grid_kwh": total_grid,
        "total_cost_bdt": total_cost,
        "peak_grid_kwh": peak,
    }


def build_baseline(scenario: ScenarioRequest, eff_solar: list[float]) -> OptimizeResponse:
    demand = [h.demand_kwh for h in sorted(scenario.hours, key=lambda x: x.hour)]
    tariff = [h.tariff_bdt_per_kwh for h in sorted(scenario.hours, key=lambda x: x.hour)]
    nets = [0.0] * 24
    plan = build_plan_from_arrays(scenario, demand, eff_solar, nets)
    totals = totals_from_plan(plan, tariff)
    summary = (
        f"Safe baseline: battery idle all 24h; "
        f"grid {totals['total_grid_kwh']} kWh, cost {totals['total_cost_bdt']} BDT, "
        f"peak {totals['peak_grid_kwh']} kWh."
    )
    return OptimizeResponse(
        scenario_id=scenario.scenario_id,
        directive_interpretation=[],
        hourly_plan=plan,
        plan_summary=summary,
        **totals,
    )
