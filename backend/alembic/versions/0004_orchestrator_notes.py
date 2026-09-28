"""Phase 6 orchestration support: supervisor notes + status history on reviews.

Revision ID: 0004_orchestrator_notes
Revises: 0003_queue_dead_letter
Create Date: 2026-09-13

Per docs/ARCHITECTURE.md §4.3 every review state change is time-stamped, and
docs/AGENTS.md §2 requires supervisor notes to be appended when an agent fails
permanently (the review is never silently completed). Both fields are JSONB so a
migration is not needed for every append.

``0001_initial_schema`` reflect the *current* ORM metadata, so a fresh database
already carries these columns when 0001 runs; the ``ADD COLUMN`` guards keep both
fresh and in-place upgrades idempotent.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0004_orchestrator_notes"
down_revision = "0003_queue_dead_letter"
branch_labels = None
depends_on = None

_JSONB_EMPTY = sa.text("'[]'::jsonb")


def _columns(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {col["name"] for col in inspector.get_columns(table)}


def upgrade() -> None:
    existing = _columns("reviews")
    if "supervisor_notes" not in existing:
        op.add_column(
            "reviews",
            sa.Column("supervisor_notes", JSONB(), server_default=_JSONB_EMPTY),
        )
    if "status_history" not in existing:
        op.add_column(
            "reviews",
            sa.Column("status_history", JSONB(), server_default=_JSONB_EMPTY),
        )


def downgrade() -> None:
    existing = _columns("reviews")
    if "status_history" in existing:
        op.drop_column("reviews", "status_history")
    if "supervisor_notes" in existing:
        op.drop_column("reviews", "supervisor_notes")