"""Celery task running a single agent for one review task (Phase 5).

The supervisor (Phase 6, LangGraph) fans out one ``agents.run_agent`` task per
review task on ``review_task_queue``. The task is intentionally thin: it rebuilds
the ``AgentScope`` from the JSONB ``scope`` row and delegates to
``app.agents.runner``; structured findings + accounting are returned so the
orchestrator can persist/consolidate them (Phases 6-7).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import structlog
from celery import shared_task

from app.agents.contract import (
    AgentResult,
    AgentScope,
    scope_validation_error,
)
from app.agents.llm import LLMProvider
from app.agents.runner import run_agent

logger = structlog.get_logger(__name__)


async def execute_agent_task_core(
    agent_key: str,
    scope_data: dict[str, Any],
    *,
    provider: LLMProvider | None = None,
) -> dict[str, Any]:
    """Broker-free core: run one agent and produce a serialisable payload."""
    scope = AgentScope.from_dict(scope_data)
    invalid = scope_validation_error(scope)
    if invalid is not None:
        raise ValueError(f"{agent_key} scope invalid: {invalid}")

    result: AgentResult = await run_agent(agent_key, scope, provider=provider)
    payload = {
        "review_id": str(scope.review_id),
        "repository_id": str(scope.repository_id),
        "agent_key": result.agent_key,
        "findings": [finding.model_dump(mode="json") for finding in result.findings],
        "stats": result.stats.to_dict(),
    }
    logger.info(
        "agent_run_completed",
        review_id=str(scope.review_id),
        agent_key=agent_key,
        findings_valid=result.stats.findings_valid,
        failed_results=result.stats.failed_results,
    )
    return payload


@shared_task(
    name="agents.run_agent",
    bind=True,
    acks_late=True,
)
def run_agent_task(
    self: Any,
    review_id: str,
    agent_key: str,
    scope: str | dict[str, Any],
) -> dict[str, Any]:
    """Execute one agent over a review task scope (Celery wrapper)."""
    scope_data = scope if isinstance(scope, dict) else json.loads(scope)
    scope_data.setdefault("review_id", str(uuid.UUID(review_id)))
    return asyncio.run(execute_agent_task_core(agent_key, scope_data))
