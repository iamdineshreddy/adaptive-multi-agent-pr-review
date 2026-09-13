"""Point-of-scheduling dispatch: review task -> ``orchestrator.run_review``.

The ``queue.pop_and_stage`` hand-off in Phase 4 is the fan-out origin
(docs/QUEUE.md §1: review_task_queue = orchestrator fan-out dispatcher). The
dispatcher stays a thin, injectable core so tests exercise it without celery
delivery to a broker; the production ``send_task`` call is off-loaded to a
thread pool.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable
from typing import Protocol

import structlog

from app.queue.celery_app import QUEUES, celery_app

logger = structlog.get_logger(__name__)

_ORCHESTRATOR_TASK = "orchestrator.run_review"


class Dispatch(Protocol):
    """Push one review into the orchestrator fan-out."""

    def __call__(self, review_id: uuid.UUID) -> Awaitable[None]: ...


class OrchestrationDispatcher:
    """Sends ``orchestrator.run_review`` to the review-task queue."""

    def __init__(self, *, task_name: str = _ORCHESTRATOR_TASK) -> None:
        self._task_name = task_name

    async def dispatch(self, review_id: uuid.UUID) -> None:
        task_id = await asyncio.to_thread(
            celery_app.send_task,
            self._task_name,
            args=[str(review_id)],
            queue=QUEUES["review_task"],
        )
        logger.info(
            "orchestration_dispatched",
            review_id=str(review_id),
            task_id=str(task_id.id),
            queue=QUEUES["review_task"],
        )


dispatch_to_orchestrator = OrchestrationDispatcher().dispatch
