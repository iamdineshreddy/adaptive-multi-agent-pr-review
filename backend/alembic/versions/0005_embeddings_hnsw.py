"""Phase 7: pgvector HNSW index over finding embeddings (cosine).

Revision ID: 0005_embeddings_hnsw
Revises: 0004_orchestrator_notes
Create Date: 2026-09-13

docs/DATABASE.md §3.12 specifies an HNSW (cosine) index on ``embeddings.vector``
for the redundancy + RAG hot path. The fallback is the exact cosine scan while
tables are small; the index is created idempotently so a re-run is safe.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0005_embeddings_hnsw"
down_revision = "0004_orchestrator_notes"
branch_labels = None
depends_on = None

_INDEX_NAME = "ix_embeddings_vector_hnsw"


def upgrade() -> None:
    op.execute(
        sa.text(
            f"CREATE INDEX IF NOT EXISTS {_INDEX_NAME} ON embeddings "
            "USING hnsw (vector vector_cosine_ops)"
        )
    )


def downgrade() -> None:
    op.execute(sa.text(f"DROP INDEX IF EXISTS {_INDEX_NAME}"))
