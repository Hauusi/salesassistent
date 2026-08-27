"""Tests for the required-settings startup check - the one that must crash
the process hard, in every environment, rather than let a missing
credential fail opaquely on whichever request/job touches it first.
"""
from __future__ import annotations

import pytest

from app.config import Settings
from app.services import startup_checks

_VALID_FERNET_KEY = "fe_jOcQiCyBI5Y6orwejRJg5DpKYM1YLJtEOR6k4jcU="

_VALID_OVERRIDES = {
    "google_client_id": "123456789-abcdefghijklmnop.apps.googleusercontent.com",
    "google_client_secret": "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gth",
    "anthropic_api_key": "sk-ant-api03-" + "a" * 40,
    "voyage_api_key": "pa-" + "b" * 40,
    "token_encryption_key": _VALID_FERNET_KEY,
}


def _settings(**overrides) -> Settings:
    merged = {**_VALID_OVERRIDES, **overrides}
    return Settings(**merged)


def test_a_fully_configured_dev_environment_passes() -> None:
    startup_checks.verify_required_settings(_settings(app_env="development"))


def test_a_fully_configured_production_environment_passes() -> None:
    startup_checks.verify_required_settings(
        _settings(
            app_env="production",
            google_redirect_uri="https://app.example.com/api/auth/gmail/callback",
            frontend_base_url="https://app.example.com",
            api_base_url="https://api.example.com",
        )
    )


@pytest.mark.parametrize(
    "field",
    ["google_client_id", "google_client_secret", "anthropic_api_key", "voyage_api_key"],
)
def test_a_missing_required_secret_is_fatal(field: str) -> None:
    with pytest.raises(startup_checks.StartupCheckFailed, match=field.upper()):
        startup_checks.verify_required_settings(_settings(**{field: ""}))


def test_a_missing_required_secret_is_fatal_in_development_too() -> None:
    """The carve-out that lets the API/DB-agreement checks stay soft in
    development (see app/main.py) must not apply here."""
    with pytest.raises(startup_checks.StartupCheckFailed):
        startup_checks.verify_required_settings(
            _settings(app_env="development", anthropic_api_key="")
        )


@pytest.mark.parametrize(
    "field,placeholder",
    [
        ("google_client_id", "your-google-client-id"),
        ("google_client_secret", "changeme"),
        ("anthropic_api_key", "REPLACE_ME_WITH_REAL_KEY"),
        ("voyage_api_key", "<voyage-api-key>"),
    ],
)
def test_a_placeholder_looking_secret_is_fatal(field: str, placeholder: str) -> None:
    with pytest.raises(startup_checks.StartupCheckFailed):
        startup_checks.verify_required_settings(_settings(**{field: placeholder}))


def test_a_short_obviously_fake_secret_is_fatal_even_without_a_marker_word() -> None:
    with pytest.raises(startup_checks.StartupCheckFailed):
        startup_checks.verify_required_settings(_settings(anthropic_api_key="test123"))


def test_an_empty_token_encryption_key_is_fatal() -> None:
    with pytest.raises(startup_checks.StartupCheckFailed, match="TOKEN_ENCRYPTION_KEY"):
        startup_checks.verify_required_settings(_settings(token_encryption_key=""))


def test_a_structurally_invalid_token_encryption_key_is_fatal() -> None:
    """A key that is present but not valid Fernet fails every OAuth token
    read/write identically to a missing one - just harder to spot by eye."""
    with pytest.raises(startup_checks.StartupCheckFailed, match="TOKEN_ENCRYPTION_KEY"):
        startup_checks.verify_required_settings(
            _settings(token_encryption_key="not-a-valid-fernet-key-at-all")
        )


def test_localhost_urls_are_fine_in_development() -> None:
    """The code-level defaults for these three ARE localhost - correct and
    documented for local development (see .env.example)."""
    startup_checks.verify_required_settings(
        _settings(
            app_env="development",
            google_redirect_uri="http://localhost:8000/api/auth/gmail/callback",
            frontend_base_url="http://localhost:3000",
            api_base_url="http://localhost:8000",
        )
    )


@pytest.mark.parametrize(
    "field",
    ["google_redirect_uri", "frontend_base_url", "api_base_url"],
)
def test_a_localhost_url_outside_development_is_fatal(field: str) -> None:
    """The most common real deployment bug this catches: forgetting to
    override one of these for a real environment, leaving the code's
    localhost fallback silently in place."""
    with pytest.raises(startup_checks.StartupCheckFailed, match=field.upper()):
        startup_checks.verify_required_settings(
            _settings(app_env="production", **{field: "http://localhost:8000/x"})
        )


def test_multiple_problems_are_all_reported_at_once() -> None:
    """A deployer fixing config one variable at a time, restarting between
    each, is a worse experience than seeing every problem in one error."""
    with pytest.raises(startup_checks.StartupCheckFailed) as exc_info:
        startup_checks.verify_required_settings(
            _settings(google_client_id="", anthropic_api_key="", voyage_api_key="")
        )
    message = str(exc_info.value)
    assert "GOOGLE_CLIENT_ID" in message
    assert "ANTHROPIC_API_KEY" in message
    assert "VOYAGE_API_KEY" in message
