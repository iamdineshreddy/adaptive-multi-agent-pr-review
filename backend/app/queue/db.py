"""Async DB helpers used by the queue tasks (docs/QUEUE.md §4).

Persistence is PostgreSQL-backed and therefore covered by the self-skipping
integration suite when no database is reachable locally; the task cores that
depend on these helpers accept injectable callables so their orchestration logic
is unit-tested DB-free.
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Review, ReviewStatus

logger = structlog.get_logger(__name__)


async def fetch_priority_score(
    session_factory: async_sessionmaker[AsyncSession],
    review_id: uuid.UUID,
) -> float | None:
    """Read the stored priority score for a review run."""
    async with session_factory() as session:
        score = await session.scalar(
            select(Review.priority_score).where(Review.id == review_id)
        )
        return float(score) if score is not None else None


async def mark_review_failed(
    session_factory: async_sessionmaker[AsyncSession],
    review_id: uuid.UUID,
    reason: str,
) -> bool:
    """Terminal transition: Review -> FAILED with a reason (docs/QUEUE.md §4)."""
    async with session_factory() as session, session.begin():
        review = await session.get(Review, review_id)
        if review is None:
            logger.warning("mark_review_failed_missing", review_id=str(review_id))
            return False
        review.status = ReviewStatus.FAILED
        review.failure_reason = reason
        return True
