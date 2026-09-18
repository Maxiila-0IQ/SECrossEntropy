from app.schemas import DirectiveEntry

ALLOWED_TYPES = {
    "solar_reduction",
    "minimum_battery_reserve",
    "no_charge_window",
    "no_discharge_window",
    "max_grid_window",
    "no_op",
}

REQUIRED_KEYS = {
    "solar_reduction": {"hours", "factor"},
    "minimum_battery_reserve": {"hours", "minimum_energy_kwh"},
    "no_charge_window": {"hours"},
    "no_discharge_window": {"hours"},
    "max_grid_window": {"hours", "max_grid_kwh"},
    "no_op": set(),
}


def _repair_hours(hours) -> list[int] | None:
    if not isinstance(hours, list):
        return None
    cleaned = []
    for x in hours:
        if isinstance(x, bool) or not isinstance(x, int):
            continue
        if 0 <= x <= 23:
            cleaned.append(x)
    cleaned = sorted(set(cleaned))
    if not cleaned:
        return None
    return cleaned


def _validate_entry(raw: dict, note_index: int, n_notes: int, battery) -> tuple[dict | None, str | None]:
    name = "note"
    if not isinstance(raw, dict):
        return None, f"{name} {note_index}: not a dict"
    dtype = raw.get("directive_type")
    if dtype not in ALLOWED_TYPES:
        return None, f"{name} {note_index}: bad directive_type {dtype!r}"

    fixed = dict(raw)
    if dtype != "no_op":
        adj = raw.get("structured_adjustment")
        if not isinstance(adj, dict):
            return None, f"{name} {note_index}: missing structured_adjustment"
        required = REQUIRED_KEYS[dtype]
        keys = set(adj.keys())
        if keys != required:
            return None, f"{name} {note_index}: adjustment keys {sorted(keys)} != {sorted(required)}"
        hours = _repair_hours(adj.get("hours"))
        if hours is None:
            return None, f"{name} {note_index}: bad hours"
        adj = dict(adj)
        adj["hours"] = hours

        if dtype == "solar_reduction":
            f = adj.get("factor")
            if not isinstance(f, (int, float)) or isinstance(f, bool):
                return None, f"{name} {note_index}: factor must be numeric"
            if not (0.0 <= float(f) <= 1.0):
                return None, f"{name} {note_index}: factor out of range"
            adj["factor"] = float(f)
        elif dtype == "minimum_battery_reserve":
            m = adj.get("minimum_energy_kwh")
            if not isinstance(m, (int, float)) or isinstance(m, bool):
                return None, f"{name} {note_index}: reserve must be numeric"
            if not (0.0 <= float(m) <= battery.capacity_kwh):
                return None, f"{name} {note_index}: reserve out of range"
            adj["minimum_energy_kwh"] = float(m)
        elif dtype == "max_grid_window":
            gm = adj.get("max_grid_kwh")
            if not isinstance(gm, (int, float)) or isinstance(gm, bool):
                return None, f"{name} {note_index}: max_grid_kwh must be numeric"
            if float(gm) < 0.0:
                return None, f"{name} {note_index}: max_grid_kwh negative"
            adj["max_grid_kwh"] = float(gm)

        fixed["structured_adjustment"] = adj
    else:
        fixed["structured_adjustment"] = None

    expl = raw.get("explanation")
    if not isinstance(expl, str) or not expl.strip():
        fixed["explanation"] = f"Directive from operator note {note_index}."
    else:
        fixed["explanation"] = expl.strip()

    return fixed, None


def validate_entries(raw_list, n_notes: int, battery) -> tuple[list[DirectiveEntry], list[str]]:
    errors: list[str] = []
    if not isinstance(raw_list, list):
        return [DirectiveEntry(note_index=i, applies=False, directive_type="no_op",
                               structured_adjustment=None,
                               explanation=f"Interpretation failed; treated note {i} as no-op.")
                for i in range(n_notes)], ["interpretation payload not a list"]

    best_by_index: dict[int, dict] = {}
    for raw in raw_list:
        if not isinstance(raw, dict):
            errors.append("entry not a dict")
            continue
        ni = raw.get("note_index")
        if not isinstance(ni, int) or isinstance(ni, bool) or not (0 <= ni < n_notes):
            errors.append(f"bad note_index {ni!r}")
            continue
        fixed, err = _validate_entry(raw, ni, n_notes, battery)
        if fixed is not None:
            if ni not in best_by_index:
                best_by_index[ni] = fixed
        else:
            best_by_index[ni] = {
                "note_index": ni,
                "applies": False,
                "directive_type": "no_op",
                "structured_adjustment": None,
                "explanation": err,
            }
            errors.append(err if err else f"note {ni} demoted")

    entries: list[DirectiveEntry] = []
    for i in range(n_notes):
        if i not in best_by_index:
            entries.append(DirectiveEntry(note_index=i, applies=False,
                                          directive_type="no_op",
                                          structured_adjustment=None,
                                          explanation=f"No interpretation for note {i}; no-op."))
        else:
            raw = best_by_index[i]
            dtype = raw["directive_type"]
            applies = dtype != "no_op"
            entries.append(DirectiveEntry(
                note_index=i,
                applies=applies,
                directive_type=dtype,
                structured_adjustment=raw["structured_adjustment"],
                explanation=raw["explanation"],
            ))

    return entries, errors
