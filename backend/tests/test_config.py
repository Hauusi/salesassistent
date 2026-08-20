"""Tests for settings that are derived rather than read straight through.

A wrong CORS origin does not fail loudly: the API keeps answering curl
perfectly while every browser request is blocked, and the UI shows only a
generic fetch error. Worth pinning down.
"""
from __future__ import annotations

from app.config import Settings


def _settings(**overrides) -> Settings:
    return Settings(**overrides)


def test_localhost_and_127_are_both_allowed_by_default() -> None:
    """A browser treats these as different origins, and developers use both
    interchangeably - allowing only one makes the app dead on the other."""
    origins = _settings(frontend_base_url="http://localhost:3000").cors_allowed_origins_list

    assert "http://localhost:3000" in origins
    assert "http://127.0.0.1:3000" in origins


def test_the_sibling_is_derived_from_either_direction() -> None:
    origins = _settings(frontend_base_url="http://127.0.0.1:3000").cors_allowed_origins_list
    assert "http://localhost:3000" in origins


def test_an_explicit_list_wins_over_the_derived_default() -> None:
    origins = _settings(
        frontend_base_url="http://localhost:3000",
        cors_allowed_origins="https://app.example.com, https://admin.example.com",
    ).cors_allowed_origins_list

    assert origins == ["https://app.example.com", "https://admin.example.com"]
    assert "http://localhost:3000" not in origins, "Produktion darf nicht implizit localhost erlauben"


def test_trailing_slashes_are_stripped() -> None:
    """A browser's Origin header never carries a trailing slash, so an
    entry that has one would silently never match."""
    origins = _settings(cors_allowed_origins="https://app.example.com/").cors_allowed_origins_list
    assert origins == ["https://app.example.com"]


def test_a_non_local_frontend_url_gains_no_extra_origins() -> None:
    origins = _settings(frontend_base_url="https://app.example.com").cors_allowed_origins_list
    assert origins == ["https://app.example.com"]
