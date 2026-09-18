from dataclasses import dataclass, field

import pulp

from app.schemas import ScenarioRequest, HourPlan
from app.config import MAX_HOURS, settings
from app.constraints import Constraints
from app.baseline import build_plan_from_arrays, totals_from_plan

try:
    from scipy.optimize import linprog
except Exception:  # pragma: no cover
    linprog = None


@dataclass
class BuildResult:
    plan: list[HourPlan]
    totals: dict
    status: str = "optimal"
    used_slacks: dict = field(default_factory=dict)


@dataclass
class _Lp:
    g: list
    s: list
    c: list
    d: list
    E: list


def _make_solver():
    if settings.CBC_PATH:
        return pulp.COIN_CMD(msg=False, path=settings.CBC_PATH)
    try:
        return pulp.PULP_CBC_CMD(msg=False)
    except Exception:
        return pulp.COIN_CMD(msg=False)


def _solve_pulp(cons: Constraints, battery, use_slacks: bool) -> tuple:
    """Return (problem, variable_bundle, status_int)."""
    prob = pulp.LpProblem("gridwise", pulp.LpMinimize)
    H = MAX_HOURS
    g = [pulp.LpVariable(f"g{h}", lowBound=0) for h in range(H)]
    s = [pulp.LpVariable(f"s{h}", lowBound=0, upBound=cons.eff_solar[h]) for h in range(H)]
    c_up = [battery.max_charge_kwh_per_hour if cons.can_charge[h] else 0 for h in range(H)]
    d_up = [battery.max_discharge_kwh_per_hour if cons.can_discharge[h] else 0 for h in range(H)]
    c = [pulp.LpVariable(f"c{h}", lowBound=0, upBound=c_up[h]) for h in range(H)]
    d = [pulp.LpVariable(f"d{h}", lowBound=0, upBound=d_up[h]) for h in range(H)]
    e_lo = [0.0 if use_slacks else cons.min_energy[h] for h in range(H)]
    E = [pulp.LpVariable(f"E{h}", lowBound=e_lo[h], upBound=battery.capacity_kwh) for h in range(H)]

    sl_r = [pulp.LpVariable(f"sr{h}", lowBound=0) for h in range(H)] if use_slacks else []
    sl_g = [pulp.LpVariable(f"sg{h}", lowBound=0) for h in range(H)] if use_slacks else []
    sl_np = pulp.LpVariable("snp", lowBound=0) if use_slacks else None
    sl_nn = pulp.LpVariable("snn", lowBound=0) if use_slacks else None

    penalty = pulp.LpAffineExpression()
    if use_slacks:
        for h in range(H):
            penalty += settings.RESERVE_PENALTY * sl_r[h]
            penalty += settings.GRID_CAP_PENALTY * sl_g[h]
        penalty += settings.NEUTRALITY_PENALTY * sl_np
        penalty += settings.NEUTRALITY_PENALTY * sl_nn

    prob += pulp.lpSum(g[h] * cons.tariff[h] for h in range(H)) + penalty

    for h in range(H):
        prob += g[h] + s[h] + d[h] == cons.demand[h] + c[h]
    prob += E[0] == battery.initial_energy_kwh + c[0] - d[0]
    for h in range(1, H):
        prob += E[h] == E[h - 1] + c[h] - d[h]
    for h in range(H):
        if cons.grid_cap[h] is not None:
            if use_slacks:
                prob += g[h] <= cons.grid_cap[h] + sl_g[h]
            else:
                prob += g[h] <= cons.grid_cap[h]
    if use_slacks:
        for h in range(H):
            prob += E[h] >= cons.min_energy[h] - sl_r[h]
        prob += E[H - 1] == battery.initial_energy_kwh + sl_np - sl_nn
    else:
        prob += E[H - 1] == battery.initial_energy_kwh

    try:
        status = prob.solve(_make_solver())
    except Exception:
        return None, None, None

    return prob, _Lp(g=g, s=s, c=c, d=d, E=E), status


def _solve_pulp_peak(cons: Constraints, battery, cost_bound: float) -> tuple:
    """Phase 2: minimize peak grid draw g[h] <= P subject to cost <= cost_bound."""
    prob = pulp.LpProblem("gridwise_peak", pulp.LpMinimize)
    H = MAX_HOURS
    g = [pulp.LpVariable(f"g{h}", lowBound=0) for h in range(H)]
    s = [pulp.LpVariable(f"s{h}", lowBound=0, upBound=cons.eff_solar[h]) for h in range(H)]
    c_up = [battery.max_charge_kwh_per_hour if cons.can_charge[h] else 0 for h in range(H)]
    d_up = [battery.max_discharge_kwh_per_hour if cons.can_discharge[h] else 0 for h in range(H)]
    c = [pulp.LpVariable(f"c{h}", lowBound=0, upBound=c_up[h]) for h in range(H)]
    d = [pulp.LpVariable(f"d{h}", lowBound=0, upBound=d_up[h]) for h in range(H)]
    E = [pulp.LpVariable(f"E{h}", lowBound=cons.min_energy[h], upBound=battery.capacity_kwh) for h in range(H)]
    P = pulp.LpVariable("P", lowBound=0)

    prob += P
    prob += pulp.lpSum(g[h] * cons.tariff[h] for h in range(H)) <= cost_bound + 1e-6

    for h in range(H):
        prob += g[h] + s[h] + d[h] == cons.demand[h] + c[h]
        prob += g[h] <= P
    prob += E[0] == battery.initial_energy_kwh + c[0] - d[0]
    for h in range(1, H):
        prob += E[h] == E[h - 1] + c[h] - d[h]
    for h in range(H):
        if cons.grid_cap[h] is not None:
            prob += g[h] <= cons.grid_cap[h]
    prob += E[H - 1] == battery.initial_energy_kwh

    try:
        status = prob.solve(_make_solver())
    except Exception:
        return None, None

    if status != pulp.LpStatusOptimal:
        return None, None

    return _Lp(g=g, s=s, c=c, d=d, E=E), status


def _solve_scipy(cons: Constraints, battery, use_slacks: bool, cost_bound: float | None = None):
    """Build the same LP as dense matrices for scipy HiGHS."""
    if linprog is None:
        return None
    H = MAX_HOURS
    n = 5 * H
    if cost_bound is not None:
        idx_P = n
        n += 1

    def idx_g(h): return h * 5
    def idx_s(h): return h * 5 + 1
    def idx_c(h): return h * 5 + 2
    def idx_d(h): return h * 5 + 3
    def idx_E(h): return h * 5 + 4

    if use_slacks:
        sr_off = n
        sg_off = n + H
        snp_i = n + 2 * H
        snn_i = n + 2 * H + 1
        n += 2 * H + 2

    obj = [0.0] * n
    for h in range(H):
        obj[idx_g(h)] = cons.tariff[h]
    if cost_bound is not None:
        obj[idx_P] = 1.0
    if use_slacks:
        for h in range(H):
            obj[sr_off + h] = settings.RESERVE_PENALTY
            obj[sg_off + h] = settings.GRID_CAP_PENALTY
        obj[snp_i] = settings.NEUTRALITY_PENALTY
        obj[snn_i] = settings.NEUTRALITY_PENALTY

    lo = [0.0] * n
    hi = [None] * n
    for h in range(H):
        hi[idx_s(h)] = cons.eff_solar[h]
        hi[idx_c(h)] = battery.max_charge_kwh_per_hour if cons.can_charge[h] else 0.0
        hi[idx_d(h)] = battery.max_discharge_kwh_per_hour if cons.can_discharge[h] else 0.0
        lo[idx_E(h)] = 0.0 if use_slacks else cons.min_energy[h]
        hi[idx_E(h)] = battery.capacity_kwh
    if cost_bound is not None:
        lo[idx_P] = 0.0
        hi[idx_P] = None

    rows_eq: list[dict] = []
    rhs_eq: list[float] = []
    rows_ub: list[dict] = []
    rhs_ub: list[float] = []

    for h in range(H):
        rows_eq.append({idx_g(h): 1.0, idx_s(h): 1.0, idx_d(h): 1.0, idx_c(h): -1.0})
        rhs_eq.append(cons.demand[h])
        if cons.grid_cap[h] is not None:
            if use_slacks:
                rows_ub.append({idx_g(h): 1.0, sg_off + h: -1.0})
            else:
                rows_ub.append({idx_g(h): 1.0})
            rhs_ub.append(cons.grid_cap[h])
        if cost_bound is not None:
            rows_ub.append({idx_g(h): 1.0, idx_P: -1.0})
            rhs_ub.append(0.0)

    if cost_bound is not None:
        row_cost: dict[int, float] = {idx_g(h): cons.tariff[h] for h in range(H)}
        rows_ub.append(row_cost)
        rhs_ub.append(cost_bound + 1e-6)

    rows_eq.append({idx_E(0): 1.0, idx_c(0): -1.0, idx_d(0): 1.0})
    rhs_eq.append(battery.initial_energy_kwh)
    for h in range(1, H):
        rows_eq.append({idx_E(h): 1.0, idx_E(h - 1): -1.0, idx_c(h): -1.0, idx_d(h): 1.0})
        rhs_eq.append(0.0)

    if use_slacks:
        for h in range(H):
            rows_ub.append({idx_E(h): -1.0, sr_off + h: 1.0})
            rhs_ub.append(-cons.min_energy[h])
        rows_eq.append({idx_E(H - 1): 1.0, snp_i: -1.0, snn_i: 1.0})
        rhs_eq.append(battery.initial_energy_kwh)
    else:
        rows_eq.append({idx_E(H - 1): 1.0})
        rhs_eq.append(battery.initial_energy_kwh)

    def _mat(rows, ncols):
        if not rows:
            return None
        r_idx, c_idx, data = [], [], []
        for i, row in enumerate(rows):
            for c, v in row.items():
                r_idx.append(i)
                c_idx.append(c)
                data.append(v)
        from scipy.sparse import coo_matrix
        return coo_matrix((data, (r_idx, c_idx)), shape=(len(rows), ncols)).tocsc()

    A_eq = _mat(rows_eq, n)
    A_ub = _mat(rows_ub, n)
    b_ub = rhs_ub if A_ub is not None else None

    bounds = list(zip(lo, hi))
    res = linprog(obj, A_ub=A_ub, b_ub=b_ub, A_eq=A_eq, b_eq=rhs_eq,
                  bounds=bounds, method="highs")
    if not res.success:
        return None

    nets = [round(res.x[idx_c(h)] - res.x[idx_d(h)], 6) for h in range(H)]
    cost = sum(res.x[idx_g(h)] * cons.tariff[h] for h in range(H))
    return nets, round(cost, 6)


def _extract_nets(cons: Constraints, vars_: _Lp) -> list[float]:
    return [round(vars_.c[h].value() - vars_.d[h].value(), 6) for h in range(MAX_HOURS)]


def _absorb_drift(nets: list[float], cons: Constraints, battery, initial: float) -> list[float]:
    drift = sum(nets)
    if abs(drift) <= 1e-6:
        return nets
    H = MAX_HOURS
    e_fwd = [0.0] * H
    acc = initial
    for h in range(H):
        acc += nets[h]
        e_fwd[h] = acc

    target = -drift
    for h in range(H - 1, -1, -1):
        if not (cons.can_charge[h] and cons.can_discharge[h]):
            continue
        if target > 1e-9:
            allowance = battery.max_charge_kwh_per_hour - nets[h]
            downstream = min((battery.capacity_kwh - e_fwd[k]) for k in range(h, H))
            a = min(target, allowance, downstream)
            if a > 1e-9:
                nets[h] += a
                return nets
        elif target < -1e-9:
            a_req = -target
            allowance = battery.max_discharge_kwh_per_hour + nets[h]
            downstream = min((e_fwd[k] - cons.min_energy[k]) for k in range(h, H))
            a = min(a_req, allowance, downstream)
            if a > 1e-9:
                nets[h] -= a
                return nets
    return nets


def solve_and_build(scenario: ScenarioRequest, cons: Constraints) -> BuildResult | None:
    battery = scenario.battery
    H = MAX_HOURS

    used_slacks = {}
    nets = None
    status = "optimal"

    prob, vars_, lp_status = _solve_pulp(cons, battery, use_slacks=False)
    if lp_status in (pulp.LpStatusOptimal,):
        cost_stage1 = sum(vars_.g[h].value() * cons.tariff[h] for h in range(H))
        peak_vars, peak_status = _solve_pulp_peak(cons, battery, cost_stage1)
        if peak_status == pulp.LpStatusOptimal:
            status = "optimal"
            nets = _extract_nets(cons, peak_vars)
        else:
            nets = _extract_nets(cons, vars_)
    elif lp_status is not None:
        used_slacks["pulp-relaxed"] = True
        status = "slack"
        prob2, vars2, lp_status2 = _solve_pulp(cons, battery, use_slacks=True)
        if lp_status2 == pulp.LpStatusOptimal:
            nets = _extract_nets(cons, vars2)
            vd = prob2.variablesDict()
            for h in range(H):
                sr = vd.get(f"sr{h}")
                if sr is not None and sr.value() > 1e-9:
                    used_slacks[f"reserve_h{h}"] = sr.value()
                sg = vd.get(f"sg{h}")
                if sg is not None and sg.value() > 1e-9:
                    used_slacks[f"gridcap_h{h}"] = sg.value()
            if vd.get("snp") and vd["snp"].value() > 1e-9:
                used_slacks["neutrality"] = ("pos", vd["snp"].value())
            if vd.get("snn") and vd["snn"].value() > 1e-9:
                used_slacks["neutrality"] = ("neg", vd["snn"].value())
        else:
            nets = None

    if nets is None and linprog is not None:
        out = _solve_scipy(cons, battery, use_slacks=False)
        if out is not None:
            nets, cost_stage1 = out
            status = "scipy"
            peak_out = _solve_scipy(cons, battery, use_slacks=False, cost_bound=cost_stage1)
            if peak_out is not None:
                nets, _ = peak_out
        else:
            out = _solve_scipy(cons, battery, use_slacks=True)
            if out is not None:
                nets, _ = out
                status = "slack"
                used_slacks["scipy-slack"] = True

    if nets is None:
        return None

    nets = _absorb_drift(nets, cons, battery, battery.initial_energy_kwh)
    plan = build_plan_from_arrays(scenario, cons.demand, cons.eff_solar, nets)

    if abs(plan[-1].battery_energy_after_kwh - battery.initial_energy_kwh) >= 0.01:
        return None

    totals = totals_from_plan(plan, cons.tariff)
    return BuildResult(plan=plan, totals=totals, status=status, used_slacks=used_slacks)
