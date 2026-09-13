"""Phase 11: ARUM weight learning (ARUM.md §4, NFR-5.1).

The learner joins decision-trace rows to explicit feedback by finding id,
skips unlabelled candidates, and returns ``None`` (never a noisy model) below
the min-examples guard. Candidates are versioned, in-sample evaluated, and
written to ``repository_memory.learned_weights`` — never promoted to active
scoring.
"""

from __future__ import annotations

import uuid

import pytest

from app.adaptive.features import FEATURE_NAMES
from app.adaptive.feedback import FeedbackWrite
from app.adaptive.learning import (
    feedback_labels,
    labelled_matrix,
    learn_candidate,
)
from app.adaptive.memory import FeedbackEvent
from app.adaptive.training import TrainingMatrix, build_training_matrix
from app.models.enums import FeedbackOutcome
from app.orchestrator.persistence import MemoryOrchestratorStore

REPO_ID = "00000000-0000-0000-0000-00000000000a"


def _event(
    finding_id: str,
    outcome: FeedbackOutcome,
    *,
    age_days: float = 10.0,
) -> FeedbackEvent:
    return FeedbackEvent(
        category="security/xss",
        outcome=outcome,
        age_days=age_days,
        finding_id=finding_id,
    )


# --- feedback_labels ---------------------------------------------------------


class TestFeedbackLabels:
    def test_maps_actionable_and_rejective(self):
        events = [
            _event("f1", FeedbackOutcome.ACCEPTED),
            _event("f2", FeedbackOutcome.REJECTED),
            _event("f3", FeedbackOutcome.FIXED),
            _event("f4", FeedbackOutcome.DISMISSED),
        ]
        assert feedback_labels(events) == {"f1": 1, "f2": 0, "f3": 1, "f4": 0}

    def test_skips_neutral_outcomes(self):
        events = [
            _event("f1", FeedbackOutcome.MODIFIED),
            _event("f2", FeedbackOutcome.DISCUSSION),
        ]
        assert feedback_labels(events) == {}

    @pytest.mark.parametrize(
        "outcome",
        [FeedbackOutcome.MODIFIED, FeedbackOutcome.DISCUSSION],
    )
    def test_neutral_event_never_sets_label_alongside_actionable(
        self, outcome: FeedbackOutcome
    ):
        events = [_event("f1", FeedbackOutcome.ACCEPTED), _event("f1", outcome)]
        assert feedback_labels(events) == {"f1": 1}

    def test_first_labelled_event_wins(self):
        events = [
            _event("f1", FeedbackOutcome.REJECTED),
            _event("f1", FeedbackOutcome.ACCEPTED),
        ]
        assert feedback_labels(events) == {"f1": 0}

    def test_skips_events_without_finding(self):
        event = FeedbackEvent(
            category="security/xss", outcome=FeedbackOutcome.ACCEPTED, age_days=1.0
        )
        assert feedback_labels([event]) == {}


# --- labelled_matrix ---------------------------------------------------------


FEATURE_HEADERS = ("w1", "w2", "w3", "w4")


def _decision(finding_id: str, w1: float, w3: float) -> dict:
    return {
        "finding_id": finding_id,
        "features": {"w1": w1, "w2": 0.5, "w3": w3, "w4": 0.1},
    }


class TestLabelledMatrix:
    def test_joins_by_finding_and_builds_vector_in_header_order(self):
        labels = {"f1": 1, "f2": 0}
        rows = [_decision("f1", 0.2, 0.9), _decision("f2", 0.8, 0.1)]
        matrix, coverage = labelled_matrix(rows, labels, headers=FEATURE_HEADERS)
        assert matrix.count == 2
        assert matrix.headers == FEATURE_HEADERS
        assert matrix.y == [1, 0]
        assert matrix.X[0] == (0.2, 0.5, 0.9, 0.1)
        assert matrix.X[1] == (0.8, 0.5, 0.1, 0.1)
        assert coverage == {"traces": 2, "labelled": 2, "unlabelled": 0}

    def test_skips_unlabelled_rows(self):
        labels = {"f1": 1}
        rows = [_decision("f1", 0.2, 0.9), _decision("f3", 0.4, 0.4)]
        matrix, coverage = labelled_matrix(rows, labels, headers=FEATURE_HEADERS)
        assert matrix.count == 1
        assert matrix.y == [1]
        assert coverage == {"traces": 2, "labelled": 1, "unlabelled": 1}

    def test_empty_labels_yields_empty_matrix(self):
        matrix, coverage = labelled_matrix(
            [_decision("f7", 0.1, 0.1)], {}, headers=FEATURE_HEADERS
        )
        assert matrix.count == 0
        assert coverage["unlabelled"] == 1

    def test_missing_feature_row_is_skipped(self):
        labels = {"f1": 1}
        rows = [{"finding_id": "f1", "features": None}, _decision("f1", 0.3, 0.6)]
        matrix, _ = labelled_matrix(rows, labels, headers=FEATURE_HEADERS)
        # only the well-formed row contributed; the None row carried no vector
        assert matrix.count == 1
        assert matrix.X[0] == (0.3, 0.5, 0.6, 0.1)


# --- learn_candidate ---------------------------------------------------------


def _synthetic_matrix(n: int, *labels: int) -> TrainingMatrix:
    rows = []
    outcome = list(labels) or ([1, 0] * (n // 2 + 1))[:n]
    for i in range(n):
        label = outcome[i]
        base = [0.55] * len(FEATURE_NAMES)
        vector = tuple((v + (0.3 if label == 1 else -0.3)) for v in base)
        rows.append((vector, label))
    return build_training_matrix(rows, headers=FEATURE_NAMES)


class TestLearnCandidate:
    def test_returns_none_below_min_examples(self):
        matrix = _synthetic_matrix(5)
        assert learn_candidate(matrix, min_examples=20) is None

    def test_returns_candidate_above_min_examples(self):
        matrix = _synthetic_matrix(40)
        candidate = learn_candidate(matrix, min_examples=20)
        assert candidate is not None
        assert candidate["source"] == "logistic"
        assert candidate["version"].startswith("v2-logistic-")
        # values dict keys are ARUM weight-slot names (w1_severity ... w8_*).
        assert len(candidate["values"]) == 8
        assert "w1_severity" in candidate["values"]
        assert "w8_historical_rejection" in candidate["values"]
        assert candidate["trained_on"] == 40
        assert candidate["metrics"]["f1"] >= 0.0

    def test_values_are_l1_normalised(self):
        matrix = _synthetic_matrix(40)
        candidate = learn_candidate(matrix, min_examples=20)
        assert candidate is not None
        total = sum(candidate["values"].values())
        assert total == pytest.approx(1.0, abs=1e-6)

    def test_deterministic_seed_reproduces_version(self):
        matrix = _synthetic_matrix(40)
        first = learn_candidate(matrix, seed=42)
        second = learn_candidate(matrix, seed=42)
        assert first["version"] == second["version"]

    def test_one_class_only_still_fits(self):
        matrix = _synthetic_matrix(40, *([1] * 40))
        candidate = learn_candidate(matrix, min_examples=20)
        assert candidate is not None
        assert candidate["metrics"]["precision"] == 1.0


# --- store integration -------------------------------------------------------


class TestStoreFeedbackFlow:
    async def test_feedback_events_carry_finding_id(self):
        store = MemoryOrchestratorStore()
        write = FeedbackWrite(
            review_id="00000000-0000-0000-0000-000000000001",
            finding_id="00000000-0000-0000-0000-000000000002",
            repository_id=REPO_ID,
            category="security/xss",
            outcome=FeedbackOutcome.ACCEPTED,
        )
        await store.create_feedback(write)
        events = await store.repository_feedback_events(REPO_ID, max_age_days=30)
        assert len(events) == 1
        assert events[0].finding_id == write.finding_id
        assert events[0].outcome == FeedbackOutcome.ACCEPTED

    async def test_create_feedback_is_idempotent(self):
        store = MemoryOrchestratorStore()
        write_to = lambda: FeedbackWrite(  # noqa: E731
            review_id="00000000-0000-0000-0000-000000000001",
            finding_id="00000000-0000-0000-0000-000000000002",
            repository_id=REPO_ID,
            category="security/xss",
            outcome=FeedbackOutcome.REJECTED,
        )
        first = await store.create_feedback(write_to())
        second = await store.create_feedback(write_to())
        assert first == second
        assert len(store.feedback) == 1

    async def test_resolve_feedback_finding(self):
        store = MemoryOrchestratorStore()
        review_id = "00000000-0000-0000-0000-000000000001"
        finding_id = "00000000-0000-0000-0000-000000000002"
        found = await store.resolve_feedback_finding(review_id, finding_id)
        assert found is None  # no finding seeded yet
        store.findings.append(
            {
                "id": finding_id,
                "review_id": review_id,
                "repository_id": REPO_ID,
                "category": "security/xss",
            }
        )
        resolved = await store.resolve_feedback_finding(review_id, finding_id)
        assert resolved == (REPO_ID, "security/xss")

    async def test_learned_weights_round_trip(self):
        store = MemoryOrchestratorStore()
        assert await store.repository_learned_weights(REPO_ID) is None
        candidate = {"version": "v2-logistic-abc", "values": {"w1": 0.5}}
        await store.save_weight_candidate(REPO_ID, candidate)
        assert await store.repository_learned_weights(REPO_ID) == candidate
        # A second write overwrites rather than accumulates.
        newer = {"version": "v2-logistic-def", "values": {"w1": 0.7}}
        await store.save_weight_candidate(REPO_ID, newer)
        assert await store.repository_learned_weights(REPO_ID) == newer

    async def test_upsert_memory_version_returns_version(self):
        store = MemoryOrchestratorStore()
        imported_uuid = uuid.UUID
        version = await store.upsert_repository_memory(
            REPO_ID,
            {
                "categories": {},
                "decay_params": {"tau_days": 90, "lambda": 1.0, "horizon_days": 270},
                "events_count": 0,
            },
            decay_params={"tau_days": 90, "lambda": 1.0, "horizon_days": 270},
        )
        assert version >= 1
        assert store.memory[REPO_ID]["version"] == version
        assert imported_uuid == uuid.UUID  # noqa: F841
