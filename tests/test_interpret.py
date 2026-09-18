import pytest

from app.fallback import parse_note
from app.guardrails import validate_entries
from app.schemas import BatteryInput
from app.interpret import interpret_notes
from app.constraints import build_constraints


BATTERY = BatteryInput(
    capacity_kwh=500.0,
    initial_energy_kwh=100.0,
    minimum_energy_kwh=10.0,
    max_charge_kwh_per_hour=50.0,
    max_discharge_kwh_per_hour=50.0,
)

TABLE = [
    ("Facilities will wash the rooftop solar panels from noon until 2 PM. Treat usable solar as roughly 25% of the forecast.",
     "solar_reduction", [12, 13], 0.25),
    ("The battery charger will be isolated from 2 AM until 5 AM for electrical maintenance.",
     "no_charge_window", [2, 3, 4], None),
    ("PV production will drop to about 20% between 13:00 and 15:00.",
     "solar_reduction", [13, 14], 0.2),
    ("Expect an 80% reduction in rooftop solar during the 1-3 PM maintenance window.",
     "solar_reduction", [13, 14], 0.2),
    ("Panel washing from one until three will leave roughly one-fifth of normal solar output.",
     "solar_reduction", [13, 14], 0.2),
    ("Solar will be offline entirely from 11 AM to 1 PM.",
     "solar_reduction", [11, 12], 0.0),
    ("Do not charge the battery between 2 PM and 4 PM.",
     "no_charge_window", [14, 15], None),
    ("The battery must not be drawn down between 6 and 9 PM.",
     "no_discharge_window", [18, 19, 20], None),
    ("Keep at least 120 kWh in reserve from 6 PM until 9 PM.",
     "minimum_battery_reserve", [18, 19, 20], 120),
    ("Don't let the battery fall below 120 kWh from 18:00 to 21:00.",
     "minimum_battery_reserve", [18, 19, 20], 120),
    ("Grid draw must stay under 150 kWh from 6 to 9 PM.",
     "max_grid_window", [18, 19, 20], 150),
    ("No grid import from 10 PM until 2 AM.",
     "max_grid_window", [0, 1, 22, 23], 0),
    ("Keep the battery idle from 2 to 4 PM.",
     "no_charge_window", [14, 15], None),
    ("The cafeteria menu changes tomorrow.", "no_op", None, None),
    ("The sports office moved next month's registration deadline.", "no_op", None, None),
    ("WiFi maintenance in Building 3 overnight.", "no_op", None, None),
]


def _numeric_value(adj):
    if not adj:
        return None
    return adj.get("factor", adj.get("minimum_energy_kwh", adj.get("max_grid_kwh")))


@pytest.mark.parametrize("note,expected_type,expected_hours,expected_num", TABLE)
def test_fallback_paraphrase(note, expected_type, expected_hours, expected_num):
    raw = parse_note(note, 0)
    entries, errors = validate_entries([raw], 1, BATTERY)
    assert not errors
    e = entries[0]
    adj = e.structured_adjustment
    assert e.directive_type == expected_type
    if expected_hours is None:
        assert e.applies is False
        assert adj is None
    else:
        assert e.applies is True
        assert adj["hours"] == expected_hours
        if expected_num is not None:
            assert abs(_numeric_value(adj) - expected_num) < 1e-6


@pytest.mark.parametrize("note,expected_type,expected_hours,expected_num", TABLE)
def test_interpret_paraphrase(note, expected_type, expected_hours, expected_num):
    import asyncio
    entries, _ = asyncio.run(interpret_notes([note], BATTERY))
    e = entries[0]
    adj = e.structured_adjustment
    assert e.directive_type == expected_type
    if expected_hours is None:
        assert e.applies is False
        assert adj is None
    else:
        assert e.applies is True
        assert adj["hours"] == expected_hours
        if expected_num is not None:
            assert abs(_numeric_value(adj) - expected_num) < 1e-6


def test_no_gaps_or_duplicates_note_indices():
    raw = [{"note_index": 1, "applies": True, "directive_type": "no_charge_window",
            "structured_adjustment": {"hours": [1, 2]},
            "explanation": "x"}]
    entries, errors = validate_entries(raw, 3, BATTERY)
    assert [e.note_index for e in entries] == [0, 1, 2]
    assert entries[0].directive_type == "no_op"
    assert entries[2].directive_type == "no_op"


def test_duplicate_note_index_keeps_first_valid():
    raw = [
        {"note_index": 0, "applies": True, "directive_type": "no_charge_window",
         "structured_adjustment": {"hours": [1, 2]}, "explanation": "a"},
        {"note_index": 0, "applies": True, "directive_type": "no_discharge_window",
         "structured_adjustment": {"hours": [3, 4]}, "explanation": "b"},
    ]
    entries, _ = validate_entries(raw, 1, BATTERY)
    assert len(entries) == 1
    assert entries[0].directive_type == "no_charge_window"


def test_demote_never_drop():
    raw = [
        {"note_index": 0, "applies": True, "directive_type": "not_a_type",
         "structured_adjustment": {"hours": [1]}, "explanation": "bad"},
        {"note_index": 1, "applies": True, "directive_type": "solar_reduction",
         "structured_adjustment": {"hours": [2], "factor": 3.0}, "explanation": "bad factor"},
    ]
    entries, errors = validate_entries(raw, 2, BATTERY)
    assert len(entries) == 2
    assert entries[0].directive_type == "no_op"
    assert entries[1].directive_type == "no_op"
    assert len(errors) >= 2


def test_hours_repaired_sorted_dedup():
    raw = [{"note_index": 0, "applies": True, "directive_type": "no_charge_window",
            "structured_adjustment": {"hours": [5, 2, 2, 9]}, "explanation": "x"}]
    entries, _ = validate_entries(raw, 1, BATTERY)
    assert entries[0].structured_adjustment["hours"] == [2, 5, 9]


import asyncio
from unittest.mock import patch

import app.interpret as interpret_mod


def _run(notes, battery=BATTERY):
    return asyncio.run(interpret_mod.interpret_notes(notes, battery))


def test_malformed_llm_output_degrades_to_fallback():
    bad = '{"interpretations": [{"note_index": 0, "applies": true, "directive_type": "bogus", "structured_adjustment": null, "explanation": "x"}]}'

    async def _bad_first(notes, timeout):
        return bad

    async def _bad_retry(messages, timeout):
        return bad

    with patch.object(interpret_mod.settings, "GROQ_API_KEY", "test-key"), \
         patch.object(interpret_mod, "_call_llm", side_effect=_bad_first), \
         patch.object(interpret_mod, "_call_llm_with_messages", side_effect=_bad_retry):
        entries, path = _run(["No grid import from 10 PM until 2 AM."])
        assert path == "fallback"
        assert entries[0].directive_type == "max_grid_window"


def test_invalid_json_degrades_to_fallback():
    async def _bad_call(notes, timeout):
        return "this is not json"
    with patch.object(interpret_mod.settings, "GROQ_API_KEY", "test-key"), \
         patch.object(interpret_mod, "_call_llm", side_effect=_bad_call):
        entries, path = _run(["Keep at least 120 kWh in reserve from 6 PM until 9 PM."])
        assert path == "fallback"
        assert entries[0].directive_type == "minimum_battery_reserve"


def test_llm_timeout_degrades_to_fallback():
    async def _slow_call(notes, timeout):
        await asyncio.sleep(30)
        return None
    with patch.object(interpret_mod.settings, "GROQ_API_KEY", "test-key"), \
         patch.object(interpret_mod.settings, "LLM_TIMEOUT", 0.05), \
         patch.object(interpret_mod, "_call_llm", side_effect=_slow_call):
        entries, path = _run(["PV production will drop to about 20% between 13:00 and 15:00."])
        assert path == "fallback"
        assert entries[0].directive_type == "solar_reduction"


def test_resolve_percentage_of_capacity_in_llm_output():
    notes = ["Keep at least 50% of the battery capacity stored in the battery from 6 PM until 9 PM for emergency operations."]
    raw = [{"note_index": 0, "applies": True, "directive_type": "minimum_battery_reserve",
            "structured_adjustment": {"hours": [18, 19, 20], "minimum_energy_kwh": 0.5},
            "explanation": "model fraction placeholder"}]
    patched = interpret_mod._resolve_percentage_of_capacity(raw, notes, BATTERY)
    # BATTERY capacity is 500.0
    assert patched[0]["structured_adjustment"]["minimum_energy_kwh"] == 250.0


def test_resolve_does_not_touch_absolute_reserve():
    notes = ["The data center requires at least 80 kWh to remain in the battery from 6 PM until 10 PM."]
    raw = [{"note_index": 0, "applies": True, "directive_type": "minimum_battery_reserve",
            "structured_adjustment": {"hours": [18, 19, 20, 21], "minimum_energy_kwh": 80.0},
            "explanation": "x"}]
    patched = interpret_mod._resolve_percentage_of_capacity(raw, notes, BATTERY)
    assert patched[0]["structured_adjustment"]["minimum_energy_kwh"] == 80.0


def test_retry_uses_correct_output():
    async def _flaky_call(notes, timeout):
        return '{"interpretations": [{"note_index": 0, "applies": true, "directive_type": "bogus", "structured_adjustment": null, "explanation": "bad"}]}'

    async def _good_retry(messages, timeout):
        return '{"interpretations": [{"note_index": 0, "applies": true, "directive_type": "no_charge_window", "structured_adjustment": {"hours": [2, 3, 4]}, "explanation": "x"}]}'

    with patch.object(interpret_mod.settings, "GROQ_API_KEY", "test-key"), \
         patch.object(interpret_mod, "_call_llm", side_effect=_flaky_call), \
         patch.object(interpret_mod, "_call_llm_with_messages", side_effect=_good_retry):
        entries, path = _run(["the charger is isolated from 2 until 5."])
        assert path == "retry"
        assert entries[0].directive_type == "no_charge_window"