"""Pull request, review, and iteration models."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.enums import PRState, ReviewMode, ReviewStatus, RiskClass

if TYPE_CHECKING:
    from app.models.findings import Finding


class PullRequest(Base):
    __tablename__ = "pull_requests"
    __table_args__ = (
        UniqueConstraint("repository_id", "number", name="uq_pr_repo_number"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    repository_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("repositories.id"), nullable=False
    )
    github_pr_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    author_login: Mapped[str] = mapped_column(Text, nullable=False)
    base_ref: Mapped[str] = mapped_column(Text, nullable=False)
    head_ref: Mapped[str] = mapped_column(Text, nullable=False)
    head_sha: Mapped[str] = mapped_column(Text, nullable=False)
    last_reviewed_sha: Mapped[str | None] = mapped_column(Text)
    state: Mapped[PRState] = mapped_column(
        Enum(
            PRState, name="pr_state", values_callable=lambda cls: [m.value for m in cls]
        ),
        default=PRState.OPEN.value,
        nullable=False,
    )
    changed_files: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    additions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    deletions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    priority_score: Mapped[float | None] = mapped_column(Numeric(6, 3))
    risk_class: Mapped[RiskClass | None] = mapped_column(
        Enum(
            RiskClass,
            name="risk_class",
            values_callable=lambda cls: [m.value for m in cls],
        )
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

    reviews: Mapped[list[Review]] = relationship(
        back_populates="pull_request", cascade="all, delete-orphan"
    )


class Review(Base):
    """A single review run for a pull request (the API-facing ``review_id``)."""

    __tablename__ = "reviews"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    pull_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pull_requests.id"), nullable=False
    )
    repository_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("repositories.id"), nullable=False, index=True
    )
    status: Mapped[ReviewStatus] = mapped_column(
        Enum(
            ReviewStatus,
            name="review_status",
            values_callable=lambda cls: [m.value for m in cls],
        ),
        default=ReviewStatus.RECEIVED.value,
        nullable=False,
        index=True,
    )
    mode: Mapped[ReviewMode] = mapped_column(
        Enum(
            ReviewMode,
            name="review_mode",
            values_callable=lambda cls: [m.value for m in cls],
        ),
        default=ReviewMode.OPENED.value,
        nullable=False,
    )
    priority_score: Mapped[float | None] = mapped_column(Numeric(6, 3))
    risk_class: Mapped[RiskClass | None] = mapped_column(
        Enum(
            RiskClass,
            name="risk_class",
            values_callable=lambda cls: [m.value for m in cls],
        )
    )
    github_delivery_id: Mapped[str | None] = mapped_column(
        String(64), unique=True, index=True
    )
    failure_reason: Mapped[str | None] = mapped_column(Text)
    budget_cap: Mapped[int | None] = mapped_column(Integer)
    arum_version: Mapped[str | None] = mapped_column(Text)
    root_cause: Mapped[str | None] = mapped_column(Text)
    feedback_aggregate: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    pull_request: Mapped[PullRequest] = relationship(back_populates="reviews")
    iterations: Mapped[list[ReviewIteration]] = relationship(
        back_populates="review", cascade="all, delete-orphan"
    )
    findings: Mapped[list[Finding]] = relationship(
        back_populates="review", cascade="all, delete-orphan"
    )


class ReviewIteration(Base):
    __tablename__ = "review_iterations"
    __table_args__ = (
        UniqueConstraint("review_id", "iteration", name="uq_iteration_review_round"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    review_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("reviews.id"), nullable=False
    )
    iteration: Mapped[int] = mapped_column(Integer, nullable=False)
    base_sha: Mapped[str] = mapped_column(Text, nullable=False)
    head_sha: Mapped[str] = mapped_column(Text, nullable=False)
    diff_stats: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, nullable=False
    )
    agents_invoked: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, nullable=False
    )
    tokens_used: Mapped[float | None] = mapped_column(Numeric(14, 2))
    cost_usd: Mapped[float | None] = mapped_column(Numeric(12, 6))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    published_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    resolved_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    stale_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    review: Mapped[Review] = relationship(back_populates="iterations")
