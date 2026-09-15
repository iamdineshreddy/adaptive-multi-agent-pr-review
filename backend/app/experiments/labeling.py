"""Research label mapping for the code-review corpus (docs/EXPERIMENTS.md §1).

The rigour guard from ``docs/Auth.md`` is enforced here: "code changed" is
*never* by itself evidence of developer acceptance. Every positive label
(``ACCEPTED`` / ``FIXED``) requires either a suggestion-matching later
implementation or an explicit thread outcome, and changes without relation to
the comment are always excluded from acceptance accounting.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

RESEARCH_LABELS = frozenset(
    {"ACCEPTED", "FIXED", "MODIFIED", "DISMISSED", "REJECTED", "IGNORED"}
)
POSITIVE_LABELS = frozenset({"ACCEPTED", "FIXED"})
REAL_LABELS = frozenset({"ACCEPTED", "FIXED", "MODIFIED"})
NON_ISSUE_LABELS = frozenset({"DISMISSED", "REJECTED", "IGNORED"})

VALID_SEVERITIES = frozenset({"info", "low", "medium", "high", "critical"})
VALID_EXPLICIT_OUTCOMES = frozenset(
    {"accepted", "fixed", "dismissed", "rejected", "ignored"}
)
VALID_IMPLEMENTATIONS = frozenset({"verbatim", "partial", "modified"})

RULE_NOT_REVIEW = "not_review"
RULE_CHANGE_UNRELATED = "change_unrelated"
RULE_CHANGED_UNCLASSIFIED = "changed_unclassified"
RULE_ACCEPTANCE_WITHOUT_CHANGE = "acceptance_without_change"
RULE_VERBATIM = "verbatim"
RULE_PARTIAL_MODIFIED = "partial_modified"
RULE_EXPLICIT = "explicit"
RULE_NO_RESPONSE = "no_response"
RULE_EXCLUDED = "excluded"


class LabelingError(ValueError):
    """Malformed outcome signal (unknown tombstone value, etc.)."""


@dataclass(frozen=True)
class LabelResult:
    """Outcome of one raw record through the research label rules.

    ``label`` is a research label only when ``included``; excluded records are
    dropped from acceptance accounting (docs/EXPERIMENTS.md §1) and ``rule``
    records which rule fired so exclusions are auditable.
    """

    label: str | None
    rule: str
    included: bool


def label_outcome(record: Mapping[str, Any]) -> LabelResult:
    """Apply the §1 decision table in documented precedence order.

    Precedence: comment-kind gate, then change-relation gate, then
    implemented/not-implemented branches. Where an explicit thread outcome is
    present it wins over change-derived evidence (the developer's own record
    beats inference), except that acceptance claims without any later
    implementation are excluded rather than auto-accepted.
    """
    if not bool(record.get("is_review_comment", True)):
        return LabelResult(None, RULE_NOT_REVIEW, False)
    if not bool(record.get("related_to_feedback", True)):
        return LabelResult(None, RULE_CHANGE_UNRELATED, False)

    implemented = bool(record.get("implemented_later"))
    explicit = record.get("explicit_outcome")
    if explicit is not None and explicit not in VALID_EXPLICIT_OUTCOMES:
        raise LabelingError(f"unknown explicit_outcome {explicit!r}")

    if implemented:
        if explicit in ("dismissed", "rejected", "ignored"):
            return LabelResult(_explicit_label(explicit), RULE_EXPLICIT, True)
        if explicit in ("accepted", "fixed"):
            return LabelResult(
                "FIXED" if explicit == "fixed" else "ACCEPTED", RULE_EXPLICIT, True
            )
        implementation = record.get("implementation")
        if implementation == "verbatim":
            return LabelResult("FIXED", RULE_VERBATIM, True)
        if implementation in ("partial", "modified"):
            return LabelResult("MODIFIED", RULE_PARTIAL_MODIFIED, True)
        if implementation is None:
            return LabelResult(None, RULE_CHANGED_UNCLASSIFIED, False)
        raise LabelingError(f"unknown implementation {implementation!r}")

    if explicit in ("dismissed", "rejected", "ignored"):
        return LabelResult(_explicit_label(explicit), RULE_EXPLICIT, True)
    if explicit in ("accepted", "fixed"):
        return LabelResult(None, RULE_ACCEPTANCE_WITHOUT_CHANGE, False)
    return LabelResult("IGNORED", RULE_NO_RESPONSE, True)


def _explicit_label(outcome: str) -> str:
    """Map an explicit thread outcome to its research label."""
    return {
        "accepted": "ACCEPTED",
        "fixed": "FIXED",
        "dismissed": "DISMISSED",
        "rejected": "REJECTED",
        "ignored": "IGNORED",
    }[outcome]
