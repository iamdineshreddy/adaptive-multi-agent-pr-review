"""Add reviews.github_delivery_id for webhook idempotency.

Revision ID: 0002_github_delivery_id
Revises: 0001_initial_schema
Create Date: 2026-09-12

Webhook deliveries are identified by the ``X-GitHub-Delivery`` header. Storing it
uniquely on the review row makes in-flight/duplicate deliveries idempotent at the
database (docs/QUEUE.md §5), independent of the memory (Redis) idempotency cache.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_github_delivery_id"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None

_INDEX_NAME = "uq_reviews_github_delivery_id"


def upgrade() -> None:
    op.add_column(
        "reviews",
        sa.Column("github_delivery_id", sa.String(64), nullable=True),
    )
    op.create_index(_INDEX_NAME, "reviews", ["github_delivery_id"], unique=True)


def downgrade() -> None:
    op.drop_index(_INDEX_NAME, table_name="reviews")
    op.drop_column("reviews", "github_delivery_id")