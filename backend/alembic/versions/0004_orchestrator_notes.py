"""Phase 6 orchestration support: supervisor notes + status history on reviews.

Revision ID: 0004_orchestrator_notes
Revises: 0003_queue_dead_letter
Create Date: 2026-09-13

Per docs/ARCHITECTURE.md §4.3 every review state change is time-stamped, and
docs/AGENTS.md §2 requires supervisor notes to be appended when an agent fails
permanently (the review is never silently completed). Both fields are JSONB so a
migration is not needed for every append.
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


def upgrade() -> None:
    op.add_column(
        "reviews",
        sa.Column("supervisor_notes", JSONB(), server_default=_JSONB_EMPTY),
    )
    op.add_column(
        "reviews",
        sa.Column("status_history", JSONB(), server_default=_JSONB_EMPTY),
    )


def downgrade() -> None:
    op.drop_column("reviews", "status_history")
    op.drop_column("reviews", "supervisor_notes")
