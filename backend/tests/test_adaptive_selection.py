"""Unit tests for the review budget controller + safety gates (docs/ARUM.md §7).

The policy is: rank by ARUM utility, fill the cap, and let the non-negotiable
gates override ranking (critical -> mandatory; high+high -> protected; low+low+
high-redundancy -> suppressed). Determinism matters — the annotations land in
the reproducibility log.
"""

from __future__ import annotations

import math
import uuid

import pytest

from app.adaptive.features import ArumFeatures
from app.adaptive.reprolog import compute_inputs_hash
from app.adaptive.scoring import Decision, rank_decisions, utility
from app.adaptive.selection import (
    GATE_REASON_CRITICAL,
    GATE_REASON_PROTECTED,
    GATE_REASON_SUPPRESSED,
    select_decisions,
)
from app.adaptive.weights import ArumWeights

REVIEW_ID = str(uuid.uuid4())
WEIGHTS = ArumWeights()


def _decision(
    finding_id: str,
    *,
    severity: float,
    confidence: float,
    redundancy: float = 0.0,
    agent_agreement: float = 0.5,
) -> Decision:
    features = ArumFeatures(
        severity=severity,
        confidence=confidence,
        agent_agreement=agent_agreement,
        historical_actionability=0.0,
        repository_relevance=0.0,
        context_relevance=0.0,
        redundancy=redundancy,
        historical_rejection=0.0,
    )
    return Decision(
        review_id=REVIEW_ID,
        finding_id=finding_id,
        group_id=None,
        arum_version=WEIGHTS.version,
        features=features,
        utility=utility(features, WEIGHTS),
        weights=WEIGHTS,
        inputs_hash=compute_inputs_hash(finding_id, features.as_vector()),
    )


def test_budget_fills_by_rank_and_suppresses_the_rest() -> None:
    decisions = [
        _decision("a", severity=0.8, confidence=0.9),  # protected, top utility
        _decision("b", severity=0.5, confidence=0.5),
        _decision("c", severity=0.5, confidence=0.5),  # ties with b
        _decision("d", severity=0.1, confidence=0.1),  # lowest
    ]
    result = select_decisions(decisions, cap=2)
    assert result.cap == 2
    assert result.selected_count == 2
    assert result.truncated_by_budget == 2
    assert result.protected_count == 1
    selected = {d.finding_id for d in result.decisions if d.selected}
    assert selected == {"a", "b"}
    # Truncated decisions are annotated (budget_cap set, no gate reason).
    truncated = [d for d in result.decisions if not d.selected]
    assert len(truncated) == 2
    assert all(d.budget_cap == 2 for d in truncated)
    assert all(d.safety_gate is None for d in truncated)
    # Trace order preserves ARUM rank.
    assert [d.finding_id for d in result.decisions] == [
        d.finding_id for d in rank_decisions(decisions)
    ]


def test_critical_severity_mandatory_exceeds_cap() -> None:
    decisions = [
        _decision("c1", severity=1.0, confidence=0.2),
        _decision("c2", severity=1.0, confidence=0.2),
        _decision("c3", severity=1.0, confidence=0.2),
        _decision("c4", severity=1.0, confidence=0.2),
        _decision("low", severity=0.1, confidence=0.1),
    ]
    result = select_decisions(decisions, cap=2)
    assert result.cap == 2
    # The critical gate outranks the budget: all 4 criticals publish (4 > 2),
    # which leaves the low-ranked candidate with no budget slot.
    assert result.mandatory_count == 4
    assert result.selected_count == 4
    assert result.truncated_by_budget == 1
    criticals = [d for d in result.decisions if d.safety_gate == GATE_REASON_CRITICAL]
    assert len(criticals) == 4
    assert all(d.selected for d in criticals)
    assert all(d.budget_cap == 2 for d in criticals)
    assert result.decisions[-1].finding_id == "low"  # truncated


def test_high_severity_high_confidence_protected_from_truncation() -> None:
    decisions = [
        _decision("protected", severity=0.8, confidence=0.9),
        _decision("plain", severity=0.5, confidence=0.4),
    ]
    result = select_decisions(decisions, cap=1)
    assert result.protected_count == 1
    protected = [d for d in result.decisions if d.safety_gate == GATE_REASON_PROTECTED]
    assert len(protected) == 1
    assert protected[0].finding_id == "protected"
    assert protected[0].selected is True
    assert [d.finding_id for d in result.decisions if d.selected] == ["protected"]


def test_low_value_high_redundancy_suppressed() -> None:
    decisions = [
        _decision(
            "dup-triple",
            severity=0.1,
            confidence=0.2,
            redundancy=2 / 3,  # duplicate seen 3 times
        ),
        _decision("normal", severity=0.5, confidence=0.5),
    ]
    result = select_decisions(decisions, cap=5)
    assert result.suppressed_by_gate == 1
    suppressed = [
        d for d in result.decisions if d.safety_gate == GATE_REASON_SUPPRESSED
    ]
    assert len(suppressed) == 1
    assert suppressed[0].finding_id == "dup-triple"
    assert suppressed[0].selected is False
    # The gate fires even though there is budget headroom.
    assert result.selected_count == 1
    assert result.truncated_by_budget == 0


def test_gate_thresholds_are_configurable() -> None:
    decisions = [
        _decision("x", severity=0.8, confidence=0.7),  # not high-high at 0.8
    ]
    strict = select_decisions(decisions, cap=1, high_confidence=0.8)
    assert strict.protected_count == 0
    relaxed = select_decisions(decisions, cap=1, high_confidence=0.6)
    assert relaxed.protected_count == 1
    assert relaxed.decisions[0].safety_gate == GATE_REASON_PROTECTED


def test_selection_is_deterministic() -> None:
    decisions = [
        _decision("a", severity=0.5, confidence=0.5),
        _decision("b", severity=0.6, confidence=0.6),
        _decision("c", severity=0.2, confidence=0.2),
        _decision("d", severity=0.9, confidence=0.4),
    ]
    first = select_decisions(decisions, cap=2)
    second = select_decisions(decisions, cap=2)
    assert first == second
    assert [d.finding_id for d in first.decisions] == [
        d.finding_id for d in second.decisions
    ]


def test_ties_break_by_finding_id() -> None:
    tied = [
        _decision("bb", severity=0.5, confidence=0.5),
        _decision("aa", severity=0.5, confidence=0.5),
    ]
    result = select_decisions(tied, cap=1)
    assert [d.finding_id for d in result.decisions if d.selected] == ["aa"]


def test_invalid_arguments_raise() -> None:
    decision = _decision("x", severity=0.5, confidence=0.5)
    with pytest.raises(ValueError, match="non-negative"):
        select_decisions([decision], cap=-1)
    with pytest.raises(ValueError, match="within \\[0, 1\\]"):
        select_decisions([decision], cap=1, high_confidence=1.5)


def test_utility_monotonic_with_severity() -> None:
    low = utility(
        ArumFeatures(
            severity=0.1,
            confidence=0.5,
            agent_agreement=0.5,
            historical_actionability=0.0,
            repository_relevance=0.0,
            context_relevance=0.0,
            redundancy=0.0,
            historical_rejection=0.0,
        ),
        WEIGHTS,
    )
    high = utility(
        ArumFeatures(
            severity=0.8,
            confidence=0.5,
            agent_agreement=0.5,
            historical_actionability=0.0,
            repository_relevance=0.0,
            context_relevance=0.0,
            redundancy=0.0,
            historical_rejection=0.0,
        ),
        WEIGHTS,
    )
    assert high > low
    assert math.isfinite(low)
    assert math.isfinite(high)
