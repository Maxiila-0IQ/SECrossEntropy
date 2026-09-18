import re

WORD_NUMS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12,
}
WORD_FRACTIONS = {
    "half": 0.5, "one half": 0.5, "a half": 0.5,
    "one third": 1 / 3, "a third": 1 / 3, "one-third": 1 / 3,
    "one quarter": 0.25, "a quarter": 0.25, "one fourth": 0.25, "one-fourth": 0.25,
    "a fourth": 0.25, "one fifth": 0.2, "a fifth": 0.2, "one-fifth": 0.2,
    "one tenth": 0.1, "a tenth": 0.1, "one-tenth": 0.1,
}

PCT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")
KWH_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:kwh|kw h)", re.IGNORECASE)

TOKEN_RE = re.compile(
    r"(?i)(?:(?:(\d{1,2})(?::(\d{2}))?)|(noon|midnight|zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve))"
    r"\s*(a\.?\s*m\.?|p\.?\s*m\.?)?"
)

REDUCTION_KW = re.compile(
    r"reduction|drop\s+by|decrease\s+of|decreased\s+by|loss\s+of|cut\s+by|reduced\s+by",
    re.IGNORECASE,
)
REMAINING_KW = re.compile(
    r"drop\s+to|down\s+to|reduced\s+to|treated\s+as|as\s+roughly|leaves|will\s+be|"
    r"of\s+normal|remains?\s+at",
    re.IGNORECASE,
)
ZERO_SOLAR_KW = re.compile(r"offline|no\s+output|no\s+pv|out\s+of\s+service|failed|down\s+entirely", re.IGNORECASE)
WASH_KW = re.compile(r"cleaning|washing", re.IGNORECASE)

RESERVE_RE = re.compile(
    r"reserve|at\s+least|minimum|keep\s+above|not\s+fall\s+below|hold\b|fall\s+below|recharge\s+to",
    re.IGNORECASE,
)
GRID_CAP_RE = re.compile(r"grid|import|draw")
GRID_CAP_MOD_RE = re.compile(r"capped?\s*(?:at)?|limit(?:ed)?\s*(?:to|at)?|not\s+exceed|under|below|at\s+or\s+below|at\s+most|stays?\s+at|max(?:imum)?|no\s+grid|no\s+import|no\s+draw|restrict", re.IGNORECASE)
NO_GRID_RE = re.compile(r"no\s+grid|no\s+import|no\s+draw|must\s+not\s+draw", re.IGNORECASE)

SOLAR_RE = re.compile(r"solar|pv|panel|rooftop|photovoltaic", re.IGNORECASE)
NO_CHARGE_RE = re.compile(r"charger|charging|do\s+not\s+charge|cannot\s+charge|can't\s+charge|isolated|no\s+charging|\bidle\b", re.IGNORECASE)
NO_DISCHARGE_RE = re.compile(r"discharge|drawn\s+down|draw\s+down|drain|do\s+not\s+use\s+the\s+battery", re.IGNORECASE)

PAIR_CONNECTOR_RE = re.compile(r"(?i)^[\s]*(?:\b(?:to|until|through|and)\b|from|between|[>\-–])?[\s]*(?:the|at)?[\s]*(?:to\b|until\b|through\b|and\b|[>\-–]|from|between)?[\s]*$")


def _find_tokens(text: str) -> list[dict]:
    tokens = []
    for m in TOKEN_RE.finditer(text):
        hour = None
        marker = None
        is24h = False
        if m.group(1) is not None:
            hour = int(m.group(1))
            prev = text[m.start() - 1] if m.start() > 0 else ""
            nxt = text[m.end()] if m.end() < len(text) else ""
            if prev.isdigit():
                continue
            if m.group(2) is None and nxt.isdigit():
                continue
            if m.group(2) is not None:
                is24h = True
        else:
            word = (m.group(3) or "").lower()
            if word in ("noon",):
                hour = 12
            elif word in ("midnight",):
                hour = 0
            else:
                hour = WORD_NUMS[word]
        marker_raw = (m.group(4) or "").lower().replace(" ", "").replace(".", "")
        if marker_raw in ("am", "a.m"):
            marker = "am"
        elif marker_raw in ("pm", "p.m"):
            marker = "pm"
        tokens.append(
            {"hour": hour, "marker": marker, "is24h": is24h, "start": m.start(), "end": m.end()}
        )
    return tokens


def _resolve_token(tok: dict) -> int | None:
    h = tok["hour"]
    if tok["marker"] == "pm":
        return (h % 12) + 12
    if tok["marker"] == "am":
        return h % 12
    if tok["is24h"] or h > 12:
        return h
    return None


def _extract_window(text: str) -> tuple[int, int] | None:
    toks = _find_tokens(text)
    if not toks:
        return None
    if len(toks) == 1:
        base = _resolve_token(toks[0])
        if base is None:
            base = toks[0]["hour"] if toks[0]["hour"] <= 23 else 23
        return base, base + 1

    best = None
    for i in range(len(toks) - 1):
        a, b = toks[i], toks[i + 1]
        between = text[a["end"]:b["start"]]
        if PAIR_CONNECTOR_RE.match(between.strip()):
            best = (a, b)
            break
    if best is None:
        a, b = toks[0], toks[1]

    sa = _resolve_token(a)
    sb = _resolve_token(b)
    if sa is None and sb is None:
        if a["hour"] <= 11 and b["hour"] <= 11 and a["hour"] >= 1 and b["hour"] >= 1:
            sa, sb = a["hour"] + 12, b["hour"] + 12
        else:
            sa, sb = a["hour"], b["hour"]
    elif sa is None and sb is not None:
        sa = a["hour"] + 12 if a["hour"] <= 11 and sb >= 12 else a["hour"]
    elif sa is not None and sb is None:
        sb = b["hour"] + 12 if b["hour"] <= 11 and sa >= 12 else b["hour"]

    if sb <= sa:
        return sa, sb + 24
    return sa, sb


def _window_hours(sa: int, sb: int) -> list[int]:
    if sb > 24:
        hours = list(range(sa, 24)) + list(range(0, sb - 24))
    else:
        hours = list(range(sa, sb))
    return sorted(set(hours))


def _extract_percent(text: str) -> float | None:
    m = PCT_RE.search(text)
    if m:
        return float(m.group(1)) / 100.0
    lower = text.lower()
    for phrase, f in WORD_FRACTIONS.items():
        if re.search(rf"\b{re.escape(phrase)}\b", lower):
            return f
    return None


def _extract_quantity(text: str) -> float | None:
    m = KWH_RE.search(text)
    if m:
        return float(m.group(1))
    return None


def _solar_factor(text: str) -> float:
    if ZERO_SOLAR_KW.search(text):
        return 0.0
    p = _extract_percent(text)
    if p is None:
        if WASH_KW.search(text):
            return 0.5
        return 1.0
    if REMAINING_KW.search(text):
        return round(p, 6)
    if REDUCTION_KW.search(text):
        return round(1.0 - p, 6)
    return round(p, 6)


def parse_note(note: str, note_index: int, capacity_kwh: float | None = None) -> dict:
    text = note.strip()
    window = _extract_window(text)
    hours = _window_hours(*window) if window else None

    if hours and len(hours) == 24:
        hours = None

    lower = text.lower()

    if RESERVE_RE.search(text) and ("battery" in lower or "reserve" in lower or "below" in lower or "above" in lower):
        qty = _extract_quantity(text)
        if qty is None:
            p = _extract_percent(text)
            if p is not None and capacity_kwh:
                qty = p * capacity_kwh
        if qty is not None:
            return _entry(note_index, True, "minimum_battery_reserve",
                          {"hours": hours or _scan_hours(text), "minimum_energy_kwh": qty},
                          "Minimum battery reserve inferred from operator note.")
        if "reserve" in lower and hours:
            return _entry(note_index, True, "minimum_battery_reserve",
                          {"hours": hours, "minimum_energy_kwh": 0.0},
                          "Battery reserve window without explicit quantity.")

    if GRID_CAP_RE.search(text) and GRID_CAP_MOD_RE.search(text):
        h = hours or _scan_hours(text)
        if NO_GRID_RE.search(text):
            cap = 0.0
        else:
            q = _extract_quantity(text)
            cap = q if q is not None else 0.0
        return _entry(note_index, True, "max_grid_window",
                      {"hours": h, "max_grid_kwh": cap},
                      "Grid import cap inferred from operator note.")

    if SOLAR_RE.search(text):
        h = hours or _scan_hours(text)
        return _entry(note_index, True, "solar_reduction",
                      {"hours": h, "factor": _solar_factor(text)},
                      "Solar output reduction inferred from operator note.")

    if NO_CHARGE_RE.search(text):
        return _entry(note_index, True, "no_charge_window",
                      {"hours": hours or _scan_hours(text)},
                      "Battery charging prohibited in window (idle/disconnect policy).")

    if NO_DISCHARGE_RE.search(text):
        return _entry(note_index, True, "no_discharge_window",
                      {"hours": hours or _scan_hours(text)},
                      "Battery discharging prohibited (or kept idle) in window.")

    return _entry(note_index, False, "no_op", None, "No energy-schedule directive.")


def _scan_hours(text: str) -> list[int]:
    window = _extract_window(text)
    if window:
        return _window_hours(*window)
    return list(range(24))


def _entry(note_index, applies, dtype, adj, explanation) -> dict:
    return {
        "note_index": note_index,
        "applies": applies,
        "directive_type": dtype,
        "structured_adjustment": adj,
        "explanation": explanation,
    }


def parse_notes(notes: list[str], capacity_kwh: float | None = None) -> list[dict]:
    return [parse_note(n, i, capacity_kwh) for i, n in enumerate(notes)]
