"""Celery entry point that drives the orchestrator for a scheduled review.

Docs/QUEUE.md §1 defines ``review_task_queue`` as the orchestrator fan-out
dispatcher. The worker builds the live scope (DB + GitHub) when the dispatch
payload carries only a ``review_id``, runs the LangGraph graph, and persists the
outcome. The broker-free execution lives in ``service.run_review`` and is what
the unit suite exercises; this module is the seam for production/wiring tests.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from celery.utils.log import get_task_logger

from app.agents.contract import AgentScope
from app.orchestrator.persistence import OrchestratorStore
from app.orchestrator.runners import AgentRunner
from app.orchestrator.scope_builder import build_scope_data_for_review
from app.orchestrator.service import ReviewOutcome, run_review

logger = get_task_logger(__name__)


async def execute_orchestration_core(
    review_id: str,
    scope_data: dict[str, Any] | None = None,
    *,
    runner: AgentRunner | None = None,
    store: OrchestratorStore | None = None,
) -> ReviewOutcome:
    """Run the orchestrator for one review; scope is rebuilt when not supplied."""
    uuid.UUID(review_id)  # fail fast with a clear message on bad ids
    if scope_data is None:
        scope_data = await build_scope_data_for_review(uuid.UUID(review_id))
    scope = AgentScope.from_dict(scope_data)
    return await run_review(scope, runner=runner, store=store)


@shared_task(bind=True, name="orchestrator.run_review", acks_late=True)
def run_review_task(
    self: Any,
    review_id: str,
    scope: str | dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Celery task (``orchestrator.run_review``) -> review_task_queue."""
    try:
        scope_data: dict[str, Any] | None = scope if isinstance(scope, dict) else None
        if isinstance(scope, str):
            scope_data = json.loads(scope)
        outcome = asyncio.run(execute_orchestration_core(review_id, scope_data))
        return outcome.to_dict()
    except SoftTimeLimitExceeded:
        logger.warning("orchestrator soft time limit hit", review_id=review_id)
        raise
