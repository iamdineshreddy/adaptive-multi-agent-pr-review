"""Celery-backed dispatch replacing the Phase 3 log-only dispatcher.

``send_task`` publishes ``queue.enqueue_review`` to ``pr_ingestion_queue``; the
worker then reads the stored priority score and stages the review on the Redis
priority zset (docs/QUEUE.md §3-4). Requires a reachable broker — a failure here
surfaces as a 500 on the webhook, which is the honest behaviour for an enqueue
step (the Phase 3 ``LoggingDispatcher`` remains available via
``queue_dispatch_provider=logging``).
"""

from __future__ import annotations

import asyncio

import structlog

from app.queue.celery_app import celery_app
from app.queue.contracts import DispatchResult

logger = structlog.get_logger(__name__)

_PR_INGESTION_QUEUE = "pr_ingestion_queue"
_TASK_NAME = "queue.enqueue_review"


class CeleryDispatcher:
    """Publishes ingestion tasks to the ``pr_ingestion_queue``."""

    async def dispatch(self, review_id: object, delivery_id: str) -> DispatchResult:
        result = await asyncio.to_thread(
            celery_app.send_task,
            _TASK_NAME,
            args=[str(review_id), delivery_id],
            queue=_PR_INGESTION_QUEUE,
        )
        logger.info(
            "webhook_dispatch_enqueued",
            review_id=str(review_id),
            delivery_id=delivery_id,
            task_id=result.id,
            queue=_PR_INGESTION_QUEUE,
            provider="celery",
        )
        return DispatchResult(
            dispatched=True,
            note=f"Review {review_id} enqueued on {_PR_INGESTION_QUEUE} "
            f"(task {result.id}).",
        )
