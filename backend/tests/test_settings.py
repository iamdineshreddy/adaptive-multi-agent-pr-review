"""Settings behaviour tests."""

from __future__ import annotations

import pytest

from app.config.settings import Settings


def test_settings_defaults() -> None:
    settings = Settings(_env_file=None)
    assert settings.env == "development"
    assert settings.review_budget_high == 20
    assert settings.llm_max_retries == 3


def test_settings_read_env_prefix() -> None:
    settings = Settings(_env_file=None, env="test", redis_url="redis://x:1/2")
    assert settings.env == "test"
    assert settings.redis_url == "redis://x:1/2"


def test_settings_reject_missing_production_secrets() -> None:
    with pytest.raises(ValueError):
        Settings(_env_file=None, env="production")


def test_settings_production_requires_non_local_db() -> None:
    with pytest.raises(ValueError):
        Settings(
            _env_file=None,
            env="production",
            secret_key="s",
            github_webhook_secret="w",
            database_url="postgresql+asyncpg://postgres:postgres@localhost:5432/adaptive_review",
        )
