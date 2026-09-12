"""Application configuration.

Settings are loaded from environment variables with the ``ADAPTIVE_`` prefix and
optionally a local ``.env`` file. No secrets are stored in this file.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the review platform."""

    model_config = SettingsConfigDict(
        env_prefix="ADAPTIVE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Environment -------------------------------------------------------
    env: str = "development"
    debug: bool = True

    # --- Security ----------------------------------------------------------
    secret_key: str = ""
    github_webhook_secret: str = ""
    api_token_hashes: list[str] = []  # salted hashes of API tokens

    # --- GitHub ------------------------------------------------------------
    github_app_id: str = ""
    github_app_private_key: str = (
        ""  # PEM as single env value (secrets manager in prod)
    )
    github_pat: str = ""  # alternative PAT (fallback)
    github_webhook_url: str = "https://api.github.com"
    webhook_unknown_event_action: str = "reject"  # "reject" | "ignore"
    rate_limit_webhook_per_minute: int = 120
    rate_limit_webhook_repo_burst: int = 30

    # --- Infrastructure ----------------------------------------------------
    database_url: str = (
        "postgresql+asyncpg://postgres:postgres@localhost:5432/adaptive_review"
    )
    database_echo_logging: bool = False  # emit SQL statements to the log
    redis_url: str = "redis://localhost:6379/0"

    # --- LLM ---------------------------------------------------------------
    llm_provider: str = "openai"
    llm_model_default: str = "gpt-4o-mini"
    llm_embedding_model: str = "text-embedding-3-small"
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    llm_max_retries: int = 3
    llm_timeout_seconds: float = 120.0

    # --- Adaptive Review Utility Model --------------------------------------
    arum_weights_version: str = "v1"
    arum_temporal_decay_days: int = 90
    arum_decay_lambda: float = 1.0

    # --- Review budget (configurable caps) ----------------------------------
    review_budget_low: int = 5
    review_budget_medium: int = 10
    review_budget_high: int = 20

    # --- Priority scoring (docs/QUEUE.md §2) ---------------------------------
    priority_scale: float = 10.0  # score = scale * weighted-factor-sum (0..scale)
    priority_w_security: float = 0.30
    priority_w_impact: float = 0.25
    priority_w_history: float = 0.15
    priority_w_component: float = 0.15
    priority_w_dependency: float = 0.10
    priority_w_urgency: float = 0.05
    risk_low_threshold: float = 3.0  # score < low  -> risk LOW
    risk_medium_threshold: float = 6.0  # score <= med -> risk MEDIUM, else HIGH

    # --- Queue -------------------------------------------------------------
    celery_max_retries: int = 4
    celery_retry_backoff_seconds: int = 30
    celery_priority_redis_key: str = "adaptive_review:priority"
    celery_dead_letter_redis_key: str = "adaptive_review:dead_letter"
    queue_dispatch_provider: str = "celery"  # "celery" | "logging"

    # --- Observability -------------------------------------------------------
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = ""
    prometheus_enabled: bool = True

    @model_validator(mode="after")
    def enforce_production_secrets(self) -> Settings:
        """Fail fast in production when required secrets are missing."""
        if self.env != "production":
            return self
        missing = [
            name
            for name in ("secret_key", "github_webhook_secret")
            if not getattr(self, name)
        ]
        if missing:
            raise ValueError(
                f"Missing required production settings: {', '.join(missing)}"
            )
        if "localhost" in self.database_url:
            raise ValueError("DATABASE_URL must not target localhost in production")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings singleton."""
    return Settings()


settings = get_settings()
