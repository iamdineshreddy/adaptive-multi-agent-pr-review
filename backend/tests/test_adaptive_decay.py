"""Unit tests for temporal decay (docs/ARUM.md §6)."""

from __future__ import annotations

import math

import pytest

from app.adaptive.decay import decay_weight, weighted_share


def test_fresh_event_full_weight() -> None:
    assert decay_weight(0.0) == 1.0


def test_decay_is_exponential() -> None:
    assert decay_weight(90.0) == pytest.approx(math.exp(-1.0))
    assert decay_weight(270.0) == pytest.approx(math.exp(-3.0))  # negligible
    assert decay_weight(45.0, tau_days=45.0) == pytest.approx(math.exp(-1.0))


def test_custom_parameters() -> None:
    # Faster decay (lam=2) at the same age decays more.
    assert decay_weight(45.0, lam=2.0) < decay_weight(45.0, lam=1.0)
    # Longer tau keeps older events relatively fresh.
    assert decay_weight(90.0, tau_days=180.0) == pytest.approx(math.exp(-0.5))


def test_invalid_arguments_raise() -> None:
    with pytest.raises(ValueError):
        decay_weight(-1.0)
    with pytest.raises(ValueError):
        decay_weight(1.0, tau_days=0.0)
    with pytest.raises(ValueError):
        decay_weight(1.0, lam=-0.1)


def test_weighted_share_no_evidence_is_zero() -> None:
    assert weighted_share([]) == 0.0


def test_weighted_share_fresh_events_recent_dominates() -> None:
    # One accepted recently, one rejected long ago: share leans positive.
    shares = weighted_share([(5.0, True), (400.0, False)])
    assert shares > 0.5


def test_weighted_share_equals_mean_for_zero_age() -> None:
    events = [(0.0, True), (0.0, False), (0.0, True)]
    assert weighted_share(events) == pytest.approx(2 / 3)
