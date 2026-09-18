from dataclasses import dataclass

from app.schemas import DirectiveEntry, ScenarioRequest
from app.config import MAX_HOURS


@dataclass
class Constraints:
    demand: list[float]
    tariff: list[float]
    raw_solar: list[float]
    eff_solar: list[float]
    min_energy: list[float]
    can_charge: list[bool]
    can_discharge: list[bool]
    grid_cap: list[float | None]

    def clean(self) -> bool:
        try:
            for h in range(MAX_HOURS):
                assert self.eff_solar[h] >= 0.0
                assert self.min_energy[h] >= 0.0
                cap = self.grid_cap[h]
                assert cap is None or cap >= 0.0
            return True
        except AssertionError:
            return False


def build_constraints(scenario: ScenarioRequest, entries: list[DirectiveEntry]) -> Constraints:
    hours_sorted = sorted(scenario.hours, key=lambda x: x.hour)
    demand = [h.demand_kwh for h in hours_sorted]
    tariff = [h.tariff_bdt_per_kwh for h in hours_sorted]
    raw_solar = [h.solar_kwh for h in hours_sorted]

    eff_solar = list(raw_solar)
    min_energy = [scenario.battery.minimum_energy_kwh] * MAX_HOURS
    can_charge = [True] * MAX_HOURS
    can_discharge = [True] * MAX_HOURS
    grid_cap: list[float | None] = [None] * MAX_HOURS

    for e in entries:
        if e.directive_type == "no_op" or e.structured_adjustment is None:
            continue
        adj = e.structured_adjustment
        hours = adj.get("hours", [])
        if e.directive_type == "solar_reduction":
            factor = float(adj.get("factor", 1.0))
            for h in hours:
                eff_solar[h] *= factor
        elif e.directive_type == "minimum_battery_reserve":
            m = float(adj.get("minimum_energy_kwh", 0.0))
            for h in hours:
                min_energy[h] = max(min_energy[h], m)
        elif e.directive_type == "no_charge_window":
            for h in hours:
                can_charge[h] = False
        elif e.directive_type == "no_discharge_window":
            for h in hours:
                can_discharge[h] = False
        elif e.directive_type == "max_grid_window":
            g = float(adj.get("max_grid_kwh", 0.0))
            for h in hours:
                if grid_cap[h] is None or g < grid_cap[h]:
                    grid_cap[h] = g

    return Constraints(
        demand=demand,
        tariff=tariff,
        raw_solar=raw_solar,
        eff_solar=eff_solar,
        min_energy=min_energy,
        can_charge=can_charge,
        can_discharge=can_discharge,
        grid_cap=grid_cap,
    )