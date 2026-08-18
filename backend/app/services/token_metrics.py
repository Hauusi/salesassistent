"""Prompt-size instrumentation for the two Claude API call sites
(classification, draft generation) - see app/services/classification.py
and app/services/draft_generation.py.

This module does not call the API itself. It estimates, from the exact
strings we are about to send, how the prompt splits across:

  - system_instructions: the fixed system prompt
  - tool_schema: the JSON tool definition(s) (rendered before `system` -
    see https://docs.claude.com/en/docs/build-with-claude/prompt-caching -
    and billed as input tokens like everything else)
  - mail_content: the mail actually being processed
  - product_context: product-catalog grounding from app/services/product_search.py
  - other_context: everything else (RAG contact/case history, ...)

The character-based estimate (~4 chars/token) is only used to attribute
the *relative* split across these buckets - German/mixed prose with a lot
of non-ASCII or non-prose text can tokenize somewhat worse than that
rule of thumb. The authoritative total for a given call is always
`response.usage.input_tokens` (+ `cache_creation_input_tokens` +
`cache_read_input_tokens`), which callers pass in as `usage` so it gets
logged alongside the estimate for comparison - see
https://docs.claude.com/en/docs/build-with-claude/prompt-caching#tracking-cache-performance
for what each usage field means.
"""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("app.llm.tokens")

# Rough, model-independent rule of thumb - good enough to compare buckets
# against each other, not a substitute for the real usage numbers.
_CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, round(len(text) / _CHARS_PER_TOKEN))


def log_prompt_breakdown(
    call: str,
    *,
    model: str,
    system: str = "",
    tools: list[dict] | None = None,
    mail_content: str = "",
    product_context: str = "",
    other_context: str = "",
    usage: Any | None = None,
) -> dict[str, Any]:
    """Estimates the token split of one Claude API call and logs it as a
    single structured line. Returns the breakdown dict too (handy for
    tests / callers that want to assert on it)."""
    tool_schema_text = json.dumps(tools, ensure_ascii=False, sort_keys=True) if tools else ""

    breakdown: dict[str, Any] = {
        "call": call,
        "model": model,
        "system_instructions_tokens_est": estimate_tokens(system),
        "tool_schema_tokens_est": estimate_tokens(tool_schema_text),
        "mail_content_tokens_est": estimate_tokens(mail_content),
        "product_context_tokens_est": estimate_tokens(product_context),
        "other_context_tokens_est": estimate_tokens(other_context),
    }
    breakdown["total_est"] = (
        breakdown["system_instructions_tokens_est"]
        + breakdown["tool_schema_tokens_est"]
        + breakdown["mail_content_tokens_est"]
        + breakdown["product_context_tokens_est"]
        + breakdown["other_context_tokens_est"]
    )

    # Real numbers from the API response, when we have them (both call
    # sites always pass this in after the request completes; tests mock
    # the client and may not provide a `usage` object at all).
    breakdown["actual_input_tokens"] = getattr(usage, "input_tokens", None)
    breakdown["actual_output_tokens"] = getattr(usage, "output_tokens", None)
    breakdown["cache_creation_input_tokens"] = getattr(usage, "cache_creation_input_tokens", None)
    breakdown["cache_read_input_tokens"] = getattr(usage, "cache_read_input_tokens", None)

    logger.info(
        "claude_prompt_breakdown call=%s model=%s system=%d tools=%d mail=%d product=%d other=%d "
        "total_est=%d actual_input=%s cache_read=%s cache_write=%s output=%s",
        breakdown["call"],
        breakdown["model"],
        breakdown["system_instructions_tokens_est"],
        breakdown["tool_schema_tokens_est"],
        breakdown["mail_content_tokens_est"],
        breakdown["product_context_tokens_est"],
        breakdown["other_context_tokens_est"],
        breakdown["total_est"],
        breakdown["actual_input_tokens"],
        breakdown["cache_read_input_tokens"],
        breakdown["cache_creation_input_tokens"],
        breakdown["actual_output_tokens"],
        extra={"claude_prompt_breakdown": breakdown},
    )
    return breakdown
