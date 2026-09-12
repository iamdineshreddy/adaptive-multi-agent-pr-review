"""Dispatch boundary implementations.

The webhook must return immediately (docs/ARCHITECTURE.md §4.1) while AI work is
deferred to the queue. ``CeleryDispatcher`` (Phase 4) publishes to
``pr_ingestion_queue``; ``LoggingDispatcher`` is the Phase 3 fallback that records
dispatch intent without a broker (never pretends to enqueue).
"""

from __future__ import annotations

import structlog

from app.queue.contracts import DispatchResult, ReviewDispatcher

__all__ = ["DispatchResult", "LoggingDispatcher", "ReviewDispatcher"]

logger = structlog.get_logger(__name__)

_PR_INGESTION_QUEUE = "pr_ingestion_queue"


class LoggingDispatcher(ReviewDispatcher):
    """Records dispatch intent; no broker connection (Phase 4 wires Celery)."""

    async def dispatch(self, review_id: object, delivery_id: str) -> DispatchResult:
        logger.info(
            "webhook_dispatch_intent",
            review_id=str(review_id),
            delivery_id=delivery_id,
            queue=_PR_INGESTION_QUEUE,
            provider="logging-only",
            note="Celery broker enqueue lands in Phase 4.",
        )
        return DispatchResult(
            dispatched=False,
            note=f"Review {review_id} staged for {_PR_INGESTION_QUEUE}; "
            "broker enqueue lands in Phase 4.",
        )
