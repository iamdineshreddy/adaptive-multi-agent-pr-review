"""Phase 4 queue support: failure reason on reviews + dead-letter table.

Revision ID: 0003_queue_dead_letter
Revises: 0002_github_delivery_id
Create Date: 2026-09-12

Per docs/QUEUE.md §4 and §6: tasks exhausting max_retries must leave the review in a
terminal ``FAILED`` state carrying a reason, and the dead-letter event must be
durably recorded for the dashboard/alerting.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0003_queue_dead_letter"
down_revision = "0002_github_delivery_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "reviews",
        sa.Column("failure_reason", sa.Text(), nullable=True),
    )
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
    op.create_index(
        "ix_dead_letters_review_id", "dead_letters", ["review_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_dead_letters_review_id", table_name="dead_letters")
    op.drop_table("dead_letters")
    op.drop_column("reviews", "failure_reason")