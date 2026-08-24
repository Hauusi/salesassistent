"""Tests for the prompt-size instrumentation, in particular the
cache-eligibility diagnostic.

Both Claude call sites (classification, draft generation) put
`cache_control` on the system block, but their system+tool schema is far
smaller than the per-model minimum cacheable prefix - a documented,
silent no-op (cache_creation_input_tokens: 0, cache_read_input_tokens: 0,
no error) rather than a placement bug. See
app/services/token_metrics.py::_CACHE_MIN_TOKENS_BY_MODEL.
"""
from __future__ import annotations

from types import SimpleNamespace

from app.config import get_settings
from app.services.classification import _CLASSIFY_TOOL
from app.services.classification import _SYSTEM_PROMPT as CLASSIFY_SYSTEM_PROMPT
from app.services.draft_generation import _DRAFT_TOOL
from app.services.draft_generation import _SYSTEM_PROMPT as DRAFT_SYSTEM_PROMPT
from app.services.token_metrics import _CACHE_MIN_TOKENS_BY_MODEL, log_prompt_breakdown


def _usage(**kwargs) -> SimpleNamespace:
    return SimpleNamespace(
        input_tokens=kwargs.get("input_tokens"),
        output_tokens=kwargs.get("output_tokens"),
        cache_creation_input_tokens=kwargs.get("cache_creation_input_tokens", 0),
        cache_read_input_tokens=kwargs.get("cache_read_input_tokens", 0),
    )


def test_small_prompt_on_haiku_is_flagged_below_the_cache_minimum() -> None:
    """This is the actual classify_email shape: ~400 estimated tokens of
    system+tools against Haiku 4.5's 4096-token minimum - cache_read/write
    are 0 not because cache_control is missing or misplaced, but because
    the prefix never clears the threshold that would let it cache at all."""
    breakdown = log_prompt_breakdown(
        "classify_email",
        model="claude-haiku-4-5",
        system="x" * 300,  # ~75 tokens
        tools=[{"name": "classify_email", "input_schema": {"x": "y" * 1200}}],  # ~300+ tokens
        mail_content="Betreff: Anfrage\n\nGuten Tag...",
        usage=_usage(input_tokens=900, cache_creation_input_tokens=0, cache_read_input_tokens=0),
    )

    assert breakdown["cache_min_tokens"] == 4096
    assert breakdown["cacheable_prefix_tokens_est"] < 4096
    assert breakdown["below_cache_minimum"] is True


def test_prompt_above_the_cache_minimum_is_not_flagged() -> None:
    breakdown = log_prompt_breakdown(
        "generate_draft",
        model="claude-sonnet-5",  # 1024-token minimum
        system="x" * 4200,  # ~1050 tokens on its own
        tools=None,
        mail_content="kurz",
        usage=_usage(input_tokens=1100, cache_creation_input_tokens=1050, cache_read_input_tokens=0),
    )

    assert breakdown["cache_min_tokens"] == 1024
    assert breakdown["cacheable_prefix_tokens_est"] >= 1024
    assert breakdown["below_cache_minimum"] is False


def test_unknown_model_is_not_flagged_either_way() -> None:
    """No table entry means no claim - reporting `below_cache_minimum` for
    a model we don't have a documented minimum for would be a guess."""
    breakdown = log_prompt_breakdown(
        "classify_email", model="some-future-model", system="x" * 40, tools=None,
    )

    assert breakdown["cache_min_tokens"] is None
    assert breakdown["below_cache_minimum"] is False


def test_cacheable_prefix_is_system_plus_tools_only() -> None:
    """mail/product/other context never carries cache_control at either
    call site (only the system block does, and tools render before it) -
    the diagnostic must not count volatile per-mail content as part of the
    would-be-cached prefix."""
    breakdown = log_prompt_breakdown(
        "classify_email",
        model="claude-haiku-4-5",
        system="a" * 40,
        tools=[{"name": "t"}],
        mail_content="b" * 100_000,  # huge, but not part of the cached prefix
        product_context="c" * 100_000,
        other_context="d" * 100_000,
    )

    assert breakdown["cacheable_prefix_tokens_est"] == (
        breakdown["system_instructions_tokens_est"] + breakdown["tool_schema_tokens_est"]
    )
    assert breakdown["below_cache_minimum"] is True


def test_classify_email_prompt_is_currently_below_its_model_cache_minimum() -> None:
    """Pins down the actual finding for this codebase: classify_email's
    real system prompt + tool schema, on the real configured
    classification model, does not clear that model's cache minimum -
    cache_read/cache_write being 0 in production logs is this, not a
    cache_control bug. If this test ever flips to False, it means either
    the prompt grew or the model changed enough that caching just became
    viable - a deliberate change worth noticing, not a silent one."""
    model = get_settings().anthropic_classification_model
    breakdown = log_prompt_breakdown(
        "classify_email",
        model=model,
        system=CLASSIFY_SYSTEM_PROMPT,
        tools=[_CLASSIFY_TOOL],
        mail_content="Betreff: Anfrage\n\nGuten Tag, ...",
    )

    cache_min = _CACHE_MIN_TOKENS_BY_MODEL[model]
    assert breakdown["cacheable_prefix_tokens_est"] < cache_min
    assert breakdown["below_cache_minimum"] is True


def test_generate_draft_prompt_is_currently_below_its_model_cache_minimum() -> None:
    """Same pinning test for the draft-generation call site - see
    test_classify_email_prompt_is_currently_below_its_model_cache_minimum."""
    model = get_settings().anthropic_model
    breakdown = log_prompt_breakdown(
        "generate_draft",
        model=model,
        system=DRAFT_SYSTEM_PROMPT,
        tools=[_DRAFT_TOOL],
        mail_content="Betreff: Anfrage\n\nGuten Tag, ...",
    )

    cache_min = _CACHE_MIN_TOKENS_BY_MODEL[model]
    assert breakdown["cacheable_prefix_tokens_est"] < cache_min
    assert breakdown["below_cache_minimum"] is True
