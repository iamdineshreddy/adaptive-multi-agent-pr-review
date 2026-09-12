"""Redis sorted-set priority queue for review scheduling (docs/QUEUE.md §3).

``ZADD priority score member`` encodes the member as ``{ordinal:020d}:{review_id}``
so ties on ``priority_score`` are broken by FIFO ordinal (lexicographic member
order == insertion order). The DB row stays the system of record; the zset is a
non-authoritative scheduling hint. A live Redis server is not available here, so
only the pure helpers are unit-tested; broker-backed behaviour self-skips without
Redis and is exercised by the repository-wide integration suite in Phase 16.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol

import redis.asyncio as redis

from app.config.settings import get_settings

_ORDINAL_WIDTH = 20


@dataclass(frozen=True)
class PriorityEntry:
    """A review staged on the priority zset."""

    review_id: uuid.UUID
    score: float
    ordinal: int


def make_member(ordinal: int, review_id: uuid.UUID) -> str:
    """Encodes the zset member; insertion order == lexicographic order."""
    if ordinal < 0:
        raise ValueError("ordinal must be >= 0")
    return f"{ordinal:0{_ORDINAL_WIDTH}d}:{review_id}"


def member_review_id(member: str) -> uuid.UUID:
    """Extracts the review id from an encoded member string."""
    return uuid.UUID(member.split(":", 1)[1])


class PriorityStore(Protocol):
    """Boundary over the scheduling store (Redis in production)."""

    async def enqueue(
        self, review_id: uuid.UUID, score: float, ordinal: int
    ) -> None: ...
    async def pop_highest(self) -> PriorityEntry | None: ...
    async def size(self) -> int: ...
    async def reset(self) -> None: ...


class RedisPriorityStore:
    """Redis sorted-set implementation of :class:`PriorityStore`."""

    def __init__(self, url: str | None = None, key: str | None = None) -> None:
        settings = get_settings()
        self._client = redis.Redis.from_url(
            url or settings.redis_url, decode_responses=True
        )
        self._key = key or settings.celery_priority_redis_key

    async def enqueue(self, review_id: uuid.UUID, score: float, ordinal: int) -> None:
        await self._client.zadd(self._key, {make_member(ordinal, review_id): score})

    async def pop_highest(self) -> PriorityEntry | None:
        member_scores = await self._client.zrange(self._key, 0, 0, withscores=True)
        if not member_scores:
            return None
        member, score = member_scores[0]
        removed = await self._client.zrem(self._key, member)
        if not removed:
            return None
        return PriorityEntry(
            review_id=member_review_id(member),
            score=float(score),
            ordinal=int(member.split(":", 1)[0]),
        )

    async def size(self) -> int:
        count = await self._client.zcard(self._key)
        return int(count)

    async def reset(self) -> None:
        await self._client.delete(self._key)

    async def aclose(self) -> None:
        await self._client.aclose()
