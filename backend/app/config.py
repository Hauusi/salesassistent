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
    # How many messages one poll cycle pulls per mailbox. Each costs a
    # Gmail round-trip plus a Claude call plus an embedding, sequentially,
    # so this bounds how long a single job can run.
    mail_poll_batch_size: int = 25
    mail_poll_job_timeout_seconds: int = 300

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

    # --- Newsletter pre-filter (app/services/newsletter_prefilter.py) ---
    # Vocabulary-based signals, overridable per deployment because a
    # hardcoded list silently limits the system to the language it was
    # written in. Comma-separated in the environment.
    #
    # Local-parts used essentially only by bulk-send systems/ESPs, never by
    # a human composing a genuine business mail. Deliberately excludes
    # no-reply/noreply/donotreply: those are just as common for
    # transactional mail (order confirmations, shipping notices) that must
    # NOT be swept into 'newsletter'.
    newsletter_bulk_local_parts: str = (
        "newsletter,newsletters,newsletter-noreply,marketing,mailer,mailing,campaign,"
        "campaigns,bulkmail,bulk,news,nl,mailings,broadcast"
    )
    # Footer boilerplate found in essentially every marketing send. Used
    # only as a secondary signal, never on its own.
    newsletter_boilerplate_terms: str = (
        "abmelden,abbestellen,newsletter abbestellen,vom newsletter,unsubscribe,"
        "opt out,opt-out,manage preferences,email preferences,im browser ansehen,"
        "im browser anzeigen,view in browser,view this email,sie erhalten diese e-mail,"
        "sie erhalten diese email,sie erhalten diese nachricht,you are receiving this"
    )
    # The safety guard: anything matching means a human may need to act, so
    # the mail is never auto-filed as newsletter regardless of bulk signals.
    # Covers genuine business vocabulary and phishing/urgency patterns, in
    # German and English - a language gap here means a real inquiry gets
    # quietly filed away, which is the expensive direction of the trade.
    newsletter_human_review_terms: str = (
        "anfrage,angebot,angebotsanfrage,bestellung,bestellen,auftrag,rechnung,"
        "lieferzeit,liefertermin,reklamation,storno,stornierung,kündigung,kuendigung,"
        "mahnung,dringend,eilt,frist,"
        "inquiry,enquiry,quotation,quote,purchase order,order confirmation,invoice,"
        "delivery date,lead time,complaint,cancellation,urgent,asap,deadline,"
        "passwort,password,kennwort,login,anmeldedaten,credentials,verify your,"
        "verify account,konto gesperrt,account suspended,kreditkarte,credit card,"
        "zahlungsdaten,payment details"
    )

    # --- Product search ---
    # Postgres text-search configuration used to stem and stop-word both
    # the catalog text and the inquiry (see app/services/product_search.py).
    # Must name a configuration this database actually has - validated at
    # startup. Set to the language your catalog and your customers write in.
    product_search_text_config: str = "german"

    # --- Single-tenant MVP bootstrap ---
    default_tenant_slug: str = "default"


    # --- Derived views on the comma-separated settings above -------------
    #
    # Parsed once per Settings instance (which is itself cached), so callers
    # get a ready-made set/list instead of re-splitting a string on every
    # mail.

    @property
    def newsletter_bulk_local_parts_set(self) -> frozenset[str]:
        return frozenset(_split_terms(self.newsletter_bulk_local_parts))

    @property
    def newsletter_boilerplate_terms_list(self) -> list[str]:
        return _split_terms(self.newsletter_boilerplate_terms)

    @property
    def newsletter_human_review_terms_list(self) -> list[str]:
        return _split_terms(self.newsletter_human_review_terms)


def _split_terms(raw: str) -> list[str]:
    """Splits a comma-separated setting into normalised terms."""
    return [term.strip().lower() for term in raw.split(",") if term.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
