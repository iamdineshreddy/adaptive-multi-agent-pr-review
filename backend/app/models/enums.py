"""Domain enums shared by the ORM models and API schemas.

Kept here (not in the schemas package) so that model definitions can reference the
enum instances directly. Values are stable identifiers; never rename them without a
migration.
"""

from __future__ import annotations

import enum


class ReviewStatus(enum.StrEnum):
    """Lifecycle of a review (see docs/ARCHITECTURE.md §4.3)."""

    RECEIVED = "RECEIVED"
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    AGENTS_RUNNING = "AGENTS_RUNNING"
    CONSOLIDATING = "CONSOLIDATING"
    DECIDING = "DECIDING"
    PUBLISHED = "PUBLISHED"
    WAITING_FOR_FEEDBACK = "WAITING_FOR_FEEDBACK"
    ITERATING = "ITERATING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class TaskStatus(enum.StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class Severity(enum.StrEnum):
    INFO = "INFO"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class FindingStatus(enum.StrEnum):
    """Publication lifecycle of a finding."""

    CANDIDATE = "CANDIDATE"
    SCHEDULED = "SCHEDULED"
    PUBLISHED = "PUBLISHED"
    SUPPRESSED = "SUPPRESSED"
    RESOLVED = "RESOLVED"
    STALE = "STALE"


class FeedbackOutcome(enum.StrEnum):
    """Developer outcome. Distinct from 'code changed' (never auto-labelled)."""

    ACCEPTED = "ACCEPTED"
    FIXED = "FIXED"
    MODIFIED = "MODIFIED"
    REJECTED = "REJECTED"
    DISMISSED = "DISMISSED"
    IGNORED = "IGNORED"
    DISCUSSION = "DISCUSSION"


class FeedbackSource(enum.StrEnum):
    API = "API"
    GITHUB_REACTION = "GITHUB_REACTION"
    COMMIT_DIFF = "COMMIT_DIFF"
    MANUAL = "MANUAL"


class PRState(enum.StrEnum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    MERGED = "MERGED"


class RiskClass(enum.StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class ConfidenceLevel(enum.StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class UserRole(enum.StrEnum):
    ADMIN = "ADMIN"
    OPERATOR = "OPERATOR"
    VIEWER = "VIEWER"


class ReviewMode(enum.StrEnum):
    """What triggered the review."""

    OPENED = "OPENED"
    SYNCHRONIZE = "SYNCHRONIZE"
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    REOPENED = "REOPENED"
    MANUAL_RERUN = "MANUAL_RERUN"
