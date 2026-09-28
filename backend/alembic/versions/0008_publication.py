"""GitHub publication support: review/finding GitHub ids + published_at.

Revision ID: 0008_publication
Revises: 0007_admin_audit_log
Create Date: 2026-09-22

Implements the PUBLISH step (component J, docs/ARCHITECTURE.md §4.5): the
publisher posts selected findings back to the GitHub PR as a review with inline
comments, then atomically marks the review ``PUBLISHED`` (with ``published_at``
and the created ``github_review_id``) and each posted finding ``PUBLISHED``
(carrying its GitHub comment id) for idempotent re-runs.

``0001_initial_schema`` reflects current ORM metadata, so on a fresh database the
columns already exist; this migration only adds them to pre-0008 databases.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0008_publication"
down_revision = "0007_admin_audit_log"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {col["name"] for col in inspector.get_columns(table)}


_ADD = {
    "reviews": [
        sa.Column("github_review_id", sa.BigInteger(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    ],
    "findings": [
        sa.Column("github_comment_id", sa.BigInteger(), nullable=True),
        sa.Column("github_review_id", sa.BigInteger(), nullable=True),
    ],
}


def upgrade() -> None:
    for table, columns in _ADD.items():
        existing = _columns(table)
        for column in columns:
            if column.name not in existing:
                op.add_column(table, column)


def downgrade() -> None:
    for table, columns in _ADD.items():
        existing = _columns(table)
        for column in columns:
            if column.name in existing:
                op.drop_column(table, column.name)