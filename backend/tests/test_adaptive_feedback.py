"""Phase 11: pure feedback logic (FR-5.1, FR-5.2).

Exercises outcome labelling, deterministic idempotency keys, code-change
evidence semantics (no synthetic outcome), and FeedbackWrite serialisation.
"""

from __future__ import annotations

import pytest

from app.adaptive.feedback import (
    ACTIONABLE_OUTCOMES,
    REJECTIVE_OUTCOMES,
    FeedbackWrite,
    attach_code_change_evidence,
    code_change_signal,
    feedback_identity,
    outcome_label,
)
from app.models.enums import FeedbackOutcome

# --- outcome_label -----------------------------------------------------------


class TestOutcomeLabel:
    @pytest.mark.parametrize(
        "outcome,expected",
        [
            (FeedbackOutcome.ACCEPTED, 1),
            (FeedbackOutcome.FIXED, 1),
            (FeedbackOutcome.REJECTED, 0),
            (FeedbackOutcome.DISMISSED, 0),
            (FeedbackOutcome.IGNORED, 0),
            (FeedbackOutcome.MODIFIED, None),
            (FeedbackOutcome.DISCUSSION, None),
        ],
    )
    def test_expected_label(self, outcome: FeedbackOutcome, expected: int | None):
        assert outcome_label(outcome) == expected

    def test_actionable_and_rejective_sets_partition_all_outcomes(self):
        covered = (
            ACTIONABLE_OUTCOMES
            | REJECTIVE_OUTCOMES
            | {
                FeedbackOutcome.MODIFIED,
                FeedbackOutcome.DISCUSSION,
            }
        )
        assert covered == set(FeedbackOutcome)


# --- feedback_identity -------------------------------------------------------


def _base_write(
    *,
    outcome: FeedbackOutcome = FeedbackOutcome.ACCEPTED,
    author_login: str | None = "dev",
    commit_sha: str | None = None,
    details: dict | None = None,
) -> FeedbackWrite:
    return FeedbackWrite(
        review_id="00000000-0000-0000-0000-000000000001",
        finding_id="00000000-0000-0000-0000-000000000002",
        repository_id="00000000-0000-0000-0000-000000000003",
        category="security/xss",
        outcome=outcome,
        author_login=author_login,
        commit_sha=commit_sha,
        details=details,
    )


class TestFeedbackIdentity:
    def test_deterministic(self):
        a = feedback_identity(_base_write())
        b = feedback_identity(_base_write())
        assert a == b

    def test_differs_on_outcome(self):
        a = feedback_identity(_base_write(outcome=FeedbackOutcome.ACCEPTED))
        b = feedback_identity(_base_write(outcome=FeedbackOutcome.IGNORED))
        assert a != b

    def test_differs_on_author(self):
        a = feedback_identity(_base_write(author_login="alice"))
        b = feedback_identity(_base_write(author_login="bob"))
        assert a != b

    def test_is_valid_uuid(self):
        import uuid

        fid = feedback_identity(_base_write())
        uuid.UUID(fid)  # raises on invalid


# --- code_change_signal ------------------------------------------------------


class TestCodeChangeSignal:
    def test_has_no_outcome_key(self):
        signal = code_change_signal(finding_id="f1", commit_sha="abc123")
        assert "outcome" not in signal

    def test_re_evaluate_true(self):
        signal = code_change_signal(finding_id="f1", commit_sha="abc123")
        assert signal["re_evaluate"] is True
        assert signal["signal"] == "code_changed"


# --- attach_code_change_evidence ---------------------------------------------


class TestAttachCodeChangeEvidence:
    def test_outcome_preserved_when_ignored(self):
        write = _base_write(outcome=FeedbackOutcome.IGNORED)
        signal = code_change_signal(finding_id="f1", commit_sha="abc123")
        updated = attach_code_change_evidence(write, signal)
        assert updated.outcome == FeedbackOutcome.IGNORED

    def test_outcome_preserved_when_accepted(self):
        write = _base_write(outcome=FeedbackOutcome.ACCEPTED)
        signal = code_change_signal(finding_id="f1", commit_sha="abc123")
        updated = attach_code_change_evidence(write, signal)
        assert updated.outcome == FeedbackOutcome.ACCEPTED

    def test_appends_to_existing_signals(self):
        write = _base_write(details={"code_change_signals": [{"old": True}]})
        signal = code_change_signal(finding_id="f1", commit_sha="abc123")
        updated = attach_code_change_evidence(write, signal)
        assert len(updated.details["code_change_signals"]) == 2
        assert updated.details["code_change_signals"][1]["commit_sha"] == "abc123"

    def test_preserves_original_write_immutability(self):
        write = _base_write()
        original_details = write.details
        signal = code_change_signal(finding_id="f1", commit_sha="abc123")
        attach_code_change_evidence(write, signal)
        assert write.details == original_details


# --- FeedbackWrite.to_row ----------------------------------------------------


class TestFeedbackWriteToRow:
    def test_stable_keys(self):
        write = _base_write(commit_sha="sha-1")
        row = write.to_row("fb-id-1", "2026-09-13T00:00:00+00:00")
        assert row["id"] == "fb-id-1"
        assert row["finding_id"] == write.finding_id
        assert row["outcome"] == "ACCEPTED"
        assert row["source"] == "API"
        assert row["commit_sha"] == "sha-1"
        assert row["created_at"] == "2026-09-13T00:00:00+00:00"
        assert set(row.keys()) == {
            "id",
            "review_id",
            "finding_id",
            "repository_id",
            "category",
            "outcome",
            "source",
            "author_login",
            "commit_sha",
            "details",
            "created_at",
        }
