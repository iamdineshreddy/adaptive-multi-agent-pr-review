"""Worker-crash / restarted-in-flight recovery under a real broker (Phase 16 part 2).

Pipeline-triggered worker crash scenario against the *real* services, extending
the sibling ``test_integration_pipeline``: PostgreSQL ``SqlReviewIngester``,
``fetch_priority_score`` (DB as system of record), the Redis priority zset, and
``pop_and_dispatch``. Requires reachable PostgreSQL + Redis; otherwise self-skips
exactly like its siblings, with a clear reason.

The crash is modeled at the boundary the Celery worker owns (docs/QUEUE.md §4):
once ``RedisPriorityStore.pop_highest`` removes the top member, the popped review
is *this* worker iteration's responsibility. A crash anywhere between that pop
and the fan-out dispatch leaves the review off the zset; the DB row remains the
system of record (docs/app/queue/priority.py), and restart recovery re-stages it
through the same ``stage_review_to_priority`` path the ingest task uses before
``pop_and_dispatch`` hands it to fan-out exactly once. These tests verify the
recovery contract is real against the broker+DB pair the production workers use.

IMPORTANT (honesty): these tests are authored against the real services but are
only *executed* where Postgres + Redis are reachable. On this machine they
self-skip; execution happens with the Phase 18 stack (docs/ROADMAP.md Phase 16
part 2). No result is claimed until then.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.config.settings import get_settings
from app.database import SessionFactory
from app.models import Base
from app.models.pr import Review
from app.queue.db import fetch_priority_score
from app.queue.priority import RedisPriorityStore
from app.queue.tasks import (
    load_default_score,
    pop_and_dispatch,
    stage_review_to_priority,
)
from app.webhooks.ingest import SqlReviewIngester
from app.webhooks.schemas import PullRequestWebhookEvent


async def _require_services() -> None:
    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(
            f"PostgreSQL not reachable at {get_settings().database_url} "
            f"({type(exc).__name__}); skipping crash-recovery integration tests."
        )
    finally:
        await engine.dispose()

    import redis.asyncio as redis

    client = redis.Redis.from_url(get_settings().redis_url)
    try:
        await client.ping()
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(
            f"Redis not reachable at {get_settings().redis_url} "
            f"({type(exc).__name__}); skipping crash-recovery integration tests."
        )
    finally:
        await client.aclose()


async def _reset_schema(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)


def _webhook_payload(number: int) -> dict[str, object]:
    return {
        "action": "opened",
        "number": number,
        "pull_request": {
            "id": 40000 + number,
            "number": number,
            "title": f"Crash pipeline PR {number}",
            "state": "open",
            "user": {"login": "crash", "id": 1},
            "base": {"ref": "main", "sha": "base-sha"},
            "head": {"ref": "feature", "sha": f"head-sha-{number}"},
            "changed_files": 2,
            "additions": 25,
            "deletions": 3,
        },
        "repository": {
            "id": 42424240 + number,
            "full_name": f"integration/crash-{uuid.uuid4().hex[:8]}-{number}",
            "default_branch": "main",
            "language": "python",
        },
        "installation": {"id": 7},
    }


async def _ingest_and_stage(
    number: int, zset: RedisPriorityStore
) -> tuple[object, str]:
    """Ingest one webhook and stage it on the real zset.

    Returns ``(review_id, delivery_id)`` with the ingested row asserted present.
    """
    event = PullRequestWebhookEvent.model_validate(_webhook_payload(number))
    delivery = str(uuid.uuid4())
    outcome = await SqlReviewIngester().ingest(event, delivery)
    assert outcome.created is True
    assert outcome.priority is not None
    staged = await stage_review_to_priority(
        outcome.review_id, delivery, zset, load_default_score
    )
    assert staged["review_id"] == str(outcome.review_id)
    return outcome.review_id, delivery


async def _set_priority_score(review_ids: dict[object, float]) -> None:
    """Force distinct zset scores via the DB (the authoritative score source)."""
    async with SessionFactory() as session, session.begin():
        for review_id, score in review_ids.items():
            review = await session.get(Review, uuid.UUID(str(review_id)))
            assert review is not None, "review row must exist (DB is system of record)"
            review.priority_score = score


async def test_crash_between_pop_and_dispatch_recovers_from_db() -> None:
    """A crash after the pop leaves the zset empty and the DB authoritative; a
    restart re-stages the same review and it is handed to fan-out exactly once."""
    await _require_services()
    from app.database import create_db_engine

    engine = create_db_engine()
    zset = RedisPriorityStore()
    try:
        await _reset_schema(engine)
        await zset.reset()

        review_id, _ = await _ingest_and_stage(41, zset)
        assert await zset.size() == 1

        # Worker crashes between pop and dispatch: the pop already removed the
        # top member, so no other scheduler tick can re-hand it in this cycle.
        crashed = await zset.pop_highest()
        assert crashed is not None
        assert crashed.review_id == uuid.UUID(str(review_id))
        assert await zset.size() == 0

        # The DB row survives as the system of record for recovery.
        score = await fetch_priority_score(SessionFactory, uuid.UUID(str(review_id)))
        assert score is not None and score > 0

        # Restart recovery re-stages from the DB through the ingest path.
        restaged = await stage_review_to_priority(
            uuid.UUID(str(review_id)),
            f"recovery:{uuid.uuid4().hex}",
            zset,
            load_default_score,
        )
        assert restaged["priority_score"] == score
        assert await zset.size() == 1

        dispatched: list[object] = []

        async def capture(review_id: object) -> None:
            dispatched.append(review_id)

        entry = await pop_and_dispatch(zset, capture)
        assert entry is not None
        assert entry.review_id == uuid.UUID(str(review_id))
        assert [str(r) for r in dispatched] == [str(review_id)]
        assert await zset.size() == 0
    finally:
        await zset.aclose()
        await engine.dispose()


async def test_restarted_in_flight_has_no_duplicate_handoff() -> None:
    """One crash consumes the fl own member; recovery adds exactly one, so the
    restarted run never double-hands a review into fan-out."""
    await _require_services()
    from app.database import create_db_engine

    engine = create_db_engine()
    zset = RedisPriorityStore()
    try:
        await _reset_schema(engine)
        await zset.reset()

        review_id, _ = await _ingest_and_stage(42, zset)
        assert await zset.size() == 1

        crashed = await zset.pop_highest()
        assert crashed is not None
        assert await zset.size() == 0

        await stage_review_to_priority(
            uuid.UUID(str(review_id)),
            f"recovery:{uuid.uuid4().hex}",
            zset,
            load_default_score,
        )
        assert await zset.size() == 1  # not 2: the crashed member is gone

        handoffs: list[object] = []

        async def capture(review_id: object) -> None:
            handoffs.append(review_id)

        entry = await pop_and_dispatch(zset, capture)
        assert entry is not None
        assert handoffs == [uuid.UUID(str(review_id))]
        assert await zset.size() == 0
    finally:
        await zset.aclose()
        await engine.dispose()


async def test_crash_recovery_preserves_priority_order_across_restart() -> None:
    """With two reviews staged, a crash on the top one + re-stage of both keeps
    the scheduler's ordering intact: highest score first, then the rest."""
    await _require_services()
    from app.database import create_db_engine

    engine = create_db_engine()
    zset = RedisPriorityStore()
    try:
        await _reset_schema(engine)
        await zset.reset()

        low_review, _ = await _ingest_and_stage(51, zset)
        high_review, _ = await _ingest_and_stage(52, zset)
        await _set_priority_score({low_review: 0.5, high_review: 9.9})

        await zset.reset()
        await stage_review_to_priority(
            uuid.UUID(str(low_review)), "d-low", zset, load_default_score
        )
        await stage_review_to_priority(
            uuid.UUID(str(high_review)), "d-high", zset, load_default_score
        )
        assert await zset.size() == 2

        # Crash lands on the highest-priority review after its pop.
        crashed = await zset.pop_highest()
        assert crashed is not None
        assert crashed.review_id == uuid.UUID(str(high_review))
        assert await zset.size() == 1

        # Restart recovery re-stages the crashed review; ordering must survive.
        await stage_review_to_priority(
            uuid.UUID(str(high_review)), "recovery-high", zset, load_default_score
        )
        assert await zset.size() == 2

        handoffs: list[object] = []

        async def capture(review_id: object) -> None:
            handoffs.append(review_id)

        first = await pop_and_dispatch(zset, capture)
        second = await pop_and_dispatch(zset, capture)
        assert first is not None and second is not None
        assert first.review_id == uuid.UUID(str(high_review))
        assert second.review_id == uuid.UUID(str(low_review))
        assert [str(r) for r in handoffs] == [str(high_review), str(low_review)]
        assert await zset.size() == 0
    finally:
        await zset.aclose()
        await engine.dispose()
