"""Label-rule matrix tests (docs/EXPERIMENTS.md §1, docs/Auth.md)."""

from __future__ import annotations

import pytest

from app.experiments.labeling import (
    RULE_ACCEPTANCE_WITHOUT_CHANGE,
    RULE_CHANGE_UNRELATED,
    RULE_CHANGED_UNCLASSIFIED,
    RULE_EXPLICIT,
    RULE_NO_RESPONSE,
    RULE_NOT_REVIEW,
    RULE_PARTIAL_MODIFIED,
    RULE_VERBATIM,
    LabelingError,
    label_outcome,
)


def _record(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "is_review_comment": True,
        "related_to_feedback": True,
        "implemented_later": False,
        "implementation": None,
        "explicit_outcome": None,
    }
    base.update(overrides)
    return base


def test_opinion_comment_is_excluded() -> None:
    result = label_outcome(_record(is_review_comment=False))
    assert result.included is False
    assert result.label is None
    assert result.rule == RULE_NOT_REVIEW


def test_change_unrelated_to_comment_is_excluded() -> None:
    result = label_outcome(_record(related_to_feedback=False))
    assert result.included is False
    assert result.rule == RULE_CHANGE_UNRELATED


@pytest.mark.parametrize(
    ("extra", "expected"),
    [
        (
            {"implemented_later": True, "implementation": "verbatim"},
            ("FIXED", RULE_VERBATIM),
        ),
        (
            {"implemented_later": True, "implementation": "partial"},
            ("MODIFIED", RULE_PARTIAL_MODIFIED),
        ),
        (
            {"implemented_later": True, "implementation": "modified"},
            ("MODIFIED", RULE_PARTIAL_MODIFIED),
        ),
    ],
)
def test_change_based_labels(
    extra: dict[str, object], expected: tuple[str, str]
) -> None:
    result = label_outcome(_record(**extra))
    assert (result.label, result.rule) == expected
    assert result.included is True


def test_changed_but_unclassified_is_excluded_not_accepted() -> None:
    """Code changed with no implementation evidence is never auto-accepted."""
    result = label_outcome(_record(implemented_later=True))
    assert result.included is False
    assert result.label is None
    assert result.rule == RULE_CHANGED_UNCLASSIFIED


@pytest.mark.parametrize("outcome", ["dismissed", "rejected", "ignored"])
def test_explicit_negative_wins_over_change(outcome: str) -> None:
    result = label_outcome(
        _record(
            implemented_later=True, implementation="verbatim", explicit_outcome=outcome
        )
    )
    assert (result.label, result.rule) == (outcome.upper(), RULE_EXPLICIT)


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [("accepted", "ACCEPTED"), ("fixed", "FIXED")],
)
def test_explicit_acceptance(outcome: str, expected: str) -> None:
    result = label_outcome(_record(implemented_later=True, explicit_outcome=outcome))
    assert (result.label, result.rule) == (expected, RULE_EXPLICIT)


def test_explicit_acceptance_without_change_is_excluded() -> None:
    result = label_outcome(_record(explicit_outcome="accepted"))
    assert result.included is False
    assert result.label is None
    assert result.rule == RULE_ACCEPTANCE_WITHOUT_CHANGE


def test_no_change_no_response_is_ignored() -> None:
    result = label_outcome(_record())
    assert (result.label, result.rule) == ("IGNORED", RULE_NO_RESPONSE)


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [("dismissed", "DISMISSED"), ("rejected", "REJECTED"), ("ignored", "IGNORED")],
)
def test_explicit_negative_without_change(outcome: str, expected: str) -> None:
    result = label_outcome(_record(explicit_outcome=outcome))
    assert (result.label, result.rule) == (expected, RULE_EXPLICIT)


@pytest.mark.parametrize(
    "bad",
    [
        {"explicit_outcome": "unknown"},
        {"implemented_later": True, "implementation": "unknown"},
    ],
)
def test_unknown_tombstones_raise(bad: dict[str, object]) -> None:
    with pytest.raises(LabelingError):
        label_outcome(_record(**bad))
