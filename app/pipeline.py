import asyncio
import hashlib
import json
import logging
import re
import time

from app.config import settings
from app.schemas import DirectiveEntry
from app.fallback import parse_notes as fallback_parse
from app.guardrails import validate_entries

from llm import LLMClient, LLMInterpreter

logger = logging.getLogger("gridwise.interpret")

_prompt_cache: dict[str, list[dict]] = {}
_CACHE_MAX = 256

_CAPACITY_PCT_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*%\s*(?:of\s+)?(?:the\s+)?battery'?s?\s+capacity",
    re.IGNORECASE,
)
_CAPACITY_PCT_SPLIT_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*%\b[^.]*\bcapacity\b", re.IGNORECASE,
)


def _cache_key(notes: list[str]) -> str:
    blob = json.dumps([n.strip() for n in notes], ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _build_client() -> LLMClient:
    return LLMClient(
        api_key=settings.DEEPSEEK_API_KEY or "not-needed",
        base_url=settings.LLM_BASE_URL,
        model=settings.LLM_MODEL,
        timeout=settings.LLM_TIMEOUT,
    )


def _build_interpreter() -> LLMInterpreter:
    return LLMInterpreter(
        client=_build_client(),
        max_retries=2,
        max_tokens=1200,
    )


def _resolve_percentage_of_capacity(entries: list[dict], notes: list[str], battery) -> list[dict]:
    """Resolve '50% of battery capacity' to actual kWh."""
    capacity = getattr(battery, "capacity_kwh", None)
    if not capacity or capacity <= 0:
        return entries
    for entry in entries:
        adj = entry.get("structured_adjustment")
        if entry.get("directive_type") == "minimum_battery_reserve" and isinstance(adj, dict):
            idx = entry.get("note_index")
            if isinstance(idx, int) and 0 <= idx < len(notes):
                text = notes[idx]
                m = _CAPACITY_PCT_RE.search(text) or _CAPACITY_PCT_SPLIT_RE.search(text)
                if m:
                    try:
                        pct = float(m.group(1))
                        adj["minimum_energy_kwh"] = round(pct / 100.0 * capacity, 3)
                    except (TypeError, ValueError):
                        pass
    return entries


def _entries_to_dicts(entries) -> list[dict]:
    """Convert llm DirectiveInterpretationEntry objects to raw dicts for guardrails."""
    return [
        {
            "note_index": e.note_index,
            "applies": e.applies,
            "directive_type": e.directive_type,
            "structured_adjustment": e.structured_adjustment,
            "explanation": e.explanation,
        }
        for e in entries
    ]


def _directives_from_fallback(notes: list[str], battery) -> tuple[list[DirectiveEntry], str]:
    raw = fallback_parse(notes, capacity_kwh=getattr(battery, "capacity_kwh", None))
    entries, errors = validate_entries(raw, len(notes), battery)
    return entries, "fallback"


async def interpret_notes(
    notes: list[str],
    battery,
) -> tuple[list[DirectiveEntry], str]:
    n = len(notes)

    cached = _prompt_cache.get(_cache_key(notes))
    if cached is not None:
        entries, _ = validate_entries(cached, n, battery)
        return entries, "llm"

    if not settings.DEEPSEEK_API_KEY:
        logger.info("No DEEPSEEK_API_KEY set; using fallback parser")
        return _directives_from_fallback(notes, battery)

    interpreter = _build_interpreter()
    scenario_id = f"api-{hashlib.md5(json.dumps(notes).encode()).hexdigest()[:8]}"

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(interpreter.interpret, notes, scenario_id=scenario_id),
            timeout=settings.LLM_TIMEOUT + 2,
        )
    except asyncio.TimeoutError:
        logger.warning("LLM call timed out; using fallback parser")
        return _directives_from_fallback(notes, battery)
    except Exception as exc:
        logger.warning("LLM call failed: %s", exc)
        return _directives_from_fallback(notes, battery)

    raw_list = _entries_to_dicts(result.directive_interpretation)
    raw_list = _resolve_percentage_of_capacity(raw_list, notes, battery)

    entries, errors = validate_entries(raw_list, n, battery)
    if not errors:
        if len(_prompt_cache) < _CACHE_MAX:
            _prompt_cache[_cache_key(notes)] = raw_list
        return entries, "llm"

    logger.info("LLM output had errors %s; using fallback", errors)
    return _directives_from_fallback(notes, battery)
