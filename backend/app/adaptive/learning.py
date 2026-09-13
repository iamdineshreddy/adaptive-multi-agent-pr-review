"""ARUM weight learning from feedback (docs/ARUM.md §4, FR-5, Phase 11).

Closed-loop calibration: decision-trace rows (the eight features per candidate,
exactly as recorded for reproducibility) are joined to *explicit* developer
feedback outcomes by finding id, giving a labelled matrix. The logistic
ablation + (lazily imported) LightGBM trainers from ``training.py`` fit it;
the result is a **candidate** weight set, in-sample evaluated, versioned, and
left *out* of active scoring until an operator/experiment promotes it
(NFR-5.1 — no fabricated metrics, no silent auto-promotion).

``MODIFIED`` / ``DISCUSSION`` feedback is never labelled (see ``feedback.py``):
only actionable vs rejective outcomes train the model.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import Any

from app.adaptive.feedback import outcome_label
from app.adaptive.memory import FeedbackEvent
from app.adaptive.training import (
    TrainingMatrix,
    build_training_matrix,
    evaluate,
    fit_logistic,
    logistic_predict,
)
from app.adaptive.weights import _WEIGHT_KEYS


def feedback_labels(events: Sequence[FeedbackEvent]) -> dict[str, int]:
    """Map each finding to its binary label — first labelled event wins.

    Events without a finding id (repo-level feedback) and neutral outcomes
    (MODIFIED / DISCUSSION) contribute no label.
    """
    labels: dict[str, int] = {}
    for event in events:
        if not event.finding_id or event.finding_id in labels:
            continue
        label = outcome_label(event.outcome)
        if label is not None:
            labels[event.finding_id] = label
    return labels


def labelled_matrix(
    decision_rows: Sequence[Mapping[str, Any]],
    labels_by_finding: Mapping[str, int],
    *,
    headers: Sequence[str],
) -> tuple[TrainingMatrix, dict[str, int]]:
    """Join decision-trace rows to feedback labels, deterministic order.

    Each decision row contributes its feature vector when ``finding_id`` has a
    label; unlabelled candidates are skipped (their decision is still recorded
    in the reproducibility log, but contributes nothing to calibration).
    """
    records: list[tuple[tuple[float, ...], int]] = []
    labeled = 0
    for row in decision_rows:
        finding_id = str(row.get("finding_id") or "")
        label = labels_by_finding.get(finding_id)
        features = row.get("features")
        if label is None or not isinstance(features, Mapping):
            continue
        vector = tuple(float(features[name]) for name in headers)
        records.append((vector, label))
        labeled += 1
    matrix = build_training_matrix(records, headers=tuple(headers))
    return matrix, {
        "traces": len(decision_rows),
        "labelled": labeled,
        "unlabelled": max(0, len(decision_rows) - labeled),
    }


def _coefficient_weights(coef: Sequence[float]) -> dict[str, float]:
    """Map logistic coefficients (bias first) onto ARUM weight magnitudes.

    ARUM already encodes direction (w7 redundancy / w8 rejection are penalty
    terms, subtracted in ``scoring.utility``), so the candidate takes the
    magnitude of each learned coefficient and L1-normalises to a magnitude
    comparable with the v1 heuristic prior. This is a documented calibration —
    the *signature* stays with ARUM's formula.
    """
    magnitudes = [abs(value) for value in coef[1:]]
    total = sum(magnitudes) or 1.0
    return {
        key: round(value / total, 6)
        for key, value in zip(_WEIGHT_KEYS, magnitudes, strict=True)
    }


def learn_candidate(
    matrix: TrainingMatrix,
    *,
    seed: int = 0,
    min_examples: int = 20,
) -> dict[str, Any] | None:
    """Fit the logistic ablation and bundle an evaluated weight candidate.

    Returns ``None`` (not a silent fake) below ``min_examples`` labelled rows —
    a model trained on a handful of decisions would be noise, and the project
    never pretends harvested data exists. Metrics are in-sample diagnostics.
    """
    if matrix.count < min_examples:
        return None
    coef = fit_logistic(matrix, seed=seed)
    probabilities = [logistic_predict(coef, features) for features in matrix.X]
    metrics = evaluate(matrix.y, probabilities)
    return {
        "version": "v2-logistic-" + _candidate_hash(coef),
        "source": "logistic",
        "values": _coefficient_weights(coef),
        "metrics": metrics,
        "trained_on": matrix.count,
        "seed": seed,
    }


def _candidate_hash(coef: Sequence[float]) -> str:
    digest = hashlib.sha256()
    for value in coef:
        digest.update(f"{value:.12f}".encode())
        digest.update(b"|")
    return digest.hexdigest()[:8]
