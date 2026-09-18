class LLMError(Exception):
    """Base exception for LLM subsystem."""


class EmptyLLMContentError(LLMError):
    """LLM returned empty content in JSON mode."""


class RetryableLLMError(LLMError):
    """Transient provider error that may succeed on retry (429, 500, 503)."""


class PermanentLLMError(LLMError):
    """Non-retryable provider error (400, 401, 402, 403, 422)."""
