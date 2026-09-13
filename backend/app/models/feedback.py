"""Developer feedback, repository memory, and system metric models."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    Double,
    Enum,
    ForeignKey,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.enums import FeedbackOutcome, FeedbackSource


class DeveloperFeedback(Base):
    __tablename__ = "developer_feedback"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    finding_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("findings.id"), index=True
    )
    review_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("reviews.id"), nullable=False, index=True
    )
    repository_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("repositories.id"), nullable=False, index=True
    )
    outcome: Mapped[FeedbackOutcome] = mapped_column(
        Enum(
            FeedbackOutcome,
            name="feedback_outcome",
            values_callable=lambda cls: [m.value for m in cls],
        ),
        nullable=False,
        index=True,
    )
    source: Mapped[FeedbackSource] = mapped_column(
        Enum(
            FeedbackSource,
            name="feedback_source",
            values_callable=lambda cls: [m.value for m in cls],
        ),
        default=FeedbackSource.API.value,
        nullable=False,
    )
    author_login: Mapped[str | None] = mapped_column(Text)
    commit_sha: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


class RepositoryMemory(Base):
    __tablename__ = "repository_memory"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    repository_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("repositories.id"), unique=True, nullable=False
    )
    snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, nullable=False
    )
    decay_params: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, nullable=False
    )
    arum_weights: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    learned_weights: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class SystemMetric(Base):
    __tablename__ = "system_metrics"
    __table_args__ = (
        UniqueConstraint("metric_name", "sampled_at", name="uq_metric_sample"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    metric_name: Mapped[str] = mapped_column(Text, nullable=False)
    repository_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("repositories.id")
    )
    value: Mapped[float] = mapped_column(Double, nullable=False)
    labels: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    sampled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
