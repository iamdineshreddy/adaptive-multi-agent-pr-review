"""Unit tests for ARUM feature extraction (docs/ARUM.md §2)."""

from __future__ import annotations

import pytest

from app.adaptive.features import (
    FEATURE_NAMES,
    agent_agreement,
    confidence_score,
    disagreement_variance,
    extract_features,
    historical_shares,
    redundancy_term,
    severity_score,
)
from app.consolidation.datatypes import FindingWrite


def _finding(
    severity: str = "MEDIUM", confidence: float = 0.5, **overrides: object
) -> FindingWrite:
    raw: dict[str, object] = {
        "id": "11111111-1111-4111-8111-111111111111",
        "agent_key": "security",
        "agent_id": "00000000-0000-4000-8000-000000000001",
        "review_id": "22222222-2222-4222-8222-222222222222",
        "repository_id": "33333333-3333-4333-8333-333333333333",
        "file_path": "app/auth.py",
        "line_start": None,
        "line_end": None,
        "category": "security/xss",
        "severity": severity,
        "confidence": confidence,
        "title": "t",
        "description": "d",
        "evidence": {},
        "suggested_fix": None,
        "reason_summary": "r",
        "duplicate_group": None,
    }
    raw.update(overrides)
    return FindingWrite(**raw)  # type: ignore[arg-type]


def test_severity_ordinal_scale() -> None:
    assert severity_score("critical") == 1.0
    assert severity_score("high") == 0.8
    assert severity_score("medium") == 0.5
    assert severity_score("low") == 0.2
    assert severity_score("info") == 0.05
    assert severity_score("HIGH") == 0.8  # case-insensitive
    assert severity_score("nonsense") == 0.0


def test_confidence_is_clamped() -> None:
    assert confidence_score(0.9) == 0.9
    assert confidence_score(1.5) == 1.0
    assert confidence_score(-0.2) == 0.0


def test_redundancy_term() -> None:
    assert redundancy_term(1) == 0.0
    assert redundancy_term(2) == pytest.approx(0.5)
    assert redundancy_term(4) == pytest.approx(0.75)


def test_agent_agreement_baseline() -> None:
    assert agent_agreement(1, 1) == 0.5
    assert agent_agreement(2, 2) == 1.0
    assert agent_agreement(3, 2) == 1.0
    # Same agent duplicated in the group does not count as cross-agent.
    assert agent_agreement(2, 1) == 0.5


def test_agent_agreement_reduced_on_disagreement() -> None:
    assert agent_agreement(2, 2, variance=0.1) == 1.0  # below threshold
    at = agent_agreement(2, 2, variance=0.6)
    assert 1.0 > at >= 0.5  # discounted by up to 50%
    at_max = agent_agreement(2, 2, variance=1.0)
    assert at_max == pytest.approx(0.5)


def test_disagreement_variance_from_members() -> None:
    a = _finding(severity="LOW", confidence=0.2)
    b = _finding(severity="HIGH", confidence=0.9)
    assert disagreement_variance([a, b]) > 0.0
    same = [_finding(), _finding()]
    assert disagreement_variance(same) == 0.0
    assert disagreement_variance([a]) == 0.0


def test_historical_shares_reads_memory_and_falls_back_to_prefix() -> None:
    memory = {
        "categories": {
            "security": {"accepted_share": 0.7, "rejected_share": 0.1},
        }
    }
    # exact category -> own entry
    assert historical_shares(
        {
            "categories": {
                "security/xss": {"accepted_share": 0.9, "rejected_share": 0.0}
            }
        },
        "security/xss",
    ) == {"accepted": 0.9, "rejected": 0.0}
    # child -> parent prefix fallback
    assert historical_shares(memory, "security/xss") == {
        "accepted": 0.7,
        "rejected": 0.1,
    }
    assert historical_shares({}, "security/xss") == {"accepted": 0.0, "rejected": 0.0}


def test_extract_features_single_agent_candidate() -> None:
    features = extract_features(
        _finding(severity="HIGH", confidence=0.9),
        member_count=1,
        distinct_agents=1,
    )
    values = features.as_dict()
    assert values["severity"] == 0.8
    assert values["confidence"] == 0.9
    assert values["agent_agreement"] == 0.5
    assert values["redundancy"] == 0.0
    # Memory-dependent features default to zero in Phase 8.
    assert values["historical_actionability"] == 0.0
    assert values["repository_relevance"] == 0.0
    assert values["context_relevance"] == 0.0
    assert values["historical_rejection"] == 0.0


def test_extract_features_grouped_candidate() -> None:
    features = extract_features(
        _finding(severity="HIGH", confidence=0.9),
        member_count=2,
        distinct_agents=2,
        memory={
            "categories": {"security": {"accepted_share": 0.8, "rejected_share": 0.2}}
        },
    )
    assert features.agent_agreement == 1.0
    assert features.redundancy == pytest.approx(0.5)
    assert features.historical_actionability == pytest.approx(0.8)
    assert features.historical_rejection == pytest.approx(0.2)


def test_feature_names_match_fields() -> None:
    assert len(FEATURE_NAMES) == 8
    vector = extract_features(_finding(), member_count=1, distinct_agents=1).as_vector()
    assert len(vector) == 8  # training-matrix column order is stable
