from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

DirectiveType = Literal[
    "solar_reduction",
    "minimum_battery_reserve",
    "no_charge_window",
    "no_discharge_window",
    "max_grid_window",
    "no_op",
]


class DirectiveInterpretationEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note_index: int = Field(ge=0)
    applies: bool
    directive_type: DirectiveType
    structured_adjustment: dict[str, Any] | None
    explanation: str


class DirectiveInterpretationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    directive_interpretation: list[DirectiveInterpretationEntry]
