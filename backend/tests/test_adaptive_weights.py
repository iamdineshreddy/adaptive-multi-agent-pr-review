"""Unit tests for ARUM weights (docs/ARUM.md §3)."""

from __future__ import annotations

import pytest

from app.adaptive.weights import (
    ArumConfigurationError,
    ArumWeights,
    load_weights,
)


def test_default_weights_match_heuristic_prior() -> None:
    w = ArumWeights()
    assert w.w1_severity == 0.30
    assert w.w2_confidence == 0.20
    assert w.w3_agent_agreement == 0.15
    assert w.w4_historical_actionability == 0.15
    assert w.w5_repository_relevance == 0.10
    assert w.w6_context_relevance == 0.10
    assert w.w7_redundancy == 0.08
    assert w.w8_historical_rejection == 0.10
    assert w.version == "v1"


def test_as_dict_and_vector_are_json_safe() -> None:
    w = ArumWeights()
    assert w.as_dict()["version"] == "v1"
    assert len(w.as_vector()) == 8
    assert all(isinstance(v, float) for v in w.as_vector())


def test_load_weights_ignores_overrides_without_them() -> None:
    w = load_weights()
    assert w == ArumWeights()
    assert load_weights(version="v1") == w


def test_load_weights_unknown_version_raises() -> None:
    with pytest.raises(ArumConfigurationError, match="unknown ARUM weights version"):
        load_weights(version="v99")


def test_repo_overrides_are_applied() -> None:
    base = load_weights()
    repo = load_weights(
        repo_overrides={"w1_severity": 0.5, "w8_historical_rejection": 0.01}
    )
    assert repo.w1_severity == 0.5
    assert repo.w8_historical_rejection == 0.01
    assert repo.w2_confidence == base.w2_confidence  # untouched


def test_repo_override_unknown_key_raises() -> None:
    with pytest.raises(ArumConfigurationError, match="unknown ARUM weight key"):
        load_weights(repo_overrides={"w1_servery": 0.5})


def test_repo_override_negative_raises() -> None:
    with pytest.raises(ArumConfigurationError, match="finite non-negative"):
        load_weights(repo_overrides={"w1_severity": -0.5})


def test_overrides_return_new_instance() -> None:
    original = ArumWeights()
    changed = original.with_overrides({"w1_severity": 0.4})
    assert original.w1_severity == 0.30
    assert changed.w1_severity == 0.40
