from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from llm.models import DirectiveType

BatteryAction = Literal["charge", "discharge", "idle"]


class HourInput(BaseModel):
    hour: int = Field(ge=0, le=23)
    demand_kwh: float = Field(ge=0)
    solar_kwh: float = Field(ge=0)
    tariff_bdt_per_kwh: float


class BatteryInput(BaseModel):
    capacity_kwh: float = Field(gt=0)
    initial_energy_kwh: float = Field(ge=0)
    minimum_energy_kwh: float = Field(ge=0)
    max_charge_kwh_per_hour: float = Field(ge=0)
    max_discharge_kwh_per_hour: float = Field(ge=0)


class ScenarioRequest(BaseModel):
    scenario_id: str
    operator_notes: list[str] = Field(min_length=1, max_length=3)
    hours: list[HourInput] = Field(min_length=24, max_length=24)
    battery: BatteryInput

    @field_validator("operator_notes")
    @classmethod
    def notes_non_empty(cls, v):
        cleaned = []
        for note in v:
            if note is None or note.strip() == "":
                raise ValueError("operator_notes entries must be non-empty")
            cleaned.append(note.strip())
        return cleaned

    @model_validator(mode="after")
    def normalize_hours_and_bounds(self):
        seen = set()
        for h in self.hours:
            if h.hour in seen:
                raise ValueError("hours must contain each hour 0..23 exactly once")
            seen.add(h.hour)
        if len(seen) != 24:
            raise ValueError("hours must contain each hour 0..23 exactly once")
        self.hours = sorted(self.hours, key=lambda h: h.hour)
        b = self.battery
        if not (b.minimum_energy_kwh <= b.initial_energy_kwh <= b.capacity_kwh):
            raise ValueError(
                "battery minimum_energy_kwh <= initial_energy_kwh <= capacity_kwh "
                "must hold"
            )
        return self


class DirectiveEntry(BaseModel):
    note_index: int
    applies: bool
    directive_type: DirectiveType
    structured_adjustment: dict | None
    explanation: str


class HourPlan(BaseModel):
    hour: int
    grid_kwh: float
    solar_used_kwh: float
    battery_action: BatteryAction
    battery_kwh: float
    battery_energy_after_kwh: float


class OptimizeResponse(BaseModel):
    scenario_id: str
    directive_interpretation: list[DirectiveEntry]
    hourly_plan: list[HourPlan]
    total_grid_kwh: float
    total_cost_bdt: float
    peak_grid_kwh: float
    plan_summary: str
