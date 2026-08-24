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

# Minimum cacheable prefix per model, in tokens - a `cache_control` marker
# below this is a silent no-op: no error, cache_creation_input_tokens: 0,
# cache_read_input_tokens: 0, forever, no matter how many times the call
# runs (https://docs.claude.com/en/docs/build-with-claude/prompt-caching).
# Not monotonic across model generations, so this can't be derived from
# the model name - it has to be a table. Both call sites here (see
# app/services/classification.py, app/services/draft_generation.py) put
# `cache_control` on the system block, which - because tools render before
# system - also covers the tool schema; this module compares that combined
# size against the model actually used for the call.
_CACHE_MIN_TOKENS_BY_MODEL: dict[str, int] = {
    "claude-opus-5": 512,
    "claude-fable-5": 512,
    "claude-mythos-5": 512,
    "claude-opus-4-8": 1024,
    "claude-sonnet-5": 1024,
    "claude-sonnet-4-6": 1024,
    "claude-sonnet-4-5": 1024,
    "claude-opus-4-1": 1024,
    "claude-opus-4": 1024,
    "claude-sonnet-4": 1024,
    "claude-opus-4-7": 2048,
    "claude-haiku-3-5": 2048,
    "claude-opus-4-6": 4096,
    "claude-opus-4-5": 4096,
    "claude-haiku-4-5": 4096,
}


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

    # `cache_control` sits on the system block at both call sites, and
    # tools render before system - so the cached prefix is system+tools
    # combined. Below the model's minimum, cache_control is a documented
    # no-op (not a bug to chase): flagging it here is what turns "why is
    # cache_read always 0" from a fresh investigation into a one-line
    # answer in the log itself.
    cacheable_prefix_tokens_est = (
        breakdown["system_instructions_tokens_est"] + breakdown["tool_schema_tokens_est"]
    )
    cache_min_tokens = _CACHE_MIN_TOKENS_BY_MODEL.get(model)
    breakdown["cacheable_prefix_tokens_est"] = cacheable_prefix_tokens_est
    breakdown["cache_min_tokens"] = cache_min_tokens
    breakdown["below_cache_minimum"] = (
        cache_min_tokens is not None and cacheable_prefix_tokens_est < cache_min_tokens
    )

    logger.info(
        "claude_prompt_breakdown call=%s model=%s system=%d tools=%d mail=%d product=%d other=%d "
        "total_est=%d actual_input=%s cache_read=%s cache_write=%s output=%s "
        "cacheable_prefix_est=%d cache_min=%s below_cache_min=%s",
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
        breakdown["cacheable_prefix_tokens_est"],
        breakdown["cache_min_tokens"],
        breakdown["below_cache_minimum"],
        extra={"claude_prompt_breakdown": breakdown},
    )

    if breakdown["below_cache_minimum"]:
        logger.info(
            "claude_prompt_cache_ineligible call=%s model=%s cacheable_prefix_est=%d "
            "cache_min=%d - cache_control is a documented no-op below the model's minimum "
            "cacheable prefix; this is not fixable by changing how cache_control is set, "
            "only by growing the shared system+tools prefix past cache_min or switching to "
            "a model with a lower minimum",
            call,
            model,
            cacheable_prefix_tokens_est,
            cache_min_tokens,
        )
    return breakdown
