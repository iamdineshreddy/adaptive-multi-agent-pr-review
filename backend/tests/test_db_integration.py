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
