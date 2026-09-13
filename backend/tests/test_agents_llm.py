"""Unit tests for the LLM provider abstraction (docs/AGENTS.md §7)."""

from __future__ import annotations

import pytest

from app.agents.llm import (
    LLMConfigurationError,
    LLMError,
    LLMProvider,
    LLMResult,
    LLMRetryableError,
    MockLLMProvider,
    ResilientLLMProvider,
    TokenUsage,
    build_llm_provider,
    estimate_cost,
)
from app.config.settings import Settings


class FakeProvider:
    """Deterministic async provider for exercising the retry/fallback logic."""

    def __init__(
        self,
        name: str,
        *,
        failures: int = 0,
        failure_type: type[LLMError] = LLMRetryableError,
    ) -> None:
        self.name = name
        self._failures = failures
        self._failure_type = failure_type
        self.attempts = 0

    async def complete(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int | None = None,
    ) -> LLMResult:
        self.attempts += 1
        if self.attempts <= self._failures:
            raise self._failure_type(f"{self.name} error on attempt {self.attempts}")
        return LLMResult(
            content='{"findings": []}',
            model="fake",
            provider=self.name,
            usage=TokenUsage(10, 20),
            cost_usd=0.001,
        )


class TestCostEstimate:
    def test_known_model(self) -> None:
        assert estimate_cost("gpt-4o-mini", TokenUsage(1_000_000, 0)) == pytest.approx(
            0.15
        )

    def test_prefix_match(self) -> None:
        assert estimate_cost("gpt-4o-mini-2024-07-18", TokenUsage(0, 1_000_000)) == (
            pytest.approx(0.60)
        )

    def test_unknown_model_costs_nothing(self) -> None:
        assert estimate_cost("llama-x", TokenUsage(10, 20)) == 0.0


class TestMockLLMProvider:
    async def test_returns_queued_content_and_usage(self) -> None:
        provider = MockLLMProvider(
            [
                {
                    "content": '{"findings": []}',
                    "usage": {"prompt_tokens": 5, "completion_tokens": 7},
                }
            ]
        )
        result = await provider.complete(system_prompt="sys", user_prompt="user")
        assert result.content == '{"findings": []}'
        assert result.usage.total == 12
        assert len(provider.calls) == 1

    async def test_exhausted_queue_raises(self) -> None:
        provider = MockLLMProvider()
        with pytest.raises(LLMError):
            await provider.complete(system_prompt="s", user_prompt="u")

    async def test_queued_exception_is_raised(self) -> None:
        provider = MockLLMProvider([LLMRetryableError("boom")])
        with pytest.raises(LLMRetryableError):
            await provider.complete(system_prompt="s", user_prompt="u")


class TestResilientLLMProvider:
    async def test_retries_then_succeeds(self) -> None:
        primary = FakeProvider("primary", failures=2)
        resilient: LLMProvider = ResilientLLMProvider(
            primary, max_retries=3, base_backoff_seconds=0.01, jitter=0.0
        )
        result = await resilient.complete(system_prompt="s", user_prompt="u")
        assert result.provider == "primary"
        assert primary.attempts == 3

    async def test_fatal_error_not_retried(self) -> None:
        primary = FakeProvider("primary", failures=9, failure_type=LLMError)
        resilient: LLMProvider = ResilientLLMProvider(
            primary, max_retries=3, base_backoff_seconds=0.01, jitter=0.0
        )
        with pytest.raises(LLMError):
            await resilient.complete(system_prompt="s", user_prompt="u")
        assert primary.attempts == 1

    async def test_fallback_used_after_retries_exhausted(self) -> None:
        primary = FakeProvider("primary", failures=99)
        fallback = FakeProvider("fallback")
        resilient = ResilientLLMProvider(
            primary,
            fallback=fallback,
            max_retries=1,
            base_backoff_seconds=0.01,
            jitter=0.0,
        )
        result = await resilient.complete(system_prompt="s", user_prompt="u")
        assert result.provider == "fallback"
        assert primary.attempts == 2
        assert fallback.attempts == 1

    async def test_exhaustion_raises(self) -> None:
        primary = FakeProvider("primary", failures=99)
        resilient: LLMProvider = ResilientLLMProvider(
            primary, max_retries=2, base_backoff_seconds=0.01, jitter=0.0
        )
        with pytest.raises(LLMRetryableError):
            await resilient.complete(system_prompt="s", user_prompt="u")

    def test_invalid_arguments_rejected(self) -> None:
        with pytest.raises(ValueError):
            ResilientLLMProvider(FakeProvider("p"), max_retries=-1)
        with pytest.raises(ValueError):
            ResilientLLMProvider(FakeProvider("p"), jitter=1.5)


class TestBuildProvider:
    def test_mock_mode(self) -> None:
        provider = build_llm_provider(Settings(llm_provider="mock"))
        assert isinstance(provider, MockLLMProvider)

    def test_openai_without_key_raises_configuration_error(self) -> None:
        with pytest.raises(LLMConfigurationError):
            build_llm_provider(Settings(llm_provider="openai", openai_api_key=""))

    def test_unsupported_provider_rejected(self) -> None:
        with pytest.raises(LLMConfigurationError):
            build_llm_provider(Settings(llm_provider="gemini"))
