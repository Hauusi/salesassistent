"""Shared LLM client factories, retry policy and response validation.

Every outbound call to Anthropic or Voyage goes through this module. It
exists because the two call sites (app/services/classification.py and
app/services/draft_generation.py) previously each had their own copy of
"create a message, then dig the tool_use block out of the response", with
no retry and no validation of what the model actually returned.

Three responsibilities, kept together because they are the same concern -
"talk to a model and come back with something the domain can trust":

1. Client factories. Thin, not module-level singletons, so tests can inject
   fakes instead of hitting the real APIs.
2. Retry. Rate limits and 5xx/overloaded responses are routine at load, not
   exceptional. They are retried with exponential backoff; anything that
   would fail again identically (auth, malformed request) is not.
3. Validation. Forced tool use makes a well-formed tool call very likely,
   but the API does not hard-enforce a tool schema's `enum` constraints, so
   the raw dict is still untrusted input. `coerce_*` turn it into domain
   values without ever raising into the caller's happy path.
"""
from __future__ import annotations

import asyncio
import enum
import logging
from typing import Any, TypeVar

import anthropic
import voyageai
import voyageai.error as voyage_error
from anthropic import AsyncAnthropic
from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import get_settings

logger = logging.getLogger(__name__)

E = TypeVar("E", bound=enum.Enum)


class LLMCallFailed(RuntimeError):
    """A model call did not succeed even after the configured retries."""


class LLMResponseInvalid(ValueError):
    """The model responded, but not with the forced tool call we required."""


# Transient by nature: the same request has a real chance of succeeding on
# a later attempt. Deliberately excludes AuthenticationError,
# PermissionDeniedError, BadRequestError and NotFoundError - those are
# configuration or programming errors that would fail identically on every
# retry, and retrying them only delays a clear failure.
_RETRYABLE_ANTHROPIC = (
    anthropic.RateLimitError,
    anthropic.APIConnectionError,
    anthropic.APITimeoutError,
    anthropic.InternalServerError,
)

_RETRYABLE_VOYAGE = (
    voyage_error.RateLimitError,
    voyage_error.APIConnectionError,
    voyage_error.ServerError,
    voyage_error.ServiceUnavailableError,
    voyage_error.Timeout,
)


def get_anthropic_client() -> AsyncAnthropic:
    return AsyncAnthropic(api_key=get_settings().anthropic_api_key)


def get_voyage_client() -> voyageai.Client:
    return voyageai.Client(api_key=get_settings().voyage_api_key)


def _retrying(retryable: tuple[type[BaseException], ...], *, call: str) -> AsyncRetrying:
    settings = get_settings()

    def _log_retry(state) -> None:
        logger.warning(
            "llm_call_retry call=%s attempt=%s error=%s",
            call,
            state.attempt_number,
            state.outcome.exception() if state.outcome else None,
        )

    return AsyncRetrying(
        retry=retry_if_exception_type(retryable),
        wait=wait_exponential(
            multiplier=settings.llm_retry_backoff_multiplier,
            min=settings.llm_retry_min_seconds,
            max=settings.llm_retry_max_seconds,
        ),
        stop=stop_after_attempt(settings.llm_max_attempts),
        before_sleep=_log_retry,
        reraise=False,
    )


async def call_tool(
    client: AsyncAnthropic,
    *,
    call: str,
    model: str,
    max_tokens: int,
    system: str,
    tool: dict,
    user_message: str,
) -> tuple[dict[str, Any], Any]:
    """Runs one forced-tool-use Claude call and returns
    ``(tool_input, raw_response)``.

    The caller gets the validated tool input plus the raw response, so it
    can still log token usage from ``response.usage``.

    Raises ``LLMCallFailed`` if every attempt failed on a transient error,
    and ``LLMResponseInvalid`` if the model answered without the forced
    tool call. Both are typed so callers (and the worker) can tell "try
    again later" apart from "this mail will never work".
    """
    response: Any = None
    try:
        async for attempt in _retrying(_RETRYABLE_ANTHROPIC, call=call):
            with attempt:
                response = await client.messages.create(
                    model=model,
                    max_tokens=max_tokens,
                    # A breakpoint on the (only) system block also covers the
                    # tools block, since tools render before system - see
                    # https://docs.claude.com/en/docs/build-with-claude/prompt-caching.
                    # Both are static across every call of a given kind.
                    system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                    tools=[tool],
                    tool_choice={"type": "tool", "name": tool["name"]},
                    messages=[{"role": "user", "content": user_message}],
                )
    except RetryError as exc:
        raise LLMCallFailed(
            f"Claude-Aufruf '{call}' ist nach {get_settings().llm_max_attempts} Versuchen "
            f"fehlgeschlagen: {exc.last_attempt.exception()}"
        ) from exc

    tool_use = next(
        (block for block in response.content if getattr(block, "type", None) == "tool_use"),
        None,
    )
    if tool_use is None:
        raise LLMResponseInvalid(f"Claude hat kein '{tool['name']}' Tool-Ergebnis zurückgegeben.")

    tool_input = tool_use.input
    if not isinstance(tool_input, dict):
        raise LLMResponseInvalid(f"Tool-Eingabe von '{tool['name']}' ist kein Objekt: {type(tool_input)!r}")
    return tool_input, response


async def embed(text: str, *, client: voyageai.Client, model: str, input_type: str) -> list[float]:
    """Embeds a single text, with the same retry policy as the Claude calls.

    The voyageai SDK is synchronous; the caller is responsible for pushing
    this onto a thread (see app/services/embeddings.py).
    """
    def _embed() -> list[float]:
        return client.embed([text], model=model, input_type=input_type).embeddings[0]

    embedding: list[float] = []
    try:
        async for attempt in _retrying(_RETRYABLE_VOYAGE, call="embed_text"):
            with attempt:
                embedding = await asyncio.to_thread(_embed)
    except RetryError as exc:
        raise LLMCallFailed(
            f"Voyage-Embedding ist nach {get_settings().llm_max_attempts} Versuchen "
            f"fehlgeschlagen: {exc.last_attempt.exception()}"
        ) from exc
    return embedding


# --- Validation of model-produced tool input -----------------------------
#
# Forced tool use makes well-formed input very likely, but the API does not
# hard-enforce `enum` in a tool schema, and a model can omit an optional
# field or return a number as a string. These coerce rather than raise so a
# single odd response degrades one mail's metadata instead of aborting the
# whole poll batch (see app/workers/tasks.py).


def coerce_enum(raw: Any, enum_cls: type[E], default: E, *, field: str, call: str) -> E:
    try:
        return enum_cls(raw)
    except ValueError:
        logger.warning(
            "llm_response_coerced call=%s field=%s value=%r fallback=%s",
            call, field, raw, default.value,
        )
        return default


def coerce_float(
    raw: Any, default: float, *, minimum: float, maximum: float, field: str, call: str
) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning(
            "llm_response_coerced call=%s field=%s value=%r fallback=%s",
            call, field, raw, default,
        )
        return default
    return min(max(value, minimum), maximum)


def coerce_str(raw: Any, default: str = "", *, max_chars: int | None = None) -> str:
    value = raw if isinstance(raw, str) else default
    value = value.strip()
    if max_chars is not None and len(value) > max_chars:
        value = value[:max_chars].rstrip()
    return value
