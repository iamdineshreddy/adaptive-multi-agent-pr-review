"""Orchestration service: run the LangGraph state machine and persist outcomes.

This is the broker-free core of Phase 6. It takes an ``AgentScope`` (already
built from DB/GitHub data), drives the graph, and records everything
(review status transitions, review_tasks, agent_metrics, supervisor notes,
root cause) through an injected :class:`OrchestratorStore`. Production entry
points (the ``orchestrator.run_review`` Celery task) wrap this core.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog

from app.agents.contract import AgentScope, scope_validation_error
from app.agents.registry import agent_metadata, queue_for_agent
from app.config.settings import Settings, get_settings
from app.models.enums import ReviewStatus, TaskStatus
from app.orchestrator.graph import build_orchestration_graph
from app.orchestrator.persistence import (
    MemoryOrchestratorStore,
    OrchestratorStore,
    TaskWrite,
    utc_now,
)
from app.orchestrator.runners import AgentRunner, InProcessAgentRunner
from app.orchestrator.state import TERMINAL_FAILED, empty_state

logger = structlog.get_logger(__name__)


@dataclass
class ReviewOutcome:
    """Serialisable result of one orchestration run."""

    review_id: str
    status: str  # ReviewStatus.value persisted on the review row
    agent_keys: tuple[str, ...]
    findings: list[dict[str, Any]]
    tasks: list[dict[str, Any]]
    notes: list[str]
    root_cause: str | None
    duration_ms: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "review_id": self.review_id,
            "status": self.status,
            "agent_keys": list(self.agent_keys),
            "findings": self.findings,
            "tasks": self.tasks,
            "notes": self.notes,
            "root_cause": self.root_cause,
            "duration_ms": self.duration_ms,
        }


@dataclass(frozen=True)
class RunContext:
    runner: AgentRunner
    store: OrchestratorStore
    settings: Settings
    started_at: datetime = field(default_factory=lambda: utc_now())


async def run_review(
    scope: AgentScope,
    *,
    runner: AgentRunner | None = None,
    store: OrchestratorStore | None = None,
    settings: Settings | None = None,
) -> ReviewOutcome:
    """Execute the orchestration graph over ``scope`` and persist its outcome.

    ``runner`` defaults to the in-process runner (tests/local), ``store`` to
    the PostgreSQL store (production). DB-free unit tests inject a
    :class:`MemoryOrchestratorStore` and a mock-backed runner.
    """
    cfg = settings or get_settings()
    ctx = RunContext(
        runner=runner or InProcessAgentRunner(),
        store=store or MemoryOrchestratorStore(),
        settings=cfg,
    )
    review_id = str(uuid.UUID(str(scope.review_id)))
    started = time.monotonic()

    invalid = scope_validation_error(scope)
    if invalid is not None:
        return await _fail_early(ctx, review_id, invalid, started)

    await ctx.store.transition(review_id, ReviewStatus.PROCESSING)
    await ctx.store.transition(review_id, ReviewStatus.AGENTS_RUNNING)

    agent_ids = await ctx.store.sync_agents(agent_metadata())
    graph = build_orchestration_graph(
        ctx.runner,
        max_retries=cfg.orchestrator_max_agent_retries,
        agent_timeout_seconds=cfg.orchestrator_agent_timeout_seconds,
    )
    final = await graph.ainvoke(
        empty_state(review_id, str(scope.repository_id), scope.to_dict())
    )

    duration_ms = int((time.monotonic() - started) * 1000)
    outcome = await _persist(ctx, review_id, scope, agent_ids, final, duration_ms)
    logger.info(
        "orchestration_completed",
        review_id=review_id,
        terminal=final["terminal"],
        persisted_status=outcome.status,
        agents_run=len(outcome.agent_keys),
        findings=len(outcome.findings),
        failed_agents=sorted(
            {
                t["agent_key"]
                for t in outcome.tasks
                if t["status"] == TaskStatus.FAILED.value
            }
        ),
        duration_ms=outcome.duration_ms,
        root_cause=outcome.root_cause,
    )
    return outcome


async def _persist(
    ctx: RunContext,
    review_id: str,
    scope: AgentScope,
    agent_ids: dict[str, str],
    final: dict[str, Any],
    duration_ms: int,
) -> ReviewOutcome:
    scope_data = scope.to_dict()
    now: datetime = utc_now()
    tasks: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    notes = list(final.get("notes") or [])

    for key in final["plan"].get("agent_keys") or []:
        success = next(
            (r for r in final.get("results", []) if r["agent_key"] == key), None
        )
        failed = next(
            (f for f in final.get("failed", []) if f["agent_key"] == key), None
        )
        status = TaskStatus.SUCCESS if success is not None else TaskStatus.FAILED
        retry_count = int(final.get("retries", {}).get(key, 0))
        await ctx.store.create_review_task(
            review_id,
            TaskWrite(
                agent_key=key,
                agent_id=agent_ids[key],
                queue_name=queue_for_agent(key),
                status=status,
                retry_count=retry_count,
                last_error=failed["error"] if failed else None,
                scope=scope_data,
                started_at=now,
                completed_at=now,
            ),
        )
        tasks.append(
            {
                "agent_key": key,
                "status": status.value,
                "retry_count": retry_count,
                "last_error": failed["error"] if failed else None,
            }
        )
        if success is not None:
            findings.extend(success["findings"] or [])
            if success.get("stats"):
                await ctx.store.create_agent_metric(
                    review_id,
                    agent_key=key,
                    agent_id=agent_ids[key],
                    stats=success["stats"],
                )

    for note in notes:
        await ctx.store.append_note(review_id, note)

    terminal = final.get("terminal")
    permanent_failures = bool(final.get("failed"))
    root_cause = final.get("root_cause")
    if permanent_failures or terminal == TERMINAL_FAILED:
        review_status = ReviewStatus.FAILED
        await ctx.store.transition(
            review_id,
            review_status,
            failure_reason=root_cause,
            root_cause=root_cause,
            completed_at=now,
        )
    elif findings:
        review_status = ReviewStatus.CONSOLIDATING  # checkpoint: Phase 7 consumes this
        await ctx.store.transition(review_id, review_status)
    else:
        review_status = ReviewStatus.COMPLETED
        await ctx.store.transition(review_id, review_status, completed_at=now)

    return ReviewOutcome(
        review_id=review_id,
        status=review_status.value,
        agent_keys=tuple(final["plan"].get("agent_keys") or []),
        findings=findings,
        tasks=tasks,
        notes=notes,
        root_cause=root_cause if permanent_failures else None,
        duration_ms=duration_ms,
    )


async def _fail_early(
    ctx: RunContext, review_id: str, reason: str, started: float
) -> ReviewOutcome:
    """A scope that cannot be reviewed: diagnosed FAILED without agent fan-out."""
    now = datetime.now(UTC)
    await ctx.store.transition(
        review_id,
        ReviewStatus.FAILED,
        failure_reason=f"scope unusable: {reason}",
        root_cause=reason,
        completed_at=now,
    )
    await ctx.store.append_note(
        review_id, f"supervisor: review failed before agent fan-out ({reason})."
    )
    logger.warning(
        "orchestration_scope_invalid",
        review_id=review_id,
        reason=reason,
    )
    return ReviewOutcome(
        review_id=review_id,
        status=ReviewStatus.FAILED.value,
        agent_keys=(),
        findings=[],
        tasks=[],
        notes=[f"supervisor: review failed before agent fan-out ({reason})."],
        root_cause=reason,
        duration_ms=int((time.monotonic() - started) * 1000),
    )
