"""Temporal-decay memory snapshot builder (docs/ARUM.md §2, §6, FR-5.3).

``memory.py`` turns ``developer_feedback`` events into the cached per-category
``accepted_share`` / ``rejected_share`` that feed ``historical_actionability``
and ``historical_rejection``. The decay math is ``decay.weighted_share``; this
suite pins the aggregation, horizon cutoff and parent-prefix rollup.
"""

from __future__ import annotations

import math

import pytest

from app.adaptive.memory import (
    FeedbackEvent,
    aggregate_category_shares,
    build_memory_snapshot,
)
from app.models.enums import FeedbackOutcome

TAU = 90.0


def _event(category: str, outcome: str, age_days: float) -> FeedbackEvent:
    return FeedbackEvent(
        category=category, outcome=FeedbackOutcome(outcome), age_days=age_days
    )


def test_fresh_accepted_events_dominate_actionability() -> None:
    events = [
        _event("security/xss", "ACCEPTED", 1.0),
        _event("security/xss", "REJECTED", 1.0),
        _event("security/xss", "ACCEPTED", 1.0),
    ]
    shares = aggregate_category_shares(events, tau_days=TAU, lam=1.0)
    entry = shares["security/xss"]
    # Equal age -> decay weights cancel: 2 accepted of 3.
    assert entry["accepted_share"] == pytest.approx(2 / 3, abs=1e-4)
    assert entry["rejected_share"] == pytest.approx(1 / 3, abs=1e-4)


def test_recent_evidence_weights_more_than_old() -> None:
    events = [
        _event("security", "ACCEPTED", 1.0),
        _event("security", "REJECTED", 269.0),  # survived the 3*tau horizon
    ]
    shares = aggregate_category_shares(events, tau_days=TAU, lam=1.0)["security"]
    # exp(-1/90) >> exp(-269/90) = exp(-2.989)
    fresh = math.exp(-1.0 / TAU)
    old = math.exp(-269.0 / TAU)
    expected = fresh / (fresh + old)
    assert shares["accepted_share"] == pytest.approx(expected, abs=1e-4)


def test_events_beyond_horizon_are_excluded() -> None:
    events = [
        _event("security", "ACCEPTED", 1.0),
        _event("security", "REJECTED", 271.0),  # >= 3*tau -> ignored
    ]
    shares = aggregate_category_shares(events, tau_days=TAU, lam=1.0)["security"]
    # The fresh ACCEPTED event stays; the old REJECTED is outside the horizon.
    assert shares["accepted_share"] == pytest.approx(1.0)
    assert shares["rejected_share"] == pytest.approx(0.0)


def test_child_events_roll_up_to_parent_prefix() -> None:
    events = [
        _event("security/xss", "ACCEPTED", 1.0),
        _event("security/xss", "REJECTED", 1.0),
    ]
    shares = aggregate_category_shares(events, tau_days=TAU, lam=1.0)
    assert shares["security/xss"]["accepted_share"] == pytest.approx(0.5)
    # features.historical_shares falls back to the parent prefix.
    assert shares["security"]["accepted_share"] == pytest.approx(0.5)
    assert shares["security"]["rejected_share"] == pytest.approx(0.5)


def test_mixed_outcomes_are_neither_accept_nor_reject() -> None:
    events = [
        _event("security/xss", "ACCEPTED", 1.0),
        _event("security/xss", "DISCUSSION", 1.0),
        _event("security/xss", "MODIFIED", 1.0),
    ]
    shares = aggregate_category_shares(events, tau_days=TAU, lam=1.0)["security/xss"]
    # Equal age -> weights cancel: 1 accepted of 3; no rejections at all.
    assert shares["accepted_share"] == pytest.approx(1 / 3, abs=1e-4)
    assert shares["rejected_share"] == pytest.approx(0.0)


def test_empty_evidence_yields_no_categories() -> None:
    assert aggregate_category_shares([], tau_days=TAU, lam=1.0) == {}


def test_snapshot_shape_used_by_features() -> None:
    events = [
        _event("quality/maintainability", "FIXED", 3.0),
        _event("quality/maintainability", "DISMISSED", 40.0),
    ]
    snapshot = build_memory_snapshot(events, tau_days=TAU, lam=1.0)
    assert set(snapshot) >= {"categories", "decay_params", "events_count"}
    assert snapshot["events_count"] == 2
    assert snapshot["decay_params"]["tau_days"] == TAU
    entry = snapshot["categories"]["quality/maintainability"]
    assert set(entry) == {"accepted_share", "rejected_share"}
    assert 0.0 <= entry["accepted_share"] <= 1.0
    assert 0.0 <= entry["rejected_share"] <= 1.0
    # FIXED counts as actionability; DISMISSED as rejection (never both).
    assert entry["accepted_share"] > entry["rejected_share"]


def test_negative_age_is_disallowed_by_decay_but_filtered_here() -> None:
    events = [_event("security", "ACCEPTED", -1.0)]
    assert aggregate_category_shares(events, tau_days=TAU, lam=1.0) == {}
