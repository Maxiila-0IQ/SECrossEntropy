from __future__ import annotations

import logging
import re
from typing import Any

from openai import OpenAI
from openai.types import CompletionUsage

from llm.errors import EmptyLLMContentError, PermanentLLMError, RetryableLLMError

logger = logging.getLogger(__name__)

_RETRYABLE_STATUS_CODES = {429, 500, 503}
_PERMANENT_STATUS_CODES = {400, 401, 402, 422}

_MD_JSON_BLOCK = re.compile(r"```(?:json)?\s*\n?(.*?)\n?\s*```", re.DOTALL)


def _strip_markdown_json(raw: str) -> str:
    """Strip markdown code fences that some models wrap around JSON."""
    stripped = raw.strip()
    # Try fullmatch first (clean case: only a code block)
    match = _MD_JSON_BLOCK.fullmatch(stripped)
    if match:
        return match.group(1).strip()
    # Fallback: search for a code block anywhere in the content
    match = _MD_JSON_BLOCK.search(stripped)
    if match:
        return match.group(1).strip()
    return stripped


class LLMClient:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        timeout: float,
    ) -> None:
        self._model = model
        self._client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
        )
        logger.info(
            "llm_client_init model=%s base_url=%s timeout=%.1f",
            model,
            base_url,
            timeout,
        )

    def parse(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
        *,
        response_format: dict | None = None,
    ) -> tuple[str, CompletionUsage | None]:
        logger.debug(
            "llm_parse_start model=%s max_tokens=%d",
            self._model,
            max_tokens,
        )

        body: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": max_tokens,
        }
        if response_format:
            body["response_format"] = response_format

        try:
            completion = self._client.chat.completions.create(**body)
        except Exception as exc:
            logger.debug("llm_api_exception type=%s", type(exc).__name__)
            self._raise_mapped(exc)
            raise  # unreachable

        content = completion.choices[0].message.content or ""
        usage = completion.usage

        logger.debug(
            "llm_parse_raw_content_len=%d content_preview=%s",
            len(content),
            content[:200] if content else "<empty>",
        )

        content = _strip_markdown_json(content)

        if not content.strip():
            raise EmptyLLMContentError("LLM returned empty content")

        logger.debug("llm_parse_clean_content_len=%d", len(content))

        return content, usage

    def _raise_mapped(self, exc: Exception) -> None:
        status = getattr(exc, "status_code", None)
        if status in _RETRYABLE_STATUS_CODES:
            logger.debug("llm_retryable_status=%s", status)
            raise RetryableLLMError(str(exc)) from exc
        if status in _PERMANENT_STATUS_CODES:
            logger.debug("llm_permanent_status=%s", status)
            raise PermanentLLMError(str(exc)) from exc
        logger.debug("llm_unknown_error treating_as_retryable")
        raise RetryableLLMError(str(exc)) from exc
