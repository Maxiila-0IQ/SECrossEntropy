from __future__ import annotations

import json
import logging
import time

from pydantic import ValidationError

from llm.client import DeepSeekClient
from llm.errors import EmptyLLMContentError, PermanentLLMError, RetryableLLMError
from llm.models import (
    DirectiveInterpretationEntry,
    DirectiveInterpretationResponse,
)
from llm.prompts import PROMPT_VERSION, SYSTEM_PROMPT, build_user_prompt

logger = logging.getLogger(__name__)

_RETRY_BACKOFF_SECONDS = 1.0


def _safe_no_op_response(note_count: int) -> DirectiveInterpretationResponse:
    logger.info("safe_fallback note_count=%d -> all no_op", note_count)
    return DirectiveInterpretationResponse(
        directive_interpretation=[
            DirectiveInterpretationEntry(
                note_index=i,
                applies=False,
                directive_type="no_op",
                structured_adjustment=None,
                explanation="Interpretation unavailable; defaulting to no_op.",
            )
            for i in range(note_count)
        ]
    )


class LLMInterpreter:
    def __init__(
        self,
        client: DeepSeekClient,
        max_retries: int = 1,
        max_tokens: int = 1200,
    ) -> None:
        self._client = client
        self._max_retries = max_retries
        self._max_tokens = max_tokens
        logger.info(
            "llm_interpreter_init max_retries=%d max_tokens=%d",
            max_retries,
            max_tokens,
        )

    def interpret(
        self,
        operator_notes: list[str],
        *,
        scenario_id: str = "",
    ) -> DirectiveInterpretationResponse:
        note_count = len(operator_notes)
        user_prompt = build_user_prompt(operator_notes)

        logger.info(
            "interpret_start scenario=%s note_count=%d prompt_version=%s",
            scenario_id,
            note_count,
            PROMPT_VERSION,
        )
        logger.debug(
            "interpret_user_prompt scenario=%s prompt=%s",
            scenario_id,
            user_prompt,
        )

        last_error: Exception | None = None

        for attempt in range(self._max_retries + 1):
            t0 = time.perf_counter()
            try:
                content, usage = self._client.parse(
                    system_prompt=SYSTEM_PROMPT,
                    user_prompt=user_prompt,
                    max_tokens=self._max_tokens,
                )
                latency_ms = (time.perf_counter() - t0) * 1000

                logger.info(
                    "llm_parse_ok scenario=%s attempt=%d latency_ms=%.0f "
                    "prompt_tokens=%s completion_tokens=%s",
                    scenario_id,
                    attempt,
                    latency_ms,
                    usage.prompt_tokens if usage else None,
                    usage.completion_tokens if usage else None,
                )

                raw = json.loads(content)
                logger.debug(
                    "llm_raw_json scenario=%s json=%s",
                    scenario_id,
                    json.dumps(raw, indent=2),
                )

                result = DirectiveInterpretationResponse.model_validate(raw)

                logger.info(
                    "interpret_success scenario=%s entries=%d types=%s",
                    scenario_id,
                    len(result.directive_interpretation),
                    [e.directive_type for e in result.directive_interpretation],
                )

                return result

            except EmptyLLMContentError as exc:
                last_error = exc
                logger.warning(
                    "llm_empty_content scenario=%s attempt=%d",
                    scenario_id,
                    attempt,
                )
                if attempt < self._max_retries:
                    time.sleep(_RETRY_BACKOFF_SECONDS)

            except json.JSONDecodeError as exc:
                last_error = exc
                logger.warning(
                    "llm_json_decode_error scenario=%s attempt=%d error=%s",
                    scenario_id,
                    attempt,
                    exc,
                )
                if attempt < self._max_retries:
                    time.sleep(_RETRY_BACKOFF_SECONDS)

            except ValidationError as exc:
                last_error = exc
                logger.warning(
                    "llm_validation_error scenario=%s attempt=%d error_count=%d",
                    scenario_id,
                    attempt,
                    len(exc.errors()),
                )
                for err in exc.errors():
                    logger.debug(
                        "llm_validation_detail scenario=%s loc=%s type=%s",
                        scenario_id,
                        err["loc"],
                        err["type"],
                    )
                if attempt < self._max_retries:
                    time.sleep(_RETRY_BACKOFF_SECONDS)

            except RetryableLLMError as exc:
                last_error = exc
                logger.warning(
                    "llm_retryable_error scenario=%s attempt=%d error=%s",
                    scenario_id,
                    attempt,
                    exc,
                )
                if attempt < self._max_retries:
                    logger.debug("retry_backoff scenario=%s sleep=%.1f", scenario_id, _RETRY_BACKOFF_SECONDS)
                    time.sleep(_RETRY_BACKOFF_SECONDS)

            except PermanentLLMError as exc:
                logger.error(
                    "llm_permanent_error scenario=%s error=%s",
                    scenario_id,
                    exc,
                )
                return _safe_no_op_response(note_count)

        logger.error(
            "llm_all_retries_exhausted scenario=%s last_error=%s",
            scenario_id,
            last_error,
        )
        return _safe_no_op_response(note_count)
