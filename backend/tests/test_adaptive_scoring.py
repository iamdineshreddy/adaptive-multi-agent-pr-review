"""Unit tests for ARUM scoring + ranking (docs/ARUM.md §1, §10)."""

from __future__ import annotations

import uuid

from app.adaptive.features import ArumFeatures
from app.adaptive.reprolog import compute_inputs_hash
from app.adaptive.scoring import Decision, rank_decisions, utility
from app.adaptive.weights import ArumWeights

REVIEW_ID = str(uuid.uuid4())


def _features(f: dict[str, float], value: float = 1.0, **rest: float) -> ArumFeatures:
    return ArumFeatures(
        severity=rest.get("severity", value),
        confidence=rest.get("confidence", value),
        agent_agreement=rest.get("agent_agreement", value),
        historical_actionability=rest.get("historical_actionability", value),
        repository_relevance=rest.get("repository_relevance", value),
        context_relevance=rest.get("context_relevance", value),
        redundancy=rest.get("redundancy", value),
        historical_rejection=rest.get("historical_rejection", value),
    )


def _decision(
    finding_id: str, features: ArumFeatures, weights: ArumWeights
) -> Decision:
    return Decision(
        review_id=REVIEW_ID,
        finding_id=finding_id,
        group_id=None,
        arum_version=weights.version,
        features=features,
        utility=utility(features, weights),
        weights=weights,
        inputs_hash=compute_inputs_hash(finding_id, features.as_vector()),
    )


def test_utility_is_weighted_linear_combination() -> None:
    w = ArumWeights()
    # All features at 1.0: positives sum to the six positive weights, then
    # both penalties subtract.
    f = _features({})
    expected = (
        w.w1_severity
        + w.w2_confidence
        + w.w3_agent_agreement
        + w.w4_historical_actionability
        + w.w5_repository_relevance
        + w.w6_context_relevance
        - w.w7_redundancy
        - w.w8_historical_rejection
    )
    assert utility(f, w) == round(expected, 6)


def test_utility_penalises_redundancy_and_rejection() -> None:
    w = ArumWeights()
    clean = _features({}, redundancy=0.0, historical_rejection=0.0)
    penalised = _features({}, redundancy=0.5, historical_rejection=0.3)
    assert utility(penalised, w) < utility(clean, w)


def test_ranking_is_descending_and_deterministic() -> None:
    w = ArumWeights()
    strong = _decision("a", _features({}, value=1.0), w)
    weak = _decision("b", _features({}, value=0.1), w)
    ranked = rank_decisions([weak, strong])
    assert [d.finding_id for d in ranked] == ["a", "b"]
    assert ranked[0].utility > ranked[1].utility
    assert rank_decisions([weak, strong]) == rank_decisions([weak, strong])


def test_ties_break_by_finding_id() -> None:
    w = ArumWeights()
    a = _decision("bb", _features({}), w)
    b = _decision("aa", _features({}), w)
    assert [d.finding_id for d in rank_decisions([a, b])] == ["aa", "bb"]


def test_decision_is_serialisable_and_self_consistent() -> None:
    w = ArumWeights()
    decision = _decision("f1", _features({}, severity=0.8), w)
    payload = decision.to_dict()
    assert payload["arum_version"] == "v1"
    assert payload["features"]["severity"] == 0.8
    assert payload["utility"] == decision.utility
    assert payload["budget_cap"] is None  # filled by Phase 9
    assert payload["safety_gate"] is None
    assert payload["group_id"] is None


def test_inputs_hash_is_stable() -> None:
    h1 = compute_inputs_hash("review-id", ("a", 1.0), 5)
    h2 = compute_inputs_hash("review-id", ("a", 1.0), 5)
    assert h1 == h2
    assert h1 != compute_inputs_hash("review-id", ("a", 1.0), 6)
