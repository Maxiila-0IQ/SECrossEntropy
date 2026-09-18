import json
import logging
import time

from pydantic import ValidationError

from llm.client import LLMClient
from llm.errors import EmptyLLMContentError, PermanentLLMError, RetryableLLMError
from llm.models import (
    DirectiveInterpretationEntry,
    DirectiveInterpretationResponse,
)
from llm.prompts import PROMPT_VERSION, SYSTEM_PROMPT, build_user_prompt

logger = logging.getLogger(__name__)

_BASE_BACKOFF = 1.0


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
        client: LLMClient,
        max_retries: int = 1,
        max_tokens: int = 1200,
        response_format: dict | None = None,
    ) -> None:
        self._client = client
        self._max_retries = max_retries
        self._max_tokens = max_tokens
        self._response_format = response_format
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

        if note_count == 0:
            return _safe_no_op_response(0)

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
            backoff = _BASE_BACKOFF * (2 ** attempt)
            t0 = time.perf_counter()
            try:
                content, usage = self._client.parse(
                    system_prompt=SYSTEM_PROMPT,
                    user_prompt=user_prompt,
                    max_tokens=self._max_tokens,
                    response_format=self._response_format,
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
                    time.sleep(backoff)

            except json.JSONDecodeError as exc:
                last_error = exc
                logger.warning(
                    "llm_json_decode_error scenario=%s attempt=%d error=%s",
                    scenario_id,
                    attempt,
                    exc,
                )
                if attempt < self._max_retries:
                    time.sleep(backoff)

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
                    time.sleep(backoff)

            except RetryableLLMError as exc:
                last_error = exc
                logger.warning(
                    "llm_retryable_error scenario=%s attempt=%d error=%s",
                    scenario_id,
                    attempt,
                    exc,
                )
                if attempt < self._max_retries:
                    logger.debug("retry_backoff scenario=%s sleep=%.1f", scenario_id, backoff)
                    time.sleep(backoff)

            except PermanentLLMError as exc:
                logger.error(
                    "llm_permanent_error scenario=%s error=%s",
                    scenario_id,
                    exc,
                )
                return _safe_no_op_response(note_count)

            except Exception as exc:
                last_error = exc
                logger.warning(
                    "llm_unexpected_error scenario=%s attempt=%d type=%s error=%s",
                    scenario_id,
                    attempt,
                    type(exc).__name__,
                    exc,
                )
                if attempt < self._max_retries:
                    time.sleep(backoff)

        logger.error(
            "llm_all_retries_exhausted scenario=%s last_error=%s",
            scenario_id,
            last_error,
        )
        return _safe_no_op_response(note_count)
