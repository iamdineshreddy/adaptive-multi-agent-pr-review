"""Alembic migration environment (async engine, single schema).

The target URL is sourced from ``ADAPTIVE_`` settings rather than
``alembic.ini`` so migrations always run against the same backend the
application uses.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from sqlalchemy import inspect, pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from app.config.settings import get_settings
from app.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", get_settings().database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit SQL to stdout without a live database (``alembic upgrade --sql``)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _widen_version_table(connection: Connection) -> None:
    """Make ``alembic_version.version_num`` fit human-readable revision ids.

    Alembic creates the version table as ``VARCHAR(32)``. Revision ids here are
    descriptive (``0006_repository_memory_learned_weights`` is 38 characters),
    so a fresh database fails on ``UPDATE alembic_version``. Pre-create the
    table with a wider column, or widen an existing one in place.
    """
    if inspect(connection).has_table("alembic_version"):
        connection.execute(
            text(
                "ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(255)"
            )
        )
    else:
        connection.execute(
            text(
                "CREATE TABLE alembic_version ("
                "version_num VARCHAR(255) NOT NULL, "
                "CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num))"
            )
        )
    # The DDL above autobegins a transaction. Alembic treats an already-open
    # transaction as caller-managed and would skip its own commit, so release it
    # here and let Alembic own the migration transaction.
    connection.commit()


def do_run_migrations(connection: Connection) -> None:
    _widen_version_table(connection)
    context.configure(
        connection=connection, target_metadata=target_metadata, compare_type=True
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
