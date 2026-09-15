"""Mode-switch semantics tests (docs/EXPERIMENTS.md §2, §4)."""

from __future__ import annotations

from dataclasses import asdict

from app.experiments.modes import (
    ABLATIONS,
    BASELINES,
    DEFAULT_BUDGET,
    SINGLE_REVIEWER,
    all_modes,
)


def _b5_fields() -> dict[str, object]:
    return {
        key: value
        for key, value in asdict(BASELINES["B5"]).items()
        if key not in ("key", "label")
    }


def test_b1_single_reviewer_publishes_all_without_selection() -> None:
    b1 = BASELINES["B1"]
    assert b1.single_reviewer == SINGLE_REVIEWER
    assert b1.dedup_only is True
    assert b1.adaptive is False
    assert b1.budget is None


def test_b2_no_adaptive_selection() -> None:
    b2 = BASELINES["B2"]
    assert b2.dedup_only is True
    assert b2.adaptive is False
    assert b2.budget is None


def test_b3_static_weights_with_budget() -> None:
    b3 = BASELINES["B3"]
    assert b3.adaptive is True
    assert b3.learned is False
    assert b3.memory is False
    assert b3.budget == DEFAULT_BUDGET
    assert b3.gates is False


def test_b4_learned_weights_with_memory() -> None:
    b4 = BASELINES["B4"]
    assert b4.learned is True
    assert b4.memory is True
    assert b4.budget == DEFAULT_BUDGET
    assert b4.gates is False


def test_b5_full_system() -> None:
    b5 = BASELINES["B5"]
    assert b5.learned is True
    assert b5.memory is True
    assert b5.gates is True
    assert b5.scope == "all"
    assert b5.variance is True
    assert b5.redundancy_filter is True


def test_each_ablation_removes_exactly_one_b5_component() -> None:
    expected_diffs = {
        "A1": {"learned", "memory"},
        "A2": {"memory"},
        "A3": {"redundancy_filter"},
        "A4": {"budget"},
        "A5": {"scope"},
        "A6": {"variance"},
    }
    for key, expected in expected_diffs.items():
        ablation = ABLATIONS[key]
        base = _b5_fields()
        actual = {
            name: value
            for name, value in asdict(ablation).items()
            if name not in ("key", "label") and value != base[name]
        }
        assert set(actual) == expected, f"{key} altered {sorted(actual)}"


def test_all_modes_order_baselines_then_ablations() -> None:
    keys = [m.key for m in all_modes()]
    assert keys == ["B1", "B2", "B3", "B4", "B5", "A1", "A2", "A3", "A4", "A5", "A6"]
