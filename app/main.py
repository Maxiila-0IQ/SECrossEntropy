import asyncio
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.config import settings
from app.schemas import ScenarioRequest, OptimizeResponse, DirectiveEntry
from app.constraints import build_constraints
from app.optimize import solve_and_build
from app.baseline import build_baseline
from app.replay import validate_plan
from app.interpret import interpret_notes

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("gridwise.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("GridWise starting up — solver backend: %s, LLM model: %s",
                settings.SOLVER_BACKEND, settings.LLM_MODEL)
    yield
    logger.info("GridWise shutting down")


app = FastAPI(title="GridWise Energy Optimizer", lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):
    return JSONResponse(status_code=400, content={"error": "invalid request: " + "; ".join(
        str(e["loc"][-1]) + ": " + str(e["msg"]) for e in exc.errors()
    )[:500]})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request, exc):
    logger.exception("unhandled exception")
    return JSONResponse(status_code=500, content={"error": "internal server error"})


@app.get("/health")
async def health():
    return {"status": "ok"}


def _baseline_response(req: ScenarioRequest, entries, eff_solar) -> OptimizeResponse:
    base = build_baseline(req, eff_solar)
    return OptimizeResponse(
        scenario_id=req.scenario_id,
        directive_interpretation=entries,
        hourly_plan=base.hourly_plan,
        total_grid_kwh=base.total_grid_kwh,
        total_cost_bdt=base.total_cost_bdt,
        peak_grid_kwh=base.peak_grid_kwh,
        plan_summary=base.plan_summary,
    )


@app.post("/optimize-energy", response_model=OptimizeResponse)
async def optimize_energy(req: ScenarioRequest):
    t0 = time.perf_counter()
    try:
        entries, interp_path = await interpret_notes(req.operator_notes, req.battery)
    except Exception as exc:
        logger.exception("interpret_notes failed")
        entries = [
            DirectiveEntry(note_index=i, applies=False, directive_type="no_op",
                           structured_adjustment=None, explanation="interpretation failed")
            for i in range(len(req.operator_notes))
        ]
        interp_path = "no_op"

    cons = build_constraints(req, entries)
    result = await asyncio.to_thread(solve_and_build, req, cons)

    violations = None
    if result is not None:
        violations = validate_plan(req, cons, result.plan, result.totals)
        replays_clean = not violations and abs(
            result.plan[-1].battery_energy_after_kwh - req.battery.initial_energy_kwh
        ) < 0.01
    else:
        replays_clean = False

    if result is not None and replays_clean:
        plan = result.plan
        t = result.totals
        n_charged = sum(1 for p in plan if p.battery_action == "charge")
        n_discharged = sum(1 for p in plan if p.battery_action == "discharge")
        summary = (
            f"Optimized 24-hour energy schedule for scenario {req.scenario_id}: "
            f"total grid {t['total_grid_kwh']} kWh, total cost {t['total_cost_bdt']} BDT, "
            f"peak draw {t['peak_grid_kwh']} kWh. "
            f"Battery charged in {n_charged} hours, discharged in {n_discharged} hours."
        )
        resp = OptimizeResponse(
            scenario_id=req.scenario_id,
            directive_interpretation=entries,
            hourly_plan=plan,
            total_grid_kwh=t["total_grid_kwh"],
            total_cost_bdt=t["total_cost_bdt"],
            peak_grid_kwh=t["peak_grid_kwh"],
            plan_summary=summary,
        )
    else:
        logger.info("solver/replay failed; falling back to baseline (violations=%s)", violations)
        resp = _baseline_response(req, entries, cons.eff_solar)

    elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
    logger.info(
        "scenario=%s interp_path=%s solve_status=%s replay=%s latency_ms=%s slacks=%s",
        req.scenario_id, interp_path,
        "baseline" if not replays_clean else (result.status if result else "error"),
        "pass" if replays_clean else "fail",
        elapsed_ms, (result.used_slacks if result else {}),
    )
    return resp