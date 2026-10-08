"""Publisher tasks: PUBLISH step (docs/ARCHITECTURE.md §4.5, component J).

- ``publisher.publish_review``: claims one ``DECIDING``/``ITERATING`` review,
  posts its ARUM-selected findings back to the PR as a GitHub review, then marks
  the review ``PUBLISHED`` and its findings ``PUBLISHED``.
- ``publisher.dequeue_pending``: beat scan that hands every pending review to
  ``publisher.publish_review`` on ``publisher_queue``.

Broker/DB concerns are delegated to ``asyncio.run`` cores; the store and GitHub
client are injectable so the publish pipeline is unit-testable offline. The
transient/permanent split from ``publish_review`` maps onto this task's retry
policy (transient -> Celery retry, permanent -> dead-letter + diagnosed FAILED).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

import structlog
from celery import shared_task
from celery.exceptions import MaxRetriesExceededError

from app.config.settings import get_settings
from app.github.client import GitHubClient
from app.orchestrator.persistence import OrchestratorStore, utc_now
from app.publisher.service import PublishError, PublishOutcome, publish_review
from app.queue.backoff import backoff_delay
from app.queue.celery_app import QUEUES, celery_app
from app.queue.deadletter import DeadLetterEntry, RedisDeadLetterLog, record_dead_letter

logger = structlog.get_logger(__name__)

_PUBLISH_QUEUE = QUEUES["publisher"]
_PUBLISH_TASK = "publisher.publish_review"

PublishDispatch = Callable[[uuid.UUID], Awaitable[Any]]


def _github_client() -> GitHubClient:
    settings = get_settings()
    return GitHubClient(
        base_url=settings.github_webhook_url,
        token=settings.github_pat,
    )


async def scan_and_dispatch(
    store: OrchestratorStore,
    dispatch: PublishDispatch,
    *,
    limit: int = 5,
) -> list[str]:
    """Scan the DB for pending publications and hand each to ``dispatch``."""
    settings = get_settings()
    candidates = await store.list_reviews_pending_publication(
        limit=limit,
        claim_stale_before=utc_now()
        - timedelta(seconds=settings.publisher_claim_stale_seconds),
    )
    handed: list[str] = []
    for review_id in candidates:
        await dispatch(uuid.UUID(review_id))
        handed.append(str(review_id))
    if handed:
        logger.info(
            "publisher_dequeued",
            count=len(handed),
            review_ids=handed,
            queue=_PUBLISH_QUEUE,
        )
    return handed


@shared_task(
    bind=True,
    name="publisher.publish_review",
    acks_late=True,
)
def publish_review_task(self: Any, review_id: str) -> dict[str, object]:
    """Worker entry point: publish one review's selected findings."""
    settings = get_settings()
    rid = uuid.UUID(str(review_id))
    max_retries = settings.celery_max_retries

    async def _run() -> PublishOutcome:
        from app.orchestrator.persistence import SqlOrchestratorStore

        store = SqlOrchestratorStore()
        return await publish_review(store, _github_client(), rid, settings=settings)

    try:
        outcome = asyncio.run(_run())
    except PublishError as exc:
        if exc.retryable and self.request.retries < max_retries:
            logger.warning(
                "publisher_retry",
                review_id=str(rid),
                retry=self.request.retries + 1,
                reason=str(exc),
            )
            raise self.retry(
                exc=exc,
                countdown=backoff_delay(
                    self.request.retries, settings.celery_retry_backoff_seconds
                ),
            ) from exc
        record_dead_letter(
            DeadLetterEntry(
                review_id=rid,
                delivery_id="publisher",
                queue=_PUBLISH_QUEUE,
                error=str(exc),
                attempts=self.request.retries + 1,
            ),
            log=RedisDeadLetterLog(),
        )
        raise MaxRetriesExceededError from exc
    except Exception as exc:  # noqa: BLE001 - Celery owns the retry policy
        if self.request.retries < max_retries:
            raise self.retry(
                exc=exc,
                countdown=backoff_delay(
                    self.request.retries, settings.celery_retry_backoff_seconds
                ),
            ) from exc
        record_dead_letter(
            DeadLetterEntry(
                review_id=rid,
                delivery_id="publisher",
                queue=_PUBLISH_QUEUE,
                error=str(exc),
                attempts=self.request.retries + 1,
            ),
            log=RedisDeadLetterLog(),
        )
        raise MaxRetriesExceededError from exc

    logger.info(
        "publisher_task_done",
        review_id=str(rid),
        github_review_id=outcome.github_review_id,
        posted=outcome.posted,
        degraded=outcome.degraded,
        already_published=outcome.already_published,
    )
    return {
        "review_id": str(rid),
        "github_review_id": outcome.github_review_id,
        "posted": outcome.posted,
        "degraded": outcome.degraded,
        "already_published": outcome.already_published,
    }


@shared_task(name="publisher.dequeue_pending")
def dequeue_pending_task() -> list[str]:
    """Beat: scan DB for pending publications and dispatch each to the queue.

    Runs only when beat is started with ``celery beat``; the DB scan is cheap
    (top-N ids) and the enqueue keeps acks small.
    """

    async def _scan() -> list[str]:
        from app.orchestrator.persistence import SqlOrchestratorStore

        store = SqlOrchestratorStore()

        async def _dispatch(review_id: uuid.UUID) -> None:
            task = await asyncio.to_thread(
                celery_app.send_task,
                _PUBLISH_TASK,
                args=[str(review_id)],
                queue=_PUBLISH_QUEUE,
            )
            logger.info(
                "publisher_dispatched",
                review_id=str(review_id),
                task_id=str(task.id),
                queue=_PUBLISH_QUEUE,
            )

        return await scan_and_dispatch(store, _dispatch)

    return asyncio.run(_scan())
