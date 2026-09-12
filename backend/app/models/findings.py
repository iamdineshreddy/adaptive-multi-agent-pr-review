"""Finding, finding-group, and embedding models."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.enums import FeedbackOutcome, FindingStatus, Severity


class FindingGroup(Base):
    __tablename__ = "finding_groups"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    review_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("reviews.id"), nullable=False, index=True
    )
    representative_finding_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("findings.id"), nullable=False
    )
    member_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    redundancy_method: Mapped[str] = mapped_column(Text, nullable=False)
    max_pairwise_similarity: Mapped[float | None] = mapped_column(Numeric(6, 4))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Finding(Base):
    __tablename__ = "findings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    review_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("reviews.id"), nullable=False, index=True
    )
    repository_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("repositories.id"), nullable=False, index=True
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id"), nullable=False
    )
    file_path: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    line_start: Mapped[int | None] = mapped_column(Integer)
    line_end: Mapped[int | None] = mapped_column(Integer)
    category: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    severity: Mapped[Severity] = mapped_column(
        Enum(
            Severity,
            name="severity",
            values_callable=lambda cls: [m.value for m in cls],
        ),
        nullable=False,
        index=True,
    )
    confidence: Mapped[float] = mapped_column(Numeric(4, 3), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, nullable=False
    )
    suggested_fix: Mapped[str | None] = mapped_column(Text)
    code_context: Mapped[str | None] = mapped_column(Text)
    agent_reasoning_summary: Mapped[str | None] = mapped_column(Text)
    duplicate_group: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("finding_groups.id"), index=True
    )
    arum_features: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    arum_utility: Mapped[float | None] = mapped_column(Numeric(8, 6))
    arum_version: Mapped[str | None] = mapped_column(Text)
    publication_status: Mapped[FindingStatus] = mapped_column(
        Enum(
            FindingStatus,
            name="finding_status",
            values_callable=lambda cls: [m.value for m in cls],
        ),
        default=FindingStatus.CANDIDATE.value,
        nullable=False,
        index=True,
    )
    feedback_result: Mapped[FeedbackOutcome | None] = mapped_column(
        Enum(
            FeedbackOutcome,
            name="feedback_outcome",
            values_callable=lambda cls: [m.value for m in cls],
        )
    )
    feedback_labelled: Mapped[bool] = mapped_column(default=False, nullable=False)
    temporal_weight: Mapped[float] = mapped_column(
        Numeric(6, 4), default=1.0, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    review = relationship("Review", back_populates="findings")


class Embedding(Base):
    __tablename__ = "embeddings"
    __table_args__ = (
        UniqueConstraint(
            "resource_type", "content_hash", name="uq_embedding_resource_hash"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    repository_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("repositories.id"), nullable=False, index=True
    )
    finding_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("findings.id")
    )
    resource_type: Mapped[str] = mapped_column(
        Text, nullable=False
    )  # finding|standard|decision|comment
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    vector: Mapped[list[float]] = mapped_column(Vector(1536), nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
