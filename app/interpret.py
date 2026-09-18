import asyncio
import hashlib
import json
import re
import time
import logging
from typing import Any

import httpx

from app.config import settings
from app.schemas import DirectiveEntry
from app.fallback import parse_notes as fallback_parse
from app.guardrails import validate_entries

logger = logging.getLogger("gridwise.interpret")

_prompt_cache: dict[str, list[dict]] = {}


_PROMPT = """You are an energy-schedule directive interpreter.

Given 1–3 operator notes, produce exactly one structured directive per note. The system has 6 directive types:

1. solar_reduction — structured_adjustment: {"hours": [int], "factor": float}
2. minimum_battery_reserve — structured_adjustment: {"hours": [int], "minimum_energy_kwh": float}
3. no_charge_window — structured_adjustment: {"hours": [int]}
4. no_discharge_window — structured_adjustment: {"hours": [int]}
5. max_grid_window — structured_adjustment: {"hours": [int], "max_grid_kwh": float}
6. no_op — structured_adjustment: null

WINDOWS ARE START-INCLUSIVE, END-EXCLUSIVE.
- "1 PM to 3 PM" → [13, 14]  (includes hour 13 and 14, but not 15)
- "2 AM until 5 AM" → [2, 3, 4]
- "noon until 2 PM" → [12, 13]

WRAP-AROUND MIDNIGHT:
- "10 PM to 2 AM" → [0, 1, 22, 23] (sorted ascending after wrapping)
- "from 18:00 to 6:00" → [0,1,2,3,4,5,18,19,20,21,22,23]

FACTOR IS THE FRACTION THAT REMAINS (not the amount removed):
- "80% reduction" → factor: 0.20 (leaving 20%)
- "drop to 25%" → factor: 0.25 (at 25%)
- "one-fifth of normal" → factor: 0.20
- "no output" / "offline" → factor: 0.0

DISAMBIGUATION:
- "charger is isolated" / "do not charge" → no_charge_window (equipment: charger)
- "must not be drawn down" / "no discharge" → no_discharge_window (action: discharge)
- "keep the battery idle" → no_charge_window (policy rule, one directive per note only)
- "no grid import" → max_grid_window with max_grid_kwh: 0
- "solar offline" / "no PV output" → solar_reduction with factor: 0.0

A note with an energy rule but a vague window: still emit the directive with best-guess hours. NEVER downgrade an energy-relevant note to no_op. If the note does NOT change the 24-hour electricity schedule at all (e.g., administrative, unrelated), return no_op with applies: false.

NEVER invent demand, tariff, solar, or battery values. Never invent a directive type outside the six listed.

WORKED EXAMPLES:
1. "Facilities will wash the rooftop solar panels from noon until 2 PM. Treat usable solar as roughly 25% of the forecast."
   → solar_reduction, hours: [12, 13], factor: 0.25

2. "The battery charger will be isolated from 2 AM until 5 AM for electrical maintenance."
   → no_charge_window, hours: [2, 3, 4]

3. "PV production will drop to about 20% between 13:00 and 15:00."
   → solar_reduction, hours: [13, 14], factor: 0.2

4. "Expect an 80% reduction in rooftop solar during the 1-3 PM maintenance window."
   → solar_reduction, hours: [13, 14], factor: 0.20

5. "Panel washing from one until three will leave roughly one-fifth of normal solar output."
   → solar_reduction, hours: [13, 14], factor: 0.2

6. "Solar will be offline entirely from 11 AM to 1 PM."
   → solar_reduction, hours: [11, 12], factor: 0.0

7. "Do not charge the battery between 2 PM and 4 PM."
   → no_charge_window, hours: [14, 15]

8. "The battery must not be drawn down between 6 and 9 PM."
   → no_discharge_window, hours: [18, 19, 20]

9. "Keep at least 120 kWh in reserve from 6 PM until 9 PM."
   → minimum_battery_reserve, hours: [18, 19, 20], minimum_energy_kwh: 120

10. "Don't let the battery fall below 120 kWh from 18:00 to 21:00."
    → minimum_battery_reserve, hours: [18, 19, 20], minimum_energy_kwh: 120

11. "Grid draw must stay under 150 kWh from 6 to 9 PM."
    → max_grid_window, hours: [18, 19, 20], max_grid_kwh: 150

12. "No grid import from 10 PM until 2 AM."
    → max_grid_window, hours: [0, 1, 22, 23], max_grid_kwh: 0

13. "Keep the battery idle from 2 to 4 PM."
    → no_charge_window, hours: [14, 15]

DISTRACTOR EXAMPLES (no_op):
14. "The cafeteria menu changes tomorrow." → no_op
15. "The sports office moved next month's registration deadline." → no_op
16. "WiFi maintenance in Building 3 overnight." → no_op

OUTPUT FORMAT (JSON only, no markdown, no commentary):
{"interpretations": [
  {"note_index": 0, "applies": true, "directive_type": "...", "structured_adjustment": {...}, "explanation": "..."},
  {"note_index": 1, "applies": false, "directive_type": "no_op", "structured_adjustment": null, "explanation": "..."}
]}

You MUST return exactly one entry per note. The note_index fields must be 0, 1, ..., N-1.
"""


def _cache_key(notes: list[str]) -> str:
    blob = json.dumps([n.strip() for n in notes], ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _parse_llm_response(raw: str) -> list[dict] | None:
    try:
        if raw.startswith("```"):
            lines = raw.strip().splitlines()
            inner = "\n".join(lines[1:-1]) if len(lines) > 2 else lines[0]
            raw = inner
        obj = json.loads(raw)
        if isinstance(obj, dict) and "interpretations" in obj:
            entries = obj["interpretations"]
            if isinstance(entries, list):
                return entries
        return None
    except Exception:
        return None


_CAPACITY_PCT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%\s*(?:of\s+)?(?:the\s+)?battery'?s?\s+capacity", re.IGNORECASE)
_CAPACITY_PCT_SPLIT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%\b[^.]*\bcapacity\b", re.IGNORECASE)


def _resolve_percentage_of_capacity(raw_list: list[dict], notes: list[str], battery) -> list[dict]:
    """The model cannot know battery capacity. When a reserve note states an
    explicit percentage of battery capacity, resolve it deterministically from
    the known capacity (e.g. '50% of the battery capacity' -> 0.5 * capacity)."""
    capacity = getattr(battery, "capacity_kwh", None)
    if not capacity or capacity <= 0:
        return raw_list
    patched = []
    for entry in raw_list:
        idx = entry.get("note_index")
        adj = entry.get("structured_adjustment")
        if (entry.get("directive_type") == "minimum_battery_reserve"
                and isinstance(idx, int) and 0 <= idx < len(notes)
                and isinstance(adj, dict)):
            text = notes[idx]
            m = _CAPACITY_PCT_RE.search(text) or _CAPACITY_PCT_SPLIT_RE.search(text)
            if m:
                try:
                    pct = float(m.group(1))
                except (TypeError, ValueError):
                    pct = None
                if pct is not None:
                    adj["minimum_energy_kwh"] = round(pct / 100.0 * capacity, 3)
        patched.append(entry)
    return patched


async def _call_llm(notes: list[str], timeout: float) -> str | None:
    messages = [
        {"role": "system", "content": _PROMPT},
        {"role": "user", "content": "\n".join(f"[{i}] {note}" for i, note in enumerate(notes))},
    ]
    return await _call_llm_with_messages(messages, timeout)


async def _call_llm_with_messages(messages: list[dict], timeout: float) -> str | None:
    if not settings.GROQ_API_KEY:
        return None
    body: dict[str, Any] = {
        "model": settings.LLM_MODEL,
        "messages": messages,
        "temperature": settings.LLM_TEMPERATURE,
    }
    if settings.LLM_USE_JSON_MODE:
        body["response_format"] = {"type": "json_object"}

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{settings.LLM_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.GROQ_API_KEY}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
    except Exception as exc:
        logger.warning("LLM call failed: %s", exc)
        return None


def _directives_from_fallback(notes: list[str], battery) -> tuple[list[DirectiveEntry], str]:
    raw = fallback_parse(notes, capacity_kwh=getattr(battery, "capacity_kwh", None))
    entries, errors = validate_entries(raw, len(notes), battery)
    return entries, "fallback"


def _directives_from_raw(raw_list: list[dict], notes: list[str], battery) -> tuple[list[DirectiveEntry], str]:
    entries, errors = validate_entries(raw_list, len(notes), battery)
    return entries, errors


async def interpret_notes(
    notes: list[str],
    battery,
) -> tuple[list[DirectiveEntry], str]:
    n = len(notes)
    cached = _prompt_cache.get(_cache_key(notes))
    if cached is not None:
        entries, _ = validate_entries(cached, n, battery)
        return entries, "llm"

    if not settings.GROQ_API_KEY:
        logger.info("No GROQ_API_KEY set; using fallback parser")
        return _directives_from_fallback(notes, battery)

    try:
        raw_text = await asyncio.wait_for(_call_llm(notes, settings.LLM_TIMEOUT), timeout=settings.LLM_TIMEOUT + 1)
    except asyncio.TimeoutError:
        logger.warning("LLM call timed out; using fallback parser")
        return _directives_from_fallback(notes, battery)

    raw_list = _parse_llm_response(raw_text) if raw_text else None
    if raw_list is not None:
        raw_list = _resolve_percentage_of_capacity(raw_list, notes, battery)
        entries, errors = validate_entries(raw_list, n, battery)
        if not errors:
            try:
                _prompt_cache[_cache_key(notes)] = raw_list
            except Exception:
                pass
            return entries, "llm"
        else:
            logger.info("LLM output had errors %s; retrying with error text appended", errors)
            error_feedback = "The previous output had errors:\n" + "\n".join(errors) + "\nPlease fix and return the corrected JSON output."
            retry_messages = [
                {"role": "system", "content": _PROMPT},
                {"role": "user", "content": "\n".join(f"[{i}] {note}" for i, note in enumerate(notes))},
                {"role": "assistant", "content": raw_text},
                {"role": "user", "content": error_feedback},
            ]
            try:
                retry_raw = await asyncio.wait_for(
                    _call_llm_with_messages(retry_messages, settings.LLM_TIMEOUT),
                    timeout=settings.LLM_TIMEOUT + 1,
                )
                retry_list = _parse_llm_response(retry_raw)
                if retry_list:
                    retry_list = _resolve_percentage_of_capacity(retry_list, notes, battery)
                    entries2, errors2 = validate_entries(retry_list, n, battery)
                    if not errors2:
                        try:
                            _prompt_cache[_cache_key(notes)] = retry_list
                        except Exception:
                            pass
                        return entries2, "retry"
            except asyncio.TimeoutError:
                logger.warning("LLM retry timed out; using fallback")
            except Exception as exc:
                logger.warning("LLM retry call failed: %s", exc)

    logger.info("Falling back to deterministic parser for all notes")
    return _directives_from_fallback(notes, battery)