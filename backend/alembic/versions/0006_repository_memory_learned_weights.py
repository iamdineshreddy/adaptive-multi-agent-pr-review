"""Phase 11: ``repository_memory.learned_weights`` (ARUM weight candidates).

Revision ID: 0006_repository_memory_learned_weights
Revises: 0005_embeddings_hnsw
Create Date: 2026-09-13

docs/DATABASE.md §3.11: the Adaptive Learning Module stores *candidate* weight
sets (docs/ARUM.md §4) here — fitted, in-sample evaluated, versioned, and never
auto-promoted into active scoring (NFR-5.1). Idempotent ``ADD COLUMN IF NOT
EXISTS`` guards a re-run against already-migrated databases.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0006_repository_memory_learned_weights"
down_revision = "0005_embeddings_hnsw"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "ALTER TABLE repository_memory "
            "ADD COLUMN IF NOT EXISTS learned_weights jsonb"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text("ALTER TABLE repository_memory DROP COLUMN IF EXISTS learned_weights")
    )
