"""Database integration tests (skipped when no PostgreSQL is reachable).

Locally we have no PostgreSQL server, so these tests self-skip with a clear
reason. CI/dev machines that set ``ADAPTIVE_DATABASE_URL`` to a real database
exercise the full schema round-trip.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.config.settings import get_settings
from app.database import SessionFactory, create_db_engine
from app.models import Base, Repository, User
from app.webhooks.ingest import IngestOutcome, SqlReviewIngester
from app.webhooks.schemas import PullRequestWebhookEvent


async def _require_db() -> None:
    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(
            f"PostgreSQL not reachable at {get_settings().database_url} "
            f"({type(exc).__name__}); skipping integration tests."
        )
    finally:
        await engine.dispose()


async def _reset_schema(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)


async def test_schema_roundtrip() -> None:
    """Create all tables, insert a row, read it back."""
    await _require_db()
    engine = create_db_engine()
    try:
        await _reset_schema(engine)
        repo_name = f"integration/{uuid.uuid4().hex[:8]}"
        async with SessionFactory() as session, session.begin():
            session.add(
                Repository(
                    github_id=123456789,
                    full_name=repo_name,
                    default_branch="main",
                    main_language="python",
                )
            )
        async with SessionFactory() as session:
            rows = (
                (
                    await session.execute(
                        text("SELECT full_name FROM repositories WHERE full_name = :n"),
                        {"n": repo_name},
                    )
                )
                .scalars()
                .all()
            )
            assert rows == [repo_name]
    finally:
        await engine.dispose()


async def test_uuid_pk_defaults_populated() -> None:
    """Inserting without an explicit id yields a populated UUID."""
    await _require_db()
    engine = create_db_engine()
    try:
        await _reset_schema(engine)
        async with SessionFactory() as session, session.begin():
            session.add_all([User(github_login="u1"), User(github_login="u2")])
        async with SessionFactory() as session:
            users = (
                (await session.execute(text("SELECT github_login FROM users")))
                .scalars()
                .all()
            )
            assert set(users) == {"u1", "u2"}
    finally:
        await engine.dispose()


async def _webhook_payload() -> dict:
    return {
        "action": "opened",
        "number": 17,
        "pull_request": {
            "id": 9001,
            "number": 17,
            "title": "Integration PR",
            "state": "open",
            "user": {"login": "integration", "id": 1},
            "base": {"ref": "main", "sha": "base-sha"},
            "head": {"ref": "feature", "sha": "head-sha"},
            "changed_files": 2,
            "additions": 25,
            "deletions": 3,
        },
        "repository": {
            "id": 42424242,
            "full_name": f"integration/ingest-{uuid.uuid4().hex[:8]}",
            "default_branch": "main",
            "language": "python",
        },
        "installation": {"id": 7},
    }


async def test_webhook_ingest_creates_review() -> None:
    """SqlReviewIngester persists repo/PR and creates a queued review."""
    await _require_db()
    engine = create_db_engine()
    try:
        await _reset_schema(engine)
        event = PullRequestWebhookEvent.model_validate(await _webhook_payload())
        ingester = SqlReviewIngester(SessionFactory)
        delivery = str(uuid.uuid4())

        first = await ingester.ingest(event, delivery)
        assert isinstance(first, IngestOutcome)
        assert first.created is True
        assert first.priority is not None
        assert first.priority.score >= 0

        duplicate = await ingester.ingest(event, delivery)
        assert duplicate.created is False
        assert duplicate.review_id == first.review_id

        async with SessionFactory() as session:
            count = (
                await session.execute(
                    text("SELECT COUNT(*) FROM reviews WHERE github_delivery_id = :d"),
                    {"d": delivery},
                )
            ).scalar()
            assert count == 1
    finally:
        await engine.dispose()
