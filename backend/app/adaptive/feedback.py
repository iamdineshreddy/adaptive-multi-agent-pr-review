"""Developer feedback: explicit outcomes, code-change signals, memory updates.

Phase 11 closes the feedback loop (docs/ARCHITECTURE.md §4.6, ARUM.md §5,
FR-5.1/FR-5.2). The hard rule, enforced here and unit-tested, is that **an
explicit outcome is always required before any memory counts a finding as
accepted** — "code changed" produces re-evaluation evidence, never a label
(FR-5.2). This module is pure (no store); persistence + memory recomputation
live on the API/service path.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from typing import Any

from app.models.enums import FeedbackOutcome, FeedbackSource

# Actionability = ACCEPTED / FIXED (ARUM.md §5); rejection = REJECTED / DISMISSED.
# IGNORED is a deliberate non-follow-up and scores as negative evidence for the
# learning labels; MODIFIED / DISCUSSION are neutral (neither positive nor
# negative) and never labelled.
ACTIONABLE_OUTCOMES: frozenset[FeedbackOutcome] = frozenset(
    {FeedbackOutcome.ACCEPTED, FeedbackOutcome.FIXED}
)
REJECTIVE_OUTCOMES: frozenset[FeedbackOutcome] = frozenset(
    {FeedbackOutcome.REJECTED, FeedbackOutcome.DISMISSED, FeedbackOutcome.IGNORED}
)


@dataclass(frozen=True)
class FeedbackWrite:
    """One explicit developer-feedback row to persist to ``developer_feedback``.

    ``outcome`` is always supplied by the caller (API body or a GitHub signal
    that already carried an outcome) — there is no synthesised-outcome path.
    ``category`` is looked up from the finding so the memory snapshot can roll
    up shares without a join.
    """

    review_id: str
    finding_id: str
    repository_id: str
    category: str
    outcome: FeedbackOutcome
    source: FeedbackSource = FeedbackSource.API
    author_login: str | None = None
    commit_sha: str | None = None
    details: dict[str, Any] | None = None

    def to_row(self, feedback_id: str, created_at: str) -> dict[str, Any]:
        """The dict row the in-memory store keeps (stable keys for tests)."""
        return {
            "id": feedback_id,
            "review_id": self.review_id,
            "finding_id": self.finding_id,
            "repository_id": self.repository_id,
            "category": self.category,
            "outcome": self.outcome.value,
            "source": self.source.value,
            "author_login": self.author_login,
            "commit_sha": self.commit_sha,
            "details": self.details,
            "created_at": created_at,
        }


def outcome_label(outcome: FeedbackOutcome) -> int | None:
    """Binary learning label: 1 actionable, 0 rejective, None neutral.

    Only these ever enter the training matrix; MODIFIED / DISCUSSION are
    evidence, not a publish signal (ARUM.md §5).
    """
    if outcome in ACTIONABLE_OUTCOMES:
        return 1
    if outcome in REJECTIVE_OUTCOMES:
        return 0
    return None


def feedback_identity(write: FeedbackWrite) -> str:
    """Deterministic id so a replayed submission is idempotent.

    Re-submitting the same (review, finding, outcome, commit, author) returns
    the same row instead of duplicating the signal (ARCHITECTURE §6 webhook/API
    idempotency discipline).
    """
    digest = hashlib.sha256()
    for part in (
        write.review_id,
        write.finding_id,
        write.repository_id,
        write.outcome.value,
        write.commit_sha or "",
        write.author_login or "",
    ):
        digest.update(part.encode("utf-8"))
        digest.update(b"|")
    return str(uuid.UUID(hex=digest.hexdigest()[:32]))


def code_change_signal(*, finding_id: str, commit_sha: str) -> dict[str, Any]:
    """Evidence that a commit touched the finding's region — **never a label**.

    FR-5.2 / ARCHITECTURE §4.6: "code changed" means "re-evaluate", nothing
    more. This evidencing dict deliberately carries no ``outcome`` key; any
    caller that has only this signal has no feedback to persist.
    """
    return {
        "signal": "code_changed",
        "finding_id": finding_id,
        "commit_sha": commit_sha,
        "re_evaluate": True,
    }


def attach_code_change_evidence(
    write: FeedbackWrite, signal: dict[str, Any]
) -> FeedbackWrite:
    """Augment ``details`` with code-change evidence without touching outcome.

    The explicit outcome supplied by the developer stays exactly as given —
    a commit that references the suggestion never upgrades IGNORED/MODIFIED to
    ACCEPTED on its own (ARUM.md §5, FR-5.2).
    """
    details = dict(write.details or {})
    details.setdefault("code_change_signals", []).append(signal)
    return FeedbackWrite(
        review_id=write.review_id,
        finding_id=write.finding_id,
        repository_id=write.repository_id,
        category=write.category,
        outcome=write.outcome,
        source=write.source,
        author_login=write.author_login,
        commit_sha=write.commit_sha,
        details=details,
    )
