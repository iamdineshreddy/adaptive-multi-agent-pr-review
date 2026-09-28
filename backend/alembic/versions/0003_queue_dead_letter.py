"""Phase 4 queue support: failure reason on reviews + dead-letter table.

Revision ID: 0003_queue_dead_letter
Revises: 0002_github_delivery_id
Create Date: 2026-09-12

Per docs/QUEUE.md §4 and §6: tasks exhausting max_retries must leave the review in a
terminal ``FAILED`` state carrying a reason, and the dead-letter event must be
durably recorded for the dashboard/alerting.

``0001_initial_schema`` reflect the *current* ORM metadata, so a fresh database
already carries ``failure_reason``/``dead_letters`` when 0001 runs; every DDL
below is guarded so fresh and in-place upgrades both succeed.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision = "0003_queue_dead_letter"
down_revision = "0002_github_delivery_id"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {col["name"] for col in inspector.get_columns(table)}


def _tables() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return set(inspector.get_table_names())


def _indexes(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {idx["name"] for idx in inspector.get_indexes(table)}


def upgrade() -> None:
    if "failure_reason" not in _columns("reviews"):
        op.add_column(
            "reviews",
            sa.Column("failure_reason", sa.Text(), nullable=True),
        )
    if "dead_letters" not in _tables():
        op.create_table(
            "dead_letters",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "review_id",
                UUID(as_uuid=True),
                sa.ForeignKey("reviews.id"),
                nullable=False,
            ),
            sa.Column("delivery_id", sa.Text(), nullable=True),
            sa.Column("queue_name", sa.Text(), nullable=False),
            sa.Column("task_name", sa.Text(), nullable=False),
            sa.Column("error", sa.Text(), nullable=False),
            sa.Column("attempts", sa.Integer(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
        )
    if "ix_dead_letters_review_id" not in _indexes("dead_letters"):
        op.create_index("ix_dead_letters_review_id", "dead_letters", ["review_id"])


def downgrade() -> None:
    if "ix_dead_letters_review_id" in _indexes("dead_letters"):
        op.drop_index("ix_dead_letters_review_id", table_name="dead_letters")
    if "dead_letters" in _tables():
        op.drop_table("dead_letters")
    if "failure_reason" in _columns("reviews"):
        op.drop_column("reviews", "failure_reason")