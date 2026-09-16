"""Add ``admin_audit_log`` (Phase 15 part 2b, FR-7.3).

Revision ID: 0007_admin_audit_log
Revises: 0006_repository_memory_learned_weights
Create Date: 2026-09-16

docs/SECURITY.md §12: every admin write action (reruns, repository-settings
changes) is appended here with the acting token digest + role, and the
before/after state. ``target_id`` is a polymorphic UUID (review or repository);
``repository_id`` is denormalised for scope filtering and FK'd to repositories.
Idempotent DDL guards a re-run against already-migrated databases.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0007_admin_audit_log"
down_revision = "0006_repository_memory_learned_weights"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS admin_audit_log (
                id uuid PRIMARY KEY,
                action text NOT NULL,
                target_kind text NOT NULL,
                target_id uuid NOT NULL,
                repository_id uuid,
                principal_token_hash text NOT NULL,
                principal_role text NOT NULL,
                principal_label text NOT NULL DEFAULT '',
                before jsonb,
                after jsonb,
                created_at timestamptz NOT NULL DEFAULT now(),
                CONSTRAINT fk_admin_audit_repository
                    FOREIGN KEY (repository_id) REFERENCES repositories(id)
            )
            """
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_admin_audit_created_at "
            "ON admin_audit_log (created_at)"
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_admin_audit_action "
            "ON admin_audit_log (action)"
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_admin_audit_repository_id "
            "ON admin_audit_log (repository_id)"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS admin_audit_log"))
