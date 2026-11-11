"""Database-level defaults for NOT NULL repository columns.

Revision ID: 0009_repository_defaults
Revises: 0008_publication
Create Date: 2026-11-11

``repositories.priority_overrides``, ``review_settings`` and ``is_active`` are
NOT NULL with Python-side defaults on the ORM, but pre-0009 databases had no
column default. Any row written with raw SQL (seed/ETL scripts, integration
tests that use ``text("INSERT ...")``) failed with a not-null violation even
though the ORM path worked. Mirror the defaults in PostgreSQL.

``0001_initial_schema`` reflects current ORM metadata, so a fresh database is
already correct; this migration repairs databases created before this change.
Idempotent: re-running against an already-fixed schema is a no-op.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0009_repository_defaults"
down_revision = "0008_publication"
branch_labels = None
depends_on = None

_DEFAULTS = {
    "priority_overrides": sa.text("'{}'::jsonb"),
    "review_settings": sa.text("'{}'::jsonb"),
    "is_active": sa.text("true"),
}


def _defaults() -> dict[str, str | None]:
    inspector = sa.inspect(op.get_bind())
    return {
        col["name"]: col.get("default")
        for col in inspector.get_columns("repositories")
        if col["name"] in _DEFAULTS
    }


def upgrade() -> None:
    current = _defaults()
    for column, default in _DEFAULTS.items():
        if column in current and current[column] is None:
            op.alter_column(
                "repositories",
                column,
                existing_type=sa.Boolean() if column == "is_active" else sa.JSON(),
                existing_nullable=False,
                server_default=default,
            )


def downgrade() -> None:
    current = _defaults()
    for column in _DEFAULTS:
        if current.get(column) is not None:
            op.alter_column(
                "repositories",
                column,
                existing_type=sa.Boolean() if column == "is_active" else sa.JSON(),
                existing_nullable=False,
                server_default=None,
            )
