"""Phase 14 part 2: Langfuse tracing facade.

Offline by design: without ``langfuse`` keys nothing touches the SDK, and the
mock/offline provider path is untouched. Covers the gating, the no-op provider
wrap, and the no-op execution trace used by ``run_agent``.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.agents.llm import MockLLMProvider
from app.config.settings import get_settings
from app.monitoring import langfuse as trace_mod


@pytest.fixture(autouse=True)
def _no_langfuse_keys() -> None:
    """Force the default (no keys) settings so traces stay no-ops."""
    settings = get_settings()
    original = (settings.langfuse_public_key, settings.langfuse_secret_key)
    settings.langfuse_public_key = ""
    settings.langfuse_secret_key = ""
    trace_mod._IMPORTABLE = None
    trace_mod._CLIENT = None
    yield
    settings.langfuse_public_key, settings.langfuse_secret_key = original
    trace_mod._IMPORTABLE = None
    trace_mod._CLIENT = None


def test_tracer_not_configured_without_keys() -> None:
    assert trace_mod.tracer_configured() is False


def test_maybe_wrap_provider_returns_untouched_offline() -> None:
    provider = MockLLMProvider()
    wrapped = trace_mod.maybe_wrap_provider(provider)
    assert wrapped is provider


def test_execution_trace_yields_none_offline() -> None:
    async def _probe() -> None:
        async with trace_mod.execution_trace(
            "security", review_id=str(uuid.uuid4())
        ) as t:
            assert t is None

    import asyncio

    asyncio.run(_probe())


def test_run_agent_with_mock_provider_still_works(sample_scope: Any) -> None:
    from app.agents.runner import run_agent

    async def _probe() -> None:
        provider = MockLLMProvider(
            [
                {
                    "content": '{"findings": []}',
                    "model": "mock-model",
                    "usage": {"prompt_tokens": 9, "completion_tokens": 5},
                }
            ]
        )
        result = await run_agent("security", sample_scope, provider=provider)
        assert result.findings == []

    import asyncio

    asyncio.run(_probe())


def test_traced_provider_type_uses_cache_before_config() -> None:
    # Without keys the client cache stays empty and the wrapper is never built.
    assert trace_mod._client() is None  # type: ignore[attr-defined]
    provider = MockLLMProvider()
    wrapped = trace_mod.maybe_wrap_provider(provider)
    assert wrapped is provider
