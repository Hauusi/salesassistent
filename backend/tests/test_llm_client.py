"""Tests for the shared LLM call layer: retry policy, forced-tool
extraction and coercion of untrusted model output.

These cover the failure modes that used to abort a whole poll batch (see
app/workers/tasks.py): a transient rate limit, a response without the
forced tool call, and a tool call whose fields don't match the schema.
"""
from __future__ import annotations

import anthropic
import httpx
import pytest

from app.models.enums import TypKategorie, WichtigkeitsKategorie
from app.services.llm_client import (
    LLMCallFailed,
    LLMResponseInvalid,
    call_tool,
    coerce_enum,
    coerce_float,
    coerce_str,
)
from tests.mocks import FakeAnthropicClient, FakeMessage, tool_response

_TOOL = {"name": "demo_tool", "input_schema": {"type": "object", "properties": {}}}


def _rate_limit_error() -> anthropic.RateLimitError:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(429, request=request)
    return anthropic.RateLimitError("rate limited", response=response, body=None)


def _auth_error() -> anthropic.AuthenticationError:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(401, request=request)
    return anthropic.AuthenticationError("bad key", response=response, body=None)


@pytest.fixture(autouse=True)
def _fast_retries(monkeypatch):
    """Keep the retry *policy* under test but drop the wall-clock waits."""
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "llm_retry_min_seconds", 0.0, raising=False)
    monkeypatch.setattr(settings, "llm_retry_max_seconds", 0.0, raising=False)
    monkeypatch.setattr(settings, "llm_retry_backoff_multiplier", 0.0, raising=False)


async def test_call_tool_retries_a_transient_rate_limit_and_then_succeeds() -> None:
    attempts = {"n": 0}

    def respond(_kwargs) -> FakeMessage:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise _rate_limit_error()
        return tool_response(ok=True)

    client = FakeAnthropicClient(response_fn=respond)
    data, _response = await call_tool(
        client, call="demo", model="m", max_tokens=10, system="s", tool=_TOOL, user_message="u"
    )

    assert data == {"ok": True}
    assert attempts["n"] == 3


async def test_call_tool_gives_up_after_the_configured_attempts() -> None:
    attempts = {"n": 0}

    def respond(_kwargs) -> FakeMessage:
        attempts["n"] += 1
        raise _rate_limit_error()

    client = FakeAnthropicClient(response_fn=respond)
    with pytest.raises(LLMCallFailed):
        await call_tool(
            client, call="demo", model="m", max_tokens=10, system="s", tool=_TOOL, user_message="u"
        )

    from app.config import get_settings

    assert attempts["n"] == get_settings().llm_max_attempts


async def test_call_tool_does_not_retry_a_permanent_auth_error() -> None:
    """Retrying a bad API key only delays a clear failure."""
    attempts = {"n": 0}

    def respond(_kwargs) -> FakeMessage:
        attempts["n"] += 1
        raise _auth_error()

    client = FakeAnthropicClient(response_fn=respond)
    with pytest.raises(anthropic.AuthenticationError):
        await call_tool(
            client, call="demo", model="m", max_tokens=10, system="s", tool=_TOOL, user_message="u"
        )

    assert attempts["n"] == 1


async def test_call_tool_raises_a_typed_error_without_a_tool_use_block() -> None:
    client = FakeAnthropicClient(response_fn=lambda _k: FakeMessage(content=[]))
    with pytest.raises(LLMResponseInvalid):
        await call_tool(
            client, call="demo", model="m", max_tokens=10, system="s", tool=_TOOL, user_message="u"
        )


async def test_call_tool_forces_the_tool_and_marks_the_system_prompt_cacheable() -> None:
    client = FakeAnthropicClient(response_fn=lambda _k: tool_response(ok=True))
    await call_tool(
        client, call="demo", model="m", max_tokens=10, system="s", tool=_TOOL, user_message="u"
    )

    sent = client.messages.calls[0]
    assert sent["tool_choice"] == {"type": "tool", "name": "demo_tool"}
    assert sent["system"][0]["cache_control"] == {"type": "ephemeral"}


# --- coercion of untrusted model output ---------------------------------


def test_coerce_enum_falls_back_on_a_value_outside_the_schema_enum() -> None:
    result = coerce_enum(
        "voellig_erfunden", WichtigkeitsKategorie, WichtigkeitsKategorie.INFORMATION,
        field="wichtigkeits_kategorie", call="demo",
    )
    assert result is WichtigkeitsKategorie.INFORMATION


def test_coerce_enum_passes_a_valid_value_through() -> None:
    result = coerce_enum(
        "bestellung", TypKategorie, TypKategorie.KEINER, field="typ", call="demo"
    )
    assert result is TypKategorie.BESTELLUNG


def test_coerce_float_accepts_a_numeric_string_and_clamps_to_range() -> None:
    assert coerce_float("0.7", 0.0, minimum=0.0, maximum=1.0, field="c", call="d") == 0.7
    assert coerce_float(5.0, 0.0, minimum=0.0, maximum=1.0, field="c", call="d") == 1.0
    assert coerce_float(-2.0, 0.0, minimum=0.0, maximum=1.0, field="c", call="d") == 0.0


def test_coerce_float_falls_back_on_non_numeric_input() -> None:
    assert coerce_float("hoch", 0.0, minimum=0.0, maximum=1.0, field="c", call="d") == 0.0
    assert coerce_float(None, 0.0, minimum=0.0, maximum=1.0, field="c", call="d") == 0.0


def test_coerce_str_falls_back_and_truncates() -> None:
    assert coerce_str(None, "fallback") == "fallback"
    assert coerce_str(123, "fallback") == "fallback"
    assert coerce_str("  hallo  ") == "hallo"
    assert coerce_str("a" * 20, max_chars=5) == "aaaaa"
