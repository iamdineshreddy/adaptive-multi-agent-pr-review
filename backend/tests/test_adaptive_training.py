"""Unit tests for the ARUM learning path (docs/ARUM.md §4).

The logistic ablation baseline is pure-stdlib and always tested. LightGBM is
the primary model and is verified when the ``ml`` extra is installed;
otherwise the honest failure mode (:class:`MlExtraUnavailableError`) is
asserted.
"""

from __future__ import annotations

import math

import pytest

from app.adaptive.training import (
    MlExtraUnavailableError,
    TrainingMatrix,
    build_training_matrix,
    evaluate,
    fit_lightgbm,
    fit_logistic,
    lightgbm_predict,
    logistic_predict,
    split_train_eval,
)

HEADERS = ("x1", "x2")


def _separable_matrix(n: int = 80) -> TrainingMatrix:
    """Two-feature classes; label = (x_positive region)."""
    rows = []
    labels = []
    for i in range(n):
        if i % 2 == 0:
            rows.append((1.0, 1.0))
            labels.append(1)
        else:
            rows.append((-1.0, -1.0))
            labels.append(0)
    return TrainingMatrix(headers=HEADERS, X=rows, y=labels)


def test_build_training_matrix_validates_columns() -> None:
    matrix = build_training_matrix([((0.1, 0.2), 1)], headers=HEADERS)
    assert matrix.count == 1
    assert matrix.y == [1]
    with pytest.raises(ValueError, match="columns"):
        build_training_matrix([((0.1, 0.2, 0.3), 1)], headers=HEADERS)


def test_split_is_deterministic() -> None:
    matrix = _separable_matrix()
    train, eval_ = split_train_eval(matrix, seed=7)
    assert train.count + eval_.count == matrix.count
    again_train, again_eval = split_train_eval(matrix, seed=7)
    assert train.X == again_train.X
    assert eval_.y == again_eval.y


def test_logistic_recovers_separable_boundary() -> None:
    matrix = _separable_matrix()
    coef = fit_logistic(matrix, iterations=3000, seed=0)
    pos = logistic_predict(coef, (1.0, 1.0))
    neg = logistic_predict(coef, (-1.0, -1.0))
    assert pos > 0.95
    assert neg < 0.05
    assert len(coef) == len(HEADERS) + 1  # bias + weights


def test_logistic_predict_rejects_wrong_width() -> None:
    coef = (0.0, 1.0, 1.0)
    with pytest.raises(ValueError, match="features"):
        logistic_predict(coef, (0.5,))


def test_fit_logistic_empty_matrix_raises() -> None:
    empty = TrainingMatrix(headers=HEADERS, X=[], y=[])
    with pytest.raises(ValueError, match="empty matrix"):
        fit_logistic(empty)


def test_evaluate_metrics() -> None:
    metrics = evaluate([1, 1, 0, 0], [0.9, 0.6, 0.4, 0.1])
    assert metrics["precision"] == pytest.approx(1.0)
    assert metrics["recall"] == pytest.approx(1.0)
    assert metrics["f1"] == pytest.approx(1.0)
    assert metrics["accuracy"] == pytest.approx(1.0)
    half = evaluate([1, 0], [0.9, 0.9])
    assert half["precision"] == pytest.approx(0.5)  # one false positive
    assert half["recall"] == pytest.approx(1.0)
    assert half["accuracy"] == pytest.approx(0.5)


def test_fit_lightgbm_runs_or_raises_honestly() -> None:
    matrix = _separable_matrix()
    try:
        import lightgbm  # noqa: F401
    except ImportError:
        with pytest.raises(MlExtraUnavailableError, match="lightgbm"):
            fit_lightgbm(matrix)
        return
    booster = fit_lightgbm(matrix, seed=0, num_boost_round=30)
    pos = lightgbm_predict(booster, (1.0, 1.0))
    neg = lightgbm_predict(booster, (-1.0, -1.0))
    assert math.isfinite(pos)
    assert pos > neg


def test_train_predict_round_trip_via_reproducible_seed() -> None:
    matrix = _separable_matrix()
    first = fit_logistic(matrix, seed=3, iterations=1000)
    second = fit_logistic(matrix, seed=3, iterations=1000)
    assert first == second  # deterministic training
