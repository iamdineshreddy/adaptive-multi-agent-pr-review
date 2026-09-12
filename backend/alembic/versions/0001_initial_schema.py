"""Create the initial schema for Phase 2.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-09-12

The initial schema is sourced from the ORM metadata (``app.models``) so the
migration and the models can never drift. The full DDL for every column,
constraint and index is therefore the authoritative definition in
``backend/app/models/``. Future revisions must be hand-written per
``docs/DATABASE.md``.
"""

from __future__ import annotations

import sqlalchemy as sa

import app.models  # noqa: F401  (registers every mapped table on Base.metadata)
from alembic import op
from app.models import Base

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
    op.execute(sa.text("DROP EXTENSION IF EXISTS vector"))
