"""Dead-letter handling for tasks that exhaust max_retries (docs/QUEUE.md §6).

The record is appended to the Redis dead-letter log (ephemeral, like the priority
zset) and logged with structured JSON. The authoritative terminal state (Review ->
FAILED with reason) is a DB write performed by ``app.queue.db.mark_review_failed``.
No silent drop: every exhaustion is observable.
"""

from __future__ import annotations

import itertools
import uuid
from dataclasses import dataclass
from typing import Protocol

import structlog
from redis.asyncio import Redis

from app.config.settings import get_settings

logger = structlog.get_logger(__name__)

_DELIMITER = "|"


@dataclass(frozen=True)
class DeadLetterEntry:
    """Record of a task that exhausted its retries."""

    review_id: uuid.UUID
    delivery_id: str
    queue: str
    error: str
    attempts: int


class DeadLetterLog(Protocol):
    """Boundary over the dead-letter log (Redis list in production)."""

    async def record(self, entry: DeadLetterEntry) -> None: ...
    async def close(self) -> None: ...


def encode_entry(entry: DeadLetterEntry) -> str:
    """Deterministic wire format for the Redis list members."""
    return _DELIMITER.join(
        (
            str(entry.review_id),
            entry.delivery_id,
            entry.queue,
            entry.error.replace(_DELIMITER, " "),
            str(entry.attempts),
        )
    )


class RedisDeadLetterLog:
    """Appends records to a Redis list (LIFO: newest first)."""

    def __init__(self, url: str | None = None, key: str | None = None) -> None:
        settings = get_settings()
        self._client: Redis = Redis.from_url(
            url or settings.redis_url, decode_responses=True
        )
        self._key = key or settings.celery_dead_letter_redis_key

    async def record(self, entry: DeadLetterEntry) -> None:
        await self._client.lpush(self._key, encode_entry(entry))  # type: ignore[misc]

    async def close(self) -> None:
        await self._client.aclose()


class MemoryDeadLetterLog:
    """Deterministic in-process log for tests."""

    def __init__(self) -> None:
        self.entries: list[DeadLetterEntry] = []
        self._counter = itertools.count()

    async def record(self, entry: DeadLetterEntry) -> None:
        self.entries.append(entry)
        next(self._counter)

    async def close(self) -> None:
        return None


def record_dead_letter(
    entry: DeadLetterEntry,
    log: DeadLetterLog | None = None,
) -> None:
    """Append a dead-letter record and emit an alertable structured log line."""
    logger.error(
        "task_dead_lettered",
        review_id=str(entry.review_id),
        delivery_id=entry.delivery_id,
        queue=entry.queue,
        error=entry.error,
        attempts=entry.attempts,
        note="Task exhausted max_retries; routed to dead-letter log.",
    )
    _log = log or RedisDeadLetterLog()
    import asyncio

    asyncio.run(_log.record(entry))
