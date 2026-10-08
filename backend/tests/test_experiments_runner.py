"""End-to-end runner tests over hand-built corpora (EXPERIMENTS.md §2, §5)."""

from __future__ import annotations

import pytest

from app.experiments.corpus import build_corpus
from app.experiments.modes import BASELINES, replace
from app.experiments.runner import LEARNED_WEIGHTS_VERSION, run_mode


def _rec(
    review_id: str,
    file_path: str,
    line_start: int,
    category: str,
    severity: str,
    confidence: float,
    reviewer: str,
    *,
    implemented: bool = False,
    implementation: str | None = None,
    explicit: str | None = None,
    actionable: bool = True,
    round_no: int = 1,
    is_comment: bool = True,
    related: bool = True,
    memory: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "review_id": review_id,
        "file_path": file_path,
        "line_start": line_start,
        "line_end": line_start,
        "category": category,
        "severity": severity,
        "confidence": confidence,
        "reviewer": reviewer,
        "round": round_no,
        "comment": f"{reviewer}: {category} @ {file_path}:{line_start}",
        "suggested_fix": "fix" if actionable else None,
        "actionable": actionable,
        "is_review_comment": is_comment,
        "related_to_feedback": related,
        "implemented_later": implemented,
        "implementation": implementation,
        "explicit_outcome": explicit,
        "memory": memory or {},
    }


# --- B2: dedup-only, everything published (EXPERIMENTS.md §2) -------------


def test_b2_publishes_everything_with_dedup_only() -> None:
    corpus = build_corpus(
        [
            _rec(
                "v1",
                "a.py",
                10,
                "security/xss",
                "high",
                0.9,
                "security",
                implemented=True,
                implementation="verbatim",
            ),
            _rec(
                "v1",
                "a.py",
                11,
                "security/xss",
                "high",
                0.85,
                "quality",
                implemented=True,
                implementation="verbatim",
            ),
            _rec("v1", "b.py", 40, "quality/maintainability", "medium", 0.6, "quality"),
            _rec(
                "v1", "a.py", 90, "none", "info", 0.9, "primary", is_comment=False
            ),  # excluded, must not be a candidate
        ],
        source="t",
    )
    payload = run_mode(corpus, BASELINES["B2"], seed=0)
    assert payload["candidates"] == 2
    assert payload["selection"]["selected_count"] == 2
    assert payload["selection"]["cap"] is None
    metrics = payload["metrics"]
    assert metrics["precision"] == pytest.approx(0.5)
    assert metrics["recall"] == pytest.approx(1.0)
    assert metrics["f1"] == pytest.approx(2 * 0.5 / 1.5)
    assert metrics["comment_reduction"] == pytest.approx(0.0)
    assert metrics["redundancy_reduction"] == pytest.approx((3 - 2) / 3)


# --- B1: single reviewer only ---------------------------------------------


def test_b1_restricts_to_single_reviewer() -> None:
    corpus = build_corpus(
        [
            _rec(
                "v1",
                "a.py",
                10,
                "security/xss",
                "high",
                0.9,
                "primary",
                implemented=True,
                implementation="verbatim",
            ),
            _rec("v1", "b.py", 40, "quality/maintainability", "medium", 0.6, "quality"),
        ],
        source="t",
    )
    payload = run_mode(corpus, BASELINES["B1"], seed=0)
    assert payload["candidates"] == 1  # only the primary-reviewer row


# --- B5: gates fire (mandatory / protected / suppressed) ------------------


def test_b5_safety_gates_publish_critical_and_protected_and_suppress() -> None:
    corpus = build_corpus(
        [
            _rec(
                "g",
                "a.py",
                40,
                "security/authorization",
                "critical",
                0.95,
                "security",
                implemented=True,
                implementation="verbatim",
            ),
            _rec(
                "g",
                "a.py",
                5,
                "security/xss",
                "high",
                0.85,
                "security",
                implemented=True,
                implementation="verbatim",
            ),
            _rec(
                "g",
                "a.py",
                6,
                "security/xss",
                "high",
                0.8,
                "quality",
                implemented=True,
                implementation="verbatim",
            ),
            _rec(
                "g", "a.py", 18, "quality/maintainability", "low", 0.25, "performance"
            ),
            _rec("g", "a.py", 19, "quality/maintainability", "low", 0.2, "quality"),
            _rec(
                "g", "a.py", 18, "quality/maintainability", "low", 0.22, "architecture"
            ),
            _rec("g", "b.py", 60, "quality/error-handling", "medium", 0.5, "primary"),
        ],
        source="t",
    )
    config = replace(BASELINES["B5"], budget=2)
    payload = run_mode(corpus, config, seed=0)
    selection = payload["selection"]
    assert selection["mandatory_count"] == 1  # critical never truncated
    assert selection["protected_count"] == 1  # high + high-confidence
    assert selection["suppressed_by_gate"] == 1  # low/low-conf/redundant trio
    assert selection["selected_count"] == 2
    assert selection["cap"] == 2
    assert selection["truncated_by_budget"] == 1


# --- Learned weights (B4 / B5) --------------------------------------------


def test_learned_weight_modes_fit_logistic() -> None:
    rows = [
        _rec(
            "v1",
            "a.py",
            10,
            "security/xss",
            "high",
            0.9,
            "security",
            implemented=True,
            implementation="verbatim",
        ),
        _rec(
            "v1",
            "b.py",
            40,
            "security/authorization",
            "critical",
            0.95,
            "security",
            implemented=True,
            implementation="verbatim",
        ),
        _rec(
            "v1",
            "c.py",
            60,
            "performance/query",
            "medium",
            0.6,
            "performance",
            implemented=True,
            implementation="partial",
        ),
        _rec("v1", "d.py", 80, "quality/error-handling", "medium", 0.5, "primary"),
        _rec("v2", "a.py", 90, "standards/naming", "info", 0.3, "standards"),
        _rec("v2", "b.py", 95, "quality/maintainability", "low", 0.2, "quality"),
        _rec(
            "v2",
            "c.py",
            99,
            "security/secrets",
            "high",
            0.88,
            "security",
            implemented=True,
            implementation="verbatim",
        ),
    ]
    corpus = build_corpus(rows, source="t")
    payload = run_mode(corpus, BASELINES["B4"], seed=0)
    assert payload["weights"]["version"] == LEARNED_WEIGHTS_VERSION
    coefficients = payload["weights"]["coefficients"]
    assert coefficients is not None
    assert len(coefficients) == 9  # bias + 8 features


def test_run_is_deterministic_for_the_same_seed() -> None:
    corpus = build_corpus(
        [
            _rec(
                "v1",
                "a.py",
                10,
                "security/xss",
                "high",
                0.9,
                "security",
                implemented=True,
                implementation="verbatim",
            ),
            _rec("v1", "b.py", 40, "quality/maintainability", "medium", 0.6, "quality"),
        ],
        source="t",
    )
    first = run_mode(corpus, BASELINES["B5"], seed=7)
    second = run_mode(corpus, BASELINES["B5"], seed=7)
    assert first == second


def test_learned_mode_raises_when_there_are_no_training_candidates() -> None:
    corpus = build_corpus(
        [_rec("v1", "a.py", 10, "none", "info", 0.9, "primary", is_comment=False)],
        source="t",
    )
    with pytest.raises(ValueError, match="training candidate"):
        run_mode(corpus, BASELINES["B4"], seed=0)


def test_payload_records_code_version_for_reproducibility() -> None:
    """Each run records its code commit/dirty flag (EXPERIMENTS.md §5)."""
    corpus = build_corpus(
        [
            _rec(
                "v1",
                "a.py",
                10,
                "security/xss",
                "high",
                0.9,
                "security",
                implemented=True,
                implementation="verbatim",
            )
        ],
        source="t",
    )
    payload = run_mode(corpus, BASELINES["B3"], seed=0)
    version = payload["code_version"]
    assert set(version) == {"commit", "dirty"}
    # unavailable values are recorded as None, never guessed
    assert version["commit"] is None or (
        isinstance(version["commit"], str) and len(version["commit"]) >= 7
    )
    assert version["dirty"] in (True, False, None)
    # cached per process, so the same-seed determinism contract still holds
    assert payload == run_mode(corpus, BASELINES["B3"], seed=0)


def test_budget_is_per_review_and_metrics_use_publication_set() -> None:
    """Budget caps published findings *per review* (ARUM.md §7) and the ARUM §5
    ratios are computed over the publication set, not the candidate set.

    Two reviews x 3 candidates with budget=2 must publish 2 per review (4 of 6),
    not 2 corpus-wide and not all 6 candidates.
    """
    rows = [
        _rec(
            review,
            "a.py",
            line,
            "security/xss",
            "high",
            0.9,
            "security",
            implemented=True,
            implementation="verbatim",
        )
        for review in ("v1", "v2")
        for line in (10, 40, 70)
    ]
    corpus = build_corpus(rows, source="t")
    payload = run_mode(corpus, replace(BASELINES["B3"], budget=2), seed=0)

    selection = payload["selection"]
    assert selection["cap"] == 2
    assert selection["selected_count"] == 4  # 2 per review, not 2 corpus-wide
    assert selection["truncated_by_budget"] == 2

    metrics = payload["metrics"]
    # every finding is FIXED; the publication set is 4 of 6 candidates
    assert metrics["comment_reduction"] == pytest.approx((6 - 4) / 6)
    assert metrics["recall"] == pytest.approx(4 / 6)
    assert metrics["precision"] == pytest.approx(1.0)
