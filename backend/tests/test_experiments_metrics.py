"""Selection-layer metric tests (docs/ARUM.md §5)."""

from __future__ import annotations

import pytest

from app.experiments.metrics import (
    Metrics,
    RunOutcome,
    compute_metrics,
    outcome_total,
)


def _outcome(**overrides: int) -> RunOutcome:
    base: dict[str, int] = {
        "mode_key": "T",
        "raw_count": 10,
        "member_rows": 20,
        "group_count": 15,
        "published_count": 6,
        "positives": 8,
        "published_positives": 5,
        "accepted_published": 3,
        "non_real": 5,
        "suppressed_non_real": 2,
        "actionable_published": 4,
        "actionable_known": 6,
        "outcome_total": 0,
    }
    base.update(overrides)
    return RunOutcome(**base)


def test_precision_recall_f1() -> None:
    metrics = compute_metrics(_outcome())
    # precision 3/6, recall 5/8, f1 = 2*0.5*0.625/(1.125)
    assert metrics.precision == pytest.approx(0.5)
    assert metrics.recall == pytest.approx(0.625)
    assert metrics.f1 == pytest.approx(2 * 0.5 * 0.625 / 1.125)


def test_false_positive_rate() -> None:
    # suppressed non-issues 2 / non-issues 5
    assert compute_metrics(_outcome()).false_positive_rate == pytest.approx(0.4)


def test_comment_and_redundancy_reduction() -> None:
    metrics = compute_metrics(_outcome())
    assert metrics.comment_reduction == pytest.approx(0.4)  # (10-6)/10
    assert metrics.redundancy_reduction == pytest.approx(0.25)  # (20-15)/20


def test_acceptance_rate_over_published() -> None:
    # ACCEPTED+FIXED published 3 ÷ published 6
    assert compute_metrics(_outcome()).acceptance_rate == pytest.approx(0.5)


def test_actionability() -> None:
    # published actionable 4 / 6 with known flag
    assert compute_metrics(_outcome()).actionability == pytest.approx(4 / 6)


def test_actionability_none_when_corpus_has_no_flags() -> None:
    metrics = compute_metrics(_outcome(actionable_known=0))
    assert metrics.actionability is None


def test_zero_denominators_are_zero_not_errors() -> None:
    metrics = compute_metrics(
        _outcome(
            raw_count=0,
            member_rows=0,
            group_count=0,
            published_count=0,
            positives=0,
            published_positives=0,
            accepted_published=0,
            non_real=0,
            suppressed_non_real=0,
            actionable_published=0,
            actionable_known=0,
        )
    )
    assert metrics == Metrics(
        precision=0.0,
        recall=0.0,
        f1=0.0,
        false_positive_rate=0.0,
        comment_reduction=0.0,
        redundancy_reduction=0.0,
        acceptance_rate=0.0,
        actionability=None,
    )


def test_outcome_total_only_counts_labelled_outcomes() -> None:
    counts = {
        "ACCEPTED": 2,
        "FIXED": 1,
        "MODIFIED": 1,
        "IGNORED": 2,
        "DISMISSED": 1,
        "REJECTED": 1,
    }
    assert outcome_total(counts) == 8
