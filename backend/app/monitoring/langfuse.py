"""Langfuse LLM/agent tracing (Phase 14 part 2, docs/OBSERVABILITY.md).

Every entry point is strictly gated on configuration: without
``ADAPTIVE_LANGFUSE_PUBLIC_KEY`` and a host, or without the ``langfuse`` SDK
installed, the module degenerates to a no-op that keeps the offline/mock
provider surface free of any SDK/network coupling.

Two seams:

- ``maybe_wrap_provider`` decorates the production LLM provider built by
  ``app.agents.llm.build_llm_provider`` with a span-per-call tracer that reports
  model, token usage, cost, and latency.
- ``execution_trace`` wraps ``app.agents.runner.run_agent`` so every agent
  execution (in-process or Celery) is one Langfuse trace tagged by agent key and
  review id.
"""

from __future__ import annotations

import contextlib
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any, cast

from app.agents.llm import LLMProvider, LLMResult
from app.config.settings import settings

_IMPORTABLE: bool | None = None
_CLIENT: Any = None


def _import_available() -> bool:
    global _IMPORTABLE
    if _IMPORTABLE is None:
        try:
            import langfuse  # noqa: F401
        except ImportError:
            _IMPORTABLE = False
        else:
            _IMPORTABLE = True
    return _IMPORTABLE


def tracer_configured() -> bool:
    """True when the SDK is installed and a Langfuse public key + host exist."""
    if not settings.langfuse_public_key:
        return False
    return _import_available()


def _client() -> Any:
    """Lazily build (and reuse) the SDK client only when fully configured."""
    global _CLIENT
    if _CLIENT is not None:
        return _CLIENT
    if not tracer_configured():
        return None
    from langfuse import Langfuse

    _CLIENT = Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        host=settings.langfuse_host or "https://cloud.langfuse.com",
    )
    return _CLIENT


class TracedLLMProvider:
    """Wraps an :class:`LLMProvider`, emitting one Langfuse span per completion."""

    name: str

    def __init__(
        self, provider: LLMProvider, *, trace_tags: tuple[str, ...] = ()
    ) -> None:
        self._provider = provider
        self.name = f"traced({provider.name})"
        self._trace_tags = trace_tags

    async def complete(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int | None = None,
    ) -> LLMResult:
        client = _client()
        started = time.perf_counter()
        span: Any = None
        if client is not None:
            span = client.start_span(
                name="llm_call",
                input={"system": system_prompt, "user": user_prompt},
                metadata={
                    "provider": getattr(self._provider, "name", "provider"),
                    "model": getattr(self._provider, "model", None),
                },
                tags=list(self._trace_tags),
            )
        try:
            result = await self._provider.complete(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=max_tokens,
            )
        except BaseException:
            if span is not None:
                try:
                    span.update(
                        metadata={
                            "error": True,
                            "latency_ms": round(
                                (time.perf_counter() - started) * 1000.0, 1
                            ),
                        }
                    )
                    span.end()
                except Exception:  # pragma: no cover - never mask the LLM error
                    pass
            raise
        if span is not None:
            span.update(
                output={"content": result.content},
                usage={
                    "input": result.usage.prompt_tokens,
                    "output": result.usage.completion_tokens,
                    "total": result.usage.total,
                },
                metadata={
                    "cost_usd": result.cost_usd,
                    "latency_ms": round((time.perf_counter() - started) * 1000.0, 1),
                },
            )
            span.end()
        return result


def maybe_wrap_provider(provider: LLMProvider) -> LLMProvider:
    """Wrap ``provider`` for Langfuse spans when tracing is configured."""
    if not tracer_configured():
        return provider
    return cast(LLMProvider, TracedLLMProvider(provider))


@contextlib.asynccontextmanager
async def execution_trace(
    agent_key: str, *, review_id: str | None = None, **extra: Any
) -> AsyncIterator[Any]:
    """One Langfuse trace per agent execution; no-op when not configured."""
    if not tracer_configured():
        yield None
        return
    client = _client()
    trace = client.start_trace(
        name="agent_execution",
        trace_id=f"agent-{uuid.uuid4().hex}",
        tags=[tag for tag in (agent_key, review_id) if tag],
        metadata={"agent_key": agent_key, "review_id": review_id, **extra},
    )
    try:
        yield trace
    finally:
        trace.end()
