"""End-to-end queue pipeline against real services (Phase 16).

Exercises the full delivery seam from webhook ingest to scheduling hand-off with
the *real* providers: ``SqlReviewIngester`` (PostgreSQL), the Redis priority
zset, the DB score loader, and ``pop_and_dispatch``. This is the integration the
broker wiring promised in phase 4 ("broker delivery is exercised in Phase 16
integration"). Requires reachable PostgreSQL + Redis; otherwise self-skips with
a clear reason, exactly like the sibling ``test_db_integration``.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.config.settings import get_settings
from app.models import Base
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
            f"({type(exc).__name__}); skipping pipeline integration tests."
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
            f"({type(exc).__name__}); skipping pipeline integration tests."
        )
    finally:
        await client.aclose()


async def _reset_schema(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)


def _webhook_payload() -> dict[str, object]:
    return {
        "action": "opened",
        "number": 41,
        "pull_request": {
            "id": 41001,
            "number": 41,
            "title": "Pipeline PR",
            "state": "open",
            "user": {"login": "pipeline", "id": 1},
            "base": {"ref": "main", "sha": "base-sha"},
            "head": {"ref": "feature", "sha": "head-sha"},
            "changed_files": 2,
            "additions": 25,
            "deletions": 3,
        },
        "repository": {
            "id": 42424243,
            "full_name": f"integration/pipeline-{uuid.uuid4().hex[:8]}",
            "default_branch": "main",
            "language": "python",
        },
        "installation": {"id": 7},
    }


async def test_webhook_to_priority_zset_to_dispatch() -> None:
    """Ingest a webhook, stage it on the zset, pop it and hand off to fan-out."""
    await _require_services()
    from app.database import create_db_engine

    engine = create_db_engine()
    zset = RedisPriorityStore()
    try:
        await _reset_schema(engine)

        event = PullRequestWebhookEvent.model_validate(_webhook_payload())
        delivery = str(uuid.uuid4())
        outcome = await SqlReviewIngester().ingest(event, delivery)
        assert outcome.created is True
        assert outcome.priority is not None

        staged = await stage_review_to_priority(
            outcome.review_id, delivery, zset, load_default_score
        )
        assert staged["review_id"] == str(outcome.review_id)
        assert staged["priority_score"] == outcome.priority.score
        assert await zset.size() == 1

        dispatched: list[uuid.UUID] = []

        async def capture(review_id: uuid.UUID) -> None:
            dispatched.append(review_id)

        entry = await pop_and_dispatch(zset, capture)
        assert entry is not None
        assert entry.review_id == outcome.review_id
        assert dispatched == [outcome.review_id]
        assert await zset.size() == 0
    finally:
        await zset.aclose()
        await engine.dispose()
