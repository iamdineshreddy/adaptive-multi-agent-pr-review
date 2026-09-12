"""Dispatch boundary between the webhook HTTP layer and the queue (Phase 4).

The webhook must return immediately (docs/ARCHITECTURE.md §4.1) while AI work is
deferred to the queue. Phase 3 has no broker yet, so ``LoggingDispatcher`` records
the dispatch intent in structured logs -- it does NOT pretend to enqueue. Phase 4
replaces it with a Celery-backed dispatcher (``pr_ingestion_queue``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import structlog

logger = structlog.get_logger(__name__)

_PR_INGESTION_QUEUE = "pr_ingestion_queue"


@dataclass(frozen=True)
class DispatchResult:
    dispatched: bool
    note: str


class ReviewDispatcher(Protocol):
    """Boundary over the broker (replaced by the Celery implementation)."""

    async def dispatch(self, review_id: object, delivery_id: str) -> DispatchResult: ...


class LoggingDispatcher:
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
