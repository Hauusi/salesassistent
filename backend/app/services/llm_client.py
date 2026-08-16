"""Shared LLM client factories.

Kept as thin factories (not module-level singletons) so tests can inject
fakes/mocks instead of hitting the real Anthropic/Voyage APIs.
"""
from __future__ import annotations

from anthropic import AsyncAnthropic

from app.config import get_settings

settings = get_settings()


def get_anthropic_client() -> AsyncAnthropic:
    return AsyncAnthropic(api_key=settings.anthropic_api_key)
