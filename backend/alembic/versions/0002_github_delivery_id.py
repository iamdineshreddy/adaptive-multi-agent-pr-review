"""Add reviews.github_delivery_id for webhook idempotency.

Revision ID: 0002_github_delivery_id
Revises: 0001_initial_schema
Create Date: 2026-09-12

Webhook deliveries are identified by the ``X-GitHub-Delivery`` header. Storing it
uniquely on the review row makes in-flight/duplicate deliveries idempotent at the
database (docs/QUEUE.md §5), independent of the memory (Redis) idempotency cache.

``0001_initial_schema`` reflect the *current* ORM metadata, so a fresh database
already carries this column/index when 0001 runs; every DDL below is guarded so a
fresh ``alembic upgrade head`` and an in-place upgrade of an existing database
both succeed.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0002_github_delivery_id"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None

_INDEX_NAME = "uq_reviews_github_delivery_id"


def _columns(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {col["name"] for col in inspector.get_columns(table)}


def _indexes(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {idx["name"] for idx in inspector.get_indexes(table)}


def upgrade() -> None:
    if "github_delivery_id" not in _columns("reviews"):
        op.add_column(
            "reviews",
            sa.Column("github_delivery_id", sa.String(64), nullable=True),
        )
    if _INDEX_NAME not in _indexes("reviews"):
        op.create_index(_INDEX_NAME, "reviews", ["github_delivery_id"], unique=True)


def downgrade() -> None:
    if _INDEX_NAME in _indexes("reviews"):
        op.drop_index(_INDEX_NAME, table_name="reviews")
    if "github_delivery_id" in _columns("reviews"):
        op.drop_column("reviews", "github_delivery_id")