"""Minimal fakes for the Anthropic SDK surface used by
app.services.classification / app.services.draft_generation, so tests run
fully offline and deterministically instead of calling the real API."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class FakeToolUseBlock:
    input: dict
    name: str = "tool"
    type: str = "tool_use"


@dataclass
class FakeMessage:
    content: list[Any]


class FakeMessagesResource:
    def __init__(self, response_fn: Callable[[dict], FakeMessage]):
        self._response_fn = response_fn
        self.calls: list[dict] = []

    async def create(self, **kwargs) -> FakeMessage:
        self.calls.append(kwargs)
        return self._response_fn(kwargs)


@dataclass
class FakeAnthropicClient:
    response_fn: Callable[[dict], FakeMessage]
    messages: FakeMessagesResource = field(init=False)

    def __post_init__(self) -> None:
        self.messages = FakeMessagesResource(self.response_fn)


def tool_response(**tool_input: Any) -> FakeMessage:
    return FakeMessage(content=[FakeToolUseBlock(input=tool_input)])
