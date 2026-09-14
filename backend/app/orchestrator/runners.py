"""Agent execution boundaries for the orchestrator (docs/AGENTS.md §2-3).

The LangGraph ``AGENT_FAN_OUT`` node calls an ``AgentRunner`` for each planned
agent. Two implementations exist:

- ``InProcessAgentRunner`` — deterministic, broker-free; used by unit tests and
  local dev (the Phase 6 default for the async core).
- ``CeleryFanOutRunner`` — production fan-out: one ``agents.run_agent`` task per
  agent routed to its dedicated queue (docs/QUEUE.md §1), joined via the result
  backend.

Runners never leak provider internals: every outcome is flattened into a
:class:`RunnerResult` with an explicit ``retryable`` flag so the graph can apply
its bounded retry policy (docs/AGENTS.md §2).
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from celery.exceptions import TimeoutError as CeleryTimeoutError

from app.agents.contract import (
    AgentOutputError,
    AgentScope,
    AgentScopeConfigurationError,
)
from app.agents.llm import LLMError, LLMProvider, LLMRetryableError
from app.agents.registry import agent_keys, queue_for_agent
from app.agents.runner import run_agent
from app.queue.celery_app import celery_app

_AGENT_TASK = "agents.run_agent"


@dataclass(frozen=True)
class RunnerResult:
    """Normalised outcome of running one agent, independent of the runner impl."""

    agent_key: str
    success: bool
    retryable: bool = False
    findings: tuple[dict[str, Any], ...] = ()
    stats: dict[str, Any] | None = None
    error: str | None = None


class AgentRunner(Protocol):
    """Boundary the graph uses to execute planned agents."""

    async def agent_keys(self) -> Sequence[str]: ...
    async def run(self, agent_key: str, scope: AgentScope) -> RunnerResult: ...


class TargetedAgentRunner:
    """Iteration boundary adapter (FR-6.2): restricts the planned agent set.

    Wraps any :class:`AgentRunner` and exposes only ``keys`` via
    ``agent_keys()``. Running an out-of-set key is a loud contract error, not a
    silent pass — the graph must never exceed the planned target set.
    """

    name = "targeted"

    def __init__(self, runner: AgentRunner, keys: Sequence[str]) -> None:
        self._runner = runner
        self._keys = tuple(keys)

    @property
    def planned_keys(self) -> tuple[str, ...]:
        return self._keys

    async def agent_keys(self) -> Sequence[str]:
        return self._keys

    async def run(self, agent_key: str, scope: AgentScope) -> RunnerResult:
        if agent_key not in self._keys:
            return RunnerResult(
                agent_key,
                success=False,
                retryable=False,
                error=(
                    f"agent '{agent_key}' is outside the iteration target set "
                    f"{', '.join(self._keys) or '(empty)'}"
                ),
            )
        return await self._runner.run(agent_key, scope)


class InProcessAgentRunner:
    """Runs agents in-process through ``app.agents.runner.run_agent``.

    ``providers`` maps agent_key -> provider override (tests inject
    ``MockLLMProvider`` here); missing keys use the configured production
    provider.
    """

    name = "in-process"

    def __init__(self, providers: Mapping[str, LLMProvider] | None = None) -> None:
        self._providers = dict(providers or {})

    async def agent_keys(self) -> Sequence[str]:
        return tuple(agent_keys())

    async def run(self, agent_key: str, scope: AgentScope) -> RunnerResult:
        try:
            result = await run_agent(
                agent_key,
                scope,
                provider=self._providers.get(agent_key),
            )
        except Exception as exc:  # noqa: BLE001 - classification is explicit
            return self._classify(exc, agent_key)
        return RunnerResult(
            agent_key,
            success=True,
            findings=tuple(f.model_dump(mode="json") for f in result.findings),
            stats=result.stats.to_dict(),
        )

    @staticmethod
    def _classify(exc: Exception, agent_key: str | None = None) -> RunnerResult:
        """Map one agent exception onto the graph's retry/permanent contract."""
        key = agent_key or "?"
        if isinstance(exc, LLMRetryableError):
            return RunnerResult(key, success=False, retryable=True, error=str(exc))
        if isinstance(exc, LLMError):
            return RunnerResult(key, success=False, retryable=False, error=str(exc))
        if isinstance(exc, AgentOutputError):
            return RunnerResult(
                key,
                success=False,
                retryable=False,
                error=f"unparseable output after one repair attempt: {exc}",
            )
        if isinstance(exc, AgentScopeConfigurationError):
            return RunnerResult(key, success=False, retryable=False, error=str(exc))
        return RunnerResult(
            key,
            success=False,
            retryable=False,
            error=f"unexpected agent failure: {exc}",
        )


class CeleryFanOutRunner:
    """Production fan-out: dispatches and joins ``agents.run_agent`` tasks.

    Each planned agent is handed to its dedicated worker queue
    (docs/QUEUE.md §1) and the result is joined through the broker result
    backend. Live only — requires a reachable broker; unit tests use
    :class:`InProcessAgentRunner`. Classified outcomes let the graph decide
    retry vs. permanent failure without trusting the wire format.
    """

    name = "celery-fanout"

    def __init__(
        self,
        *,
        task_name: str = _AGENT_TASK,
        join_timeout_seconds: float = 600.0,
        poll_interval_seconds: float = 0.5,
    ) -> None:
        self._task_name = task_name
        self._join_timeout = join_timeout_seconds
        self._poll_interval = poll_interval_seconds

    async def agent_keys(self) -> Sequence[str]:
        return tuple(agent_keys())

    async def run(self, agent_key: str, scope: AgentScope) -> RunnerResult:
        queue = queue_for_agent(agent_key)
        task = await asyncio.to_thread(
            celery_app.send_task,
            self._task_name,
            args=[str(scope.review_id), agent_key, scope.to_dict()],
            queue=queue,
        )
        try:
            payload = await asyncio.to_thread(
                task.get,
                timeout=self._join_timeout,
                interval=self._poll_interval,
                propagate=True,
            )
        except CeleryTimeoutError:
            return RunnerResult(
                agent_key,
                success=False,
                retryable=True,
                error=f"fan-out join timed out after {self._join_timeout}s",
            )
        except Exception as exc:  # noqa: BLE001 - task failure surfaces for the graph
            return RunnerResult(
                agent_key,
                success=False,
                retryable=False,
                error=f"agent task failed on broker: {exc}",
            )
        return _payload_to_result(agent_key, payload or {})


def _payload_to_result(agent_key: str, payload: Any) -> RunnerResult:
    if not isinstance(payload, dict) or payload.get("agent_key") != agent_key:
        return RunnerResult(
            agent_key,
            success=False,
            retryable=False,
            error="agent task returned a malformed payload",
        )
    findings = payload.get("findings") or []
    stats = payload.get("stats")
    return RunnerResult(
        agent_key,
        success=True,
        findings=tuple(f for f in findings if isinstance(f, dict)),
        stats=stats if isinstance(stats, dict) else None,
    )
