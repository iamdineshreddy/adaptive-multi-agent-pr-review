"""Queue tasks driving the Phase 4 priority mechanism (docs/QUEUE.md §3-4).

- ``enqueue_review_task``: consumed on ``pr_ingestion_queue``; reads the stored
  priority score and stages the review on the Redis priority zset.
- ``pop_and_stage_task``: scheduler entry point; pops the highest-priority review
  from the zset and hands it to the orchestrator fan-out core
  (``queue.pop_and_dispatch``). The fan-out itself is Phase 6 (LangGraph),
  dispatched as ``orchestrator.run_review`` on ``review_task_queue``.

DB/Redis work is delegated to ``asyncio.run`` cores; the store, score loader and
dispatch callable are injectable so the orchestration logic is unit-testable
without a broker.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from celery import shared_task
from celery.exceptions import MaxRetriesExceededError

from app.config.settings import get_settings
from app.database import SessionFactory
from app.orchestrator.dispatch import Dispatch
from app.queue.backoff import backoff_delay
from app.queue.db import fetch_priority_score
from app.queue.deadletter import (
    DeadLetterEntry,
    RedisDeadLetterLog,
    record_dead_letter,
)
from app.queue.priority import PriorityEntry, PriorityStore, RedisPriorityStore

logger = structlog.get_logger(__name__)

_PR_INGESTION_QUEUE = "pr_ingestion_queue"

ScoreLoader = Callable[[uuid.UUID], Awaitable[float | None]]


async def load_default_score(review_id: uuid.UUID) -> float | None:
    """Default score loader: read ``Review.priority_score`` from PostgreSQL."""
    return await fetch_priority_score(SessionFactory, review_id)


async def stage_review_to_priority(
    review_id: uuid.UUID,
    delivery_id: str,
    store: PriorityStore,
    load_score: ScoreLoader,
    ordinal: int | None = None,
) -> dict[str, object]:
    """Push an ingested review run onto the priority zset (QUEUE.md §3-4)."""
    score = await load_score(review_id)
    if score is None:
        raise ValueError(f"review {review_id} has no priority score to stage")
    await store.enqueue(
        review_id,
        score,
        ordinal if ordinal is not None else time.time_ns(),
    )
    logger.info(
        "review_staged_on_priority_zset",
        review_id=str(review_id),
        delivery_id=delivery_id,
        priority_score=score,
        queue=_PR_INGESTION_QUEUE,
    )
    return {
        "review_id": str(review_id),
        "delivery_id": delivery_id,
        "priority_score": score,
        "queue": _PR_INGESTION_QUEUE,
    }


async def pop_and_dispatch(
    store: PriorityStore, dispatch: Dispatch
) -> PriorityEntry | None:
    """Pop the highest-priority review and push it into the orchestrator fan-out.

    The popped review is this call's responsibility: the hand-off sends
    ``orchestrator.run_review`` to ``review_task_queue`` (docs/QUEUE.md §1).
    Dispatch failures re-raise so the caller decides whether the review stays
    acknowledged (broker acks_late governs redelivery).
    """
    entry = await store.pop_highest()
    if entry is None:
        return None
    await dispatch(entry.review_id)
    logger.info(
        "scheduler_dispatched_review",
        review_id=str(entry.review_id),
        priority_score=entry.score,
        task="orchestrator.run_review",
    )
    return entry


async def drain_and_stage(store: PriorityStore) -> PriorityEntry | None:
    """Pop the highest-priority review without dispatching (scheduler core)."""
    entry = await store.pop_highest()
    if entry is not None:
        logger.info(
            "scheduler_handoff_intent",
            review_id=str(entry.review_id),
            priority_score=entry.score,
            note="Pop pulled; dispatch handled by pop_and_dispatch.",
        )
    return entry


@shared_task(
    bind=True,
    name="queue.enqueue_review",
    acks_late=True,
)
def enqueue_review_task(
    self: Any, review_id: str, delivery_id: str
) -> dict[str, object]:
    """Stage an ingested review on the priority zset with bounded retries."""
    settings = get_settings()
    rid = uuid.UUID(str(review_id))
    max_retries = settings.celery_max_retries

    async def _run() -> dict[str, object]:
        store = RedisPriorityStore()
        try:
            return await stage_review_to_priority(
                rid, delivery_id, store, load_default_score
            )
        finally:
            await store.aclose()

    try:
        result = asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001 - Celery owns the retry policy
        if self.request.retries >= max_retries:
            record_dead_letter(
                DeadLetterEntry(
                    review_id=rid,
                    delivery_id=delivery_id,
                    queue=_PR_INGESTION_QUEUE,
                    error=str(exc),
                    attempts=self.request.retries + 1,
                ),
                log=RedisDeadLetterLog(),
            )
            raise MaxRetriesExceededError from exc
        raise self.retry(
            exc=exc,
            countdown=backoff_delay(
                self.request.retries, settings.celery_retry_backoff_seconds
            ),
        ) from exc
    return result


@shared_task(name="queue.pop_and_stage")
def pop_and_stage_task() -> str | None:
    """Scheduler: pop the highest-priority review and dispatch the fan-out."""

    async def _pop() -> PriorityEntry | None:
        from app.orchestrator.dispatch import OrchestrationDispatcher

        store = RedisPriorityStore()
        try:
            return await pop_and_dispatch(store, OrchestrationDispatcher().dispatch)
        finally:
            await store.aclose()

    entry = asyncio.run(_pop())
    return str(entry.review_id) if entry else None
