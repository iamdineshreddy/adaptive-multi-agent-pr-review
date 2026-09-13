"""ARUM learning path (docs/ARUM.md §4, EXPERIMENTS.md §2).

Two offline training/baseline mechanisms:

- ``fit_logistic`` — pure-stdlib batch-gradient logistic regression over the
  eight ARUM features (the ablation baseline: interpretable linear model).
- ``fit_lightgbm`` — gradient-boosted trees, the chosen primary approach. It
  lazily imports ``lightgbm`` and raises :class:`MlExtraUnavailableError` when
  the dependency is not installed (mirroring the live-DB self-skip tests), so
  the path is real but never silently falls back to a fake model.

Both are deterministic (seeded) and feed the reproducibility log.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any


class MlExtraUnavailableError(RuntimeError):
    """The ``ml`` optional extra is required for this trainer."""


@dataclass(frozen=True)
class TrainingMatrix:
    """Feature rows (``FEATURE_NAMES`` order) + binary ``publish`` labels."""

    headers: tuple[str, ...]
    X: list[tuple[float, ...]]
    y: list[int]

    @property
    def count(self) -> int:
        return len(self.y)


def build_training_matrix(
    records: Sequence[tuple[tuple[float, ...], int]],
    *,
    headers: tuple[str, ...],
) -> TrainingMatrix:
    """Validate and bundle ``(features, label)`` pairs."""
    rows: list[tuple[float, ...]] = []
    labels: list[int] = []
    for features, label in records:
        if len(features) != len(headers):
            raise ValueError(
                f"feature row has {len(features)} columns, expected {len(headers)}"
            )
        rows.append(tuple(features))
        labels.append(1 if label else 0)
    return TrainingMatrix(headers=headers, X=rows, y=labels)


def split_train_eval(
    matrix: TrainingMatrix, *, frac: float = 0.8, seed: int = 0
) -> tuple[TrainingMatrix, TrainingMatrix]:
    """Deterministic shuffled train/eval split."""
    if not 0.0 < frac < 1.0:
        raise ValueError("frac must be in (0, 1)")
    indices = list(range(matrix.count))
    random.Random(seed).shuffle(indices)
    cut = max(1, int(matrix.count * frac))
    train_idx, eval_idx = indices[:cut], indices[cut:]
    return (
        TrainingMatrix(
            headers=matrix.headers,
            X=[matrix.X[i] for i in train_idx],
            y=[matrix.y[i] for i in train_idx],
        ),
        TrainingMatrix(
            headers=matrix.headers,
            X=[matrix.X[i] for i in eval_idx],
            y=[matrix.y[i] for i in eval_idx],
        ),
    )


def _sigmoid(z: float) -> float:
    if z >= 0.0:
        return 1.0 / (1.0 + math.exp(-z))
    exp_z = math.exp(z)
    return exp_z / (1.0 + exp_z)


def _predict(coef: Sequence[float], features: Sequence[float]) -> float:
    bias = coef[0]
    total = bias + sum(c * x for c, x in zip(coef[1:], features, strict=True))
    return _sigmoid(total)


def fit_logistic(
    matrix: TrainingMatrix,
    *,
    learning_rate: float = 0.1,
    iterations: int = 2000,
    l2: float = 1e-4,
    seed: int = 0,
) -> tuple[float, ...]:
    """Train logistic-regression coefficients (bias first) via batch gradient
    descent with L2 regularisation. Deterministic given ``seed``.

    Returns ``(bias, w1, ..., wN)``. This is the ablation baseline — maps the
    eight features to a publish probability with interpretable coefficients.
    """
    if matrix.count == 0:
        raise ValueError("cannot fit logistic regression on an empty matrix")
    rng = random.Random(seed)
    n_features = len(matrix.headers)
    coef = [rng.uniform(-0.1, 0.1) for _ in range(n_features + 1)]
    n = matrix.count
    for _ in range(iterations):
        gradients = [0.0] * (n_features + 1)
        for features, label in zip(matrix.X, matrix.y, strict=True):
            error = _predict(coef, features) - float(label)
            gradients[0] += error
            for idx, value in enumerate(features, start=1):
                gradients[idx] += error * value
        for idx in range(n_features + 1):
            step = gradients[idx] / n
            if idx > 0:
                step += l2 * coef[idx]
            coef[idx] -= learning_rate * step
    return tuple(coef)


def logistic_predict(coef: Sequence[float], features: Sequence[float]) -> float:
    """Publish probability from fitted logistic coefficients."""
    if len(features) != len(coef) - 1:
        raise ValueError(f"expected {len(coef) - 1} features, got {len(features)}")
    return _predict(coef, features)


def _lightgbm() -> Any:
    try:
        import lightgbm as lgb
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise MlExtraUnavailableError(
            "lightgbm not installed; install the 'ml' extra for the "
            "LightGBM training path"
        ) from exc
    return lgb


def fit_lightgbm(
    matrix: TrainingMatrix,
    *,
    seed: int = 0,
    num_boost_round: int = 50,
) -> object:
    """Train lightGBM on the feature matrix (primary approach, ARUM.md §4).

    Deterministic via ``seed``. Returns an opaque predictor so the ``ml`` extra
    stays an implementation detail; use :func:`lightgbm_predict`.
    """
    lgb = _lightgbm()
    import numpy as np

    features_matrix = np.asarray(matrix.X, dtype=np.float64)
    labels = np.asarray(matrix.y, dtype=np.int32)
    params: dict[str, object] = {
        "objective": "binary",
        "metric": "binary_logloss",
        "boosting_type": "gbdt",
        "learning_rate": 0.05,
        "num_leaves": 15,
        "seed": seed,
        "verbose": -1,
        "deterministic": True,
        "force_row_wise": True,
    }
    dataset = lgb.Dataset(features_matrix, label=labels)
    booster = lgb.train(
        params,
        dataset,
        num_boost_round=num_boost_round,
    )
    return booster


def lightgbm_predict(booster: object, features: Sequence[float]) -> float:
    """Publish probability from a fitted lightGBM booster."""
    lgb = _lightgbm()
    import numpy as np

    row = np.asarray([list(features)], dtype=np.float64)
    return float(lgb.predict(booster, row)[0])


def evaluate(
    labels: Sequence[int],
    probabilities: Sequence[float],
    *,
    threshold: float = 0.5,
) -> dict[str, float]:
    """Precision / recall / F1 / accuracy at a decision threshold."""
    tp = fp = fn = tn = 0
    for label, probability in zip(labels, probabilities, strict=True):
        predicted = int(probability >= threshold)
        if predicted == 1 and label == 1:
            tp += 1
        elif predicted == 1 and label == 0:
            fp += 1
        elif predicted == 0 and label == 1:
            fn += 1
        else:
            tn += 1
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    accuracy = (tp + tn) / len(labels) if labels else 0.0
    return {
        "precision": round(precision, 6),
        "recall": round(recall, 6),
        "f1": round(f1, 6),
        "accuracy": round(accuracy, 6),
    }
