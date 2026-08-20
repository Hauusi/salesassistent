"""Central application configuration.

All values are sourced from environment variables (see ``.env.example``).
Nothing sensitive is hard-coded here.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- App ---
    app_env: str = "development"
    app_secret_key: str = "change-me-in-env"
    api_base_url: str = "http://localhost:8000"
    frontend_base_url: str = "http://localhost:3000"

    # --- Database ---
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/salesassistent"

    # --- Redis / Queue ---
    redis_url: str = "redis://localhost:6379/0"
    mail_poll_interval_seconds: int = 60

    # --- Encryption (Fernet key, base64, 32 bytes) for OAuth tokens at rest ---
    token_encryption_key: str = ""

    # --- Google OAuth / Gmail API ---
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/api/auth/gmail/callback"
    # Scopes: readonly to fetch mail, send to dispatch approved drafts,
    # modify to apply/query labels (e.g. spam/newsletter housekeeping).
    google_oauth_scopes: str = (
        "openid "
        "https://www.googleapis.com/auth/userinfo.email "
        "https://www.googleapis.com/auth/gmail.readonly "
        "https://www.googleapis.com/auth/gmail.send "
        "https://www.googleapis.com/auth/gmail.modify"
    )

    # --- Anthropic (classification, sentiment, draft generation) ---
    anthropic_api_key: str = ""
    # Draft generation needs Sonnet's language/context quality - it writes
    # the actual customer-facing reply text.
    anthropic_model: str = "claude-sonnet-5"
    # Classification is a fixed-enum categorization task (2 axes, forced
    # tool use) - Haiku is materially cheaper and accurate enough for it,
    # see app/services/classification.py. Kept as its own setting so it can
    # be tuned/rolled back independently of the draft-generation model.
    anthropic_classification_model: str = "claude-haiku-4-5"

    # --- LLM call resilience ---
    # Rate limits (429) and overloaded/5xx responses are routine at load,
    # not exceptional, and an unhandled one used to abort a whole poll
    # batch. Retried with exponential backoff in app/services/llm_client.py;
    # permanent errors (auth, malformed request) are never retried.
    llm_max_attempts: int = 4
    llm_retry_min_seconds: float = 2.0
    llm_retry_max_seconds: float = 30.0
    llm_retry_backoff_multiplier: float = 2.0

    # --- Voyage AI (embeddings for case/RAG matching) ---
    voyage_api_key: str = ""
    voyage_embedding_model: str = "voyage-3.5"
    embedding_dimensions: int = 1024

    # --- Case matching heuristics (MVP defaults, tune via env) ---
    # Cosine similarity above which a new mail is attached to an existing
    # case instead of spawning a new one. No canonical value is given in
    # the concept doc for this MVP, so this is a documented, configurable
    # default rather than a guess baked into the code.
    case_similarity_threshold: float = 0.78
    # Contacts are considered when the newest matching mail in the case is
    # not older than this, to avoid tying a mail to a long-dead thread.
    case_lookback_days: int = 180

    # --- Single-tenant MVP bootstrap ---
    default_tenant_slug: str = "default"


@lru_cache
def get_settings() -> Settings:
    return Settings()
