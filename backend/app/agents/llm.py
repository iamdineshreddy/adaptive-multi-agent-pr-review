"""LLM provider abstraction (docs/AGENTS.md §7).

Every LLM call goes through ``LLMProvider``. Providers are injected (never
instantiated from module state), so agent code is fully testable offline and the
real provider SDKs are only touched when a key is present and a call is made.

Failure handling mirrors the documented policy:
- ``LLMRetryableError`` (timeout, 5xx, 429) → retried with bounded backoff by
  ``ResilientLLMProvider``, then a fallback provider is attempted.
- ``LLMError`` (auth/configuration/refusal) → fatal, no retry.
- Structured-output parse failures are *not* a provider concern; they surface in
  ``app.agents.contract`` and are repaired once by the agent.

Token/cost accounting is always returned to the caller; nothing here writes to
logs containing secrets.
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from typing import Any, Protocol

from app.config.settings import Settings

# Model name prefix -> (input USD per 1M tokens, output USD per 1M tokens).
# Only used for cost accounting; unknown models cost $0.0 and are logged by name.
MODEL_PRICING: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1": (2.00, 8.00),
    "gpt-3.5-turbo": (0.50, 1.50),
    "claude-3-5-sonnet": (3.00, 15.00),
    "claude-3-5-haiku": (1.00, 5.00),
    "claude-3-opus": (15.00, 75.00),
    "claude-3-haiku": (0.25, 1.25),
}


@dataclass(frozen=True)
class TokenUsage:
    """Token accounting for a single LLM call."""

    prompt_tokens: int
    completion_tokens: int

    @property
    def total(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass(frozen=True)
class LLMResult:
    """A completed, structured-output LLM call."""

    content: str
    model: str
    provider: str
    usage: TokenUsage
    cost_usd: float


class LLMError(Exception):
    """Fatal provider error (configuration, auth, refusal) — no retry."""


class LLMConfigurationError(LLMError):
    """The requested provider/model/key combination cannot be built."""


class LLMRetryableError(LLMError):
    """Transient provider error (timeout, 429, 5xx) — retry with backoff."""


class LLMProvider(Protocol):
    """A callable model backend; ``name`` is used in metrics/logs."""

    name: str

    async def complete(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int | None = None,
    ) -> LLMResult: ...


def estimate_cost(model: str, usage: TokenUsage) -> float:
    """Compute USD cost from the pricing table; unknown models cost $0."""
    for prefix, (in_rate, out_rate) in sorted(
        MODEL_PRICING.items(), key=lambda kv: len(kv[0]), reverse=True
    ):
        if model.lower().startswith(prefix):
            return round(
                usage.prompt_tokens / 1_000_000 * in_rate
                + usage.completion_tokens / 1_000_000 * out_rate,
                8,
            )
    return 0.0


def _as_int(value: object, default: int) -> int:
    """Cast a mock-usage value to an int, tolerating None/bools."""
    if isinstance(value, bool) or not isinstance(value, (int, str, float)):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class OpenAIProvider:
    """OpenAI chat-completions provider with JSON-object output mode."""

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        base_url: str | None = None,
        timeout_seconds: float = 120.0,
    ) -> None:
        if not api_key:
            raise LLMConfigurationError("OpenAI provider requires an API key")
        self.model = model
        self.name = f"openai/{model}"
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_seconds,
        )
        self._timeout_seconds = timeout_seconds

    async def complete(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int | None = None,
    ) -> LLMResult:
        from openai import (
            APIConnectionError,
            APITimeoutError,
            AuthenticationError,
            BadRequestError,
            InternalServerError,
            NotFoundError,
            PermissionDeniedError,
            RateLimitError,
            UnprocessableEntityError,
        )

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {"type": "json_object"},
            "timeout": self._timeout_seconds,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        try:
            response = await self._client.chat.completions.create(**kwargs)
        except (
            APIConnectionError,
            APITimeoutError,
            InternalServerError,
            RateLimitError,
        ) as exc:
            raise LLMRetryableError(f"openai transient error: {exc}") from exc
        except (
            AuthenticationError,
            PermissionDeniedError,
            BadRequestError,
            NotFoundError,
            UnprocessableEntityError,
        ) as exc:
            raise LLMError(f"openai rejected the request: {exc}") from exc
        return self._to_result(response)

    def _to_result(self, response: Any) -> LLMResult:
        content = response.choices[0].message.content or ""
        usage = TokenUsage(
            prompt_tokens=int(response.usage.prompt_tokens or 0),
            completion_tokens=int(response.usage.completion_tokens or 0),
        )
        return LLMResult(
            content=content,
            model=self.model,
            provider=self.name,
            usage=usage,
            cost_usd=estimate_cost(self.model, usage),
        )

    async def aclose(self) -> None:
        await self._client.close()


class AnthropicProvider:
    """Anthropic messages provider (JSON output requested in the prompt)."""

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        max_tokens: int = 2048,
        timeout_seconds: float = 120.0,
    ) -> None:
        if not api_key:
            raise LLMConfigurationError("Anthropic provider requires an API key")
        self.model = model
        self.name = f"anthropic/{model}"
        from anthropic import AsyncAnthropic

        self._client = AsyncAnthropic(api_key=api_key, timeout=timeout_seconds)
        self._max_tokens = max_tokens
        self._timeout_seconds = timeout_seconds

    async def complete(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int | None = None,
    ) -> LLMResult:
        from anthropic import (
            APIError,
            APIStatusError,
            APITimeoutError,
            AuthenticationError,
            BadRequestError,
            InternalServerError,
            NotFoundError,
            RateLimitError,
        )

        cap = max_tokens or self._max_tokens
        try:
            message = await self._client.messages.create(
                model=self.model,
                max_tokens=cap,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
        except (
            APITimeoutError,
            RateLimitError,
            InternalServerError,
            APIStatusError,
        ) as exc:
            raise LLMRetryableError(f"anthropic transient error: {exc}") from exc
        except (
            AuthenticationError,
            BadRequestError,
            NotFoundError,
            APIError,
        ) as exc:
            raise LLMError(f"anthropic rejected the request: {exc}") from exc
        return self._to_result(message)

    def _to_result(self, message: Any) -> LLMResult:
        content = "".join(
            block.text
            for block in message.content
            if getattr(block, "type", "") == "text"
        )
        usage = TokenUsage(
            prompt_tokens=int(getattr(message.usage, "input_tokens", 0) or 0),
            completion_tokens=int(getattr(message.usage, "output_tokens", 0) or 0),
        )
        return LLMResult(
            content=content,
            model=self.model,
            provider=self.name,
            usage=usage,
            cost_usd=estimate_cost(self.model, usage),
        )

    async def aclose(self) -> None:
        await self._client.close()


class MockLLMProvider:
    """Deterministic, offline provider used by tests and the ``mock`` mode.

    ``responses`` is consumed in order: each entry is an LLMResult-compatible dict
    ``{"content": ..., "model": ..., "usage": {..., ...}}``, a plain string (content)
    or an exception instance to raise.
    """

    name = "mock/unit"

    def __init__(self, responses: list[object] | None = None) -> None:
        self._responses: list[object] = list(responses or [])
        self.calls: list[dict[str, object]] = []

    def queue(self, response: object) -> None:
        self._responses.append(response)

    async def complete(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int | None = None,
    ) -> LLMResult:
        self.calls.append({"system_prompt": system_prompt, "user_prompt": user_prompt})
        if not self._responses:
            raise LLMError("MockLLMProvider ran out of queued responses")
        response = self._responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        if isinstance(response, str):
            payload: dict[str, object] = {"content": response}
        elif isinstance(response, dict):
            payload = dict(response)
        else:
            raise LLMError(
                "MockLLMProvider got unsupported response type "
                f"{type(response).__name__}"
            )
        usage_raw = payload.get("usage")
        usage_dict = usage_raw if isinstance(usage_raw, dict) else {}
        usage = TokenUsage(
            prompt_tokens=_as_int(usage_dict.get("prompt_tokens"), 10),
            completion_tokens=_as_int(usage_dict.get("completion_tokens"), 20),
        )
        cost = payload.get("cost_usd")
        cost_usd = (
            float(cost)
            if isinstance(cost, (int, float)) and not isinstance(cost, bool)
            else estimate_cost("mock-model", usage)
        )
        return LLMResult(
            content=str(payload.get("content", "")),
            model=str(payload.get("model", "mock-model")),
            provider=str(payload.get("provider", self.name)),
            usage=usage,
            cost_usd=cost_usd,
        )

    async def aclose(self) -> None:
        return None


class ResilientLLMProvider:
    """Wraps a primary provider with bounded retries and an optional fallback.

    Retry policy: ``LLMRetryableError`` backs off exponentially with jitter up to
    ``max_retries`` attempts; the fallback provider then gets a single attempt.
    Fatal ``LLMError`` is never retried.
    """

    def __init__(
        self,
        primary: LLMProvider,
        *,
        fallback: LLMProvider | None = None,
        max_retries: int = 3,
        base_backoff_seconds: float = 2.0,
        max_backoff_seconds: float = 30.0,
        jitter: float = 0.2,
        rng: random.Random | None = None,
    ) -> None:
        self._primary = primary
        self._fallback = fallback
        self.name = f"resilient({primary.name})"
        if max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        if not 0.0 <= jitter < 1.0:
            raise ValueError("jitter must be in [0, 1)")
        self._max_retries = max_retries
        self._base_backoff = base_backoff_seconds
        self._max_backoff = max_backoff_seconds
        self._jitter = jitter
        self._rng = rng or random.Random()

    async def complete(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int | None = None,
    ) -> LLMResult:
        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                return await self._primary.complete(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    max_tokens=max_tokens,
                )
            except LLMRetryableError as exc:
                last_error = exc
                if attempt < self._max_retries:
                    await asyncio.sleep(self._delay(attempt))
            except LLMError:
                raise

        if self._fallback is not None:
            try:
                return await self._fallback.complete(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    max_tokens=max_tokens,
                )
            except LLMError as exc:
                raise LLMError(
                    f"primary and fallback providers both failed: {exc}"
                ) from exc

        if last_error is not None:
            raise LLMRetryableError(
                f"primary provider failed after {self._max_retries + 1} attempts"
            ) from last_error
        raise LLMRetryableError("primary provider failed")

    def _delay(self, attempt: int) -> float:
        exponent: float = self._base_backoff * float(2**attempt)
        capped: float = min(exponent, self._max_backoff)
        jitter_amount: float = capped * self._jitter
        scale: float = 2.0 * jitter_amount
        return capped - jitter_amount + self._rng.random() * scale


def build_llm_provider(settings: Settings) -> LLMProvider:
    """Build a resilient provider from application settings (docs/AGENTS.md §7).

    ``llm_provider = "mock"`` produces an offline provider (tests, local dev).
    Otherwise the configured primary is wrapped with a fallback when the other
    provider's key is also present.
    """
    provider_name = (settings.llm_provider or "mock").lower()
    if provider_name == "mock":
        return MockLLMProvider()

    primary: LLMProvider
    fallback: LLMProvider | None = None
    timeout = settings.llm_timeout_seconds

    if provider_name == "openai":
        primary = OpenAIProvider(
            settings.openai_api_key,
            settings.llm_model_default,
            timeout_seconds=timeout,
        )
        if settings.anthropic_api_key:
            fallback = AnthropicProvider(
                settings.anthropic_api_key,
                _claude_default_model(settings),
                timeout_seconds=timeout,
            )
    elif provider_name == "anthropic":
        primary = AnthropicProvider(
            settings.anthropic_api_key,
            _claude_default_model(settings),
            timeout_seconds=timeout,
        )
        if settings.openai_api_key:
            fallback = OpenAIProvider(
                settings.openai_api_key,
                settings.llm_model_default,
                timeout_seconds=timeout,
            )
    else:
        raise LLMConfigurationError(
            f"unsupported llm_provider '{provider_name}'; expected "
            "'openai', 'anthropic', or 'mock'"
        )

    return _traced(
        ResilientLLMProvider(
            primary,
            fallback=fallback,
            max_retries=settings.llm_max_retries,
        )
    )


def _traced(provider: LLMProvider) -> LLMProvider:
    """Decorate with Langfuse span tracing when configured (Phase 14 part 2).

    Kept as a lazy local import so importing ``app.agents.llm`` never touches
    the monitoring stack; the offline/mock path stays free of SDK coupling.
    """
    from app.monitoring.langfuse import maybe_wrap_provider

    return maybe_wrap_provider(provider)


def _claude_default_model(settings: Settings) -> str:
    model = settings.llm_model_default
    if model.startswith("claude-"):
        return model
    return "claude-3-5-sonnet-20241022"
