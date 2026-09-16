"""Admin audit log (Phase 15 part 2b, FR-7.3, docs/SECURITY.md §12).

Every admin write action (``review.rerun``, ``repository.settings.update``) is
appended here with the acting principal (digest, role, label) and the
before/after state, so admin behaviour is traceable without storing token
plaintext. The ``target_id`` column is polymorphic (review id or repository id)
by design — the audit row is an append-only record, not a FK-bound entity.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AdminAuditLog(Base):
    __tablename__ = "admin_audit_log"
    __table_args__ = (
        Index("ix_admin_audit_created_at", "created_at"),
        Index("ix_admin_audit_action", "action"),
        Index("ix_admin_audit_repository_id", "repository_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    action: Mapped[str] = mapped_column(Text, nullable=False)
    target_kind: Mapped[str] = mapped_column(Text, nullable=False)  # review|repository
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    repository_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("repositories.id")
    )
    principal_token_hash: Mapped[str] = mapped_column(Text, nullable=False)
    principal_role: Mapped[str] = mapped_column(Text, nullable=False)
    principal_label: Mapped[str] = mapped_column(Text, nullable=False, default="")
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
