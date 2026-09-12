"""Async database engine and session factory.

PostgreSQL is the only supported backend (docs/DATABASE.md).

.. note::
   pgvector codec registration (``pgvector.asyncpg.register_vector``) must be
   called asynchronously on each raw asyncpg connection.  The engine factory
   below does not do this automatically because pool event listeners are sync
   while the asyncpg driver is async.  A startup hook is planned for Phase 14
   (monitoring / production readiness).  Tests skip cleanly without a live
   database, and the Vector column type is handled at the ORM layer by
   ``pgvector.sqlalchemy``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config.settings import get_settings
from app.models.base import Base

_TARGET_POSTGRES = "postgresql+asyncpg"


def create_db_engine() -> AsyncEngine:
    settings = get_settings()
    engine = create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        echo=settings.database_echo_logging,
    )
    if not engine.url.drivername.startswith(_TARGET_POSTGRES):
        raise ValueError(
            f"Unsupported database backend '{engine.url.drivername}'; "
            f"only '{_TARGET_POSTGRES}' is supported."
        )
    return engine


engine = create_db_engine()

SessionFactory = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    autoflush=False,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a session with transactional rollback."""
    async with SessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_models() -> None:
    """Create all tables/schema (non-migration) — used by tests on dev DBs."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
