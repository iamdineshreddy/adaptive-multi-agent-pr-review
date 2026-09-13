"""Temporal decay for ARUM rolling statistics (docs/ARUM.md §6).

Old developer-feedback evidence fades exponentially so a repository that
abolished a rule a few months ago stops being penalised for following it today.
The memory worker (Phase 11) uses this to build the cached per-category
``accepted_share`` / ``rejected_share`` that ``features.py`` reads; the pure
math lives here so it is unit-tested independently.
"""

from __future__ import annotations

import math
from collections.abc import Sequence


def decay_weight(age_days: float, *, tau_days: float = 90.0, lam: float = 1.0) -> float:
    """``exp(-lam * age / tau)`` — 1.0 for a fresh event, ~0 beyond 3*tau."""
    if age_days < 0.0:
        raise ValueError("age_days must be non-negative")
    if tau_days <= 0.0 or lam < 0.0:
        raise ValueError("tau must be positive and lam non-negative")
    return math.exp(-lam * age_days / tau_days)


def weighted_share(
    events: Sequence[tuple[float, bool]],
    *,
    tau_days: float = 90.0,
    lam: float = 1.0,
) -> float:
    """Decayed fraction of positive events.

    ``events`` is ``(age_days, is_positive)``. Each event's weight is
    ``decay_weight(age)``; the share is ``sum(w * positive) / sum(w)``. Returns
    ``0.0`` when there is no evidence at all.
    """
    total = 0.0
    positive = 0.0
    for age_days, is_positive in events:
        weight = decay_weight(age_days, tau_days=tau_days, lam=lam)
        total += weight
        if is_positive:
            positive += weight
    if total == 0.0:
        return 0.0
    return positive / total
