"""Repository memory snapshot builder (docs/ARUM.md §2, §6).

Historical features are fed from a cached per-repository snapshot: decayed
per-category ``accepted_share`` / ``rejected_share`` computed over
``developer_feedback`` events (Phase 11 writes the events; this module turns
them into the ``repository_memory.snapshot`` JSON that ``features.py`` reads).
The decay math lives in ``app.adaptive.decay``; this module is the pure
aggregation layer so it is unit-testable offline with no store.

Events are counted at their exact category *and* their parent prefix, so
``features.historical_shares`` can fall back to ``security`` when only
``security/xss`` events exist (ARUM.md §2: rolling share per repo + category).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.adaptive.decay import weighted_share
from app.models.enums import FeedbackOutcome

# Actionability = ACCEPTED / FIXED (ARUM.md §5); rejection = REJECTED / DISMISSED.
ACCEPTED_OUTCOMES: frozenset[FeedbackOutcome] = frozenset(
    {FeedbackOutcome.ACCEPTED, FeedbackOutcome.FIXED}
)
REJECTED_OUTCOMES: frozenset[FeedbackOutcome] = frozenset(
    {FeedbackOutcome.REJECTED, FeedbackOutcome.DISMISSED}
)


@dataclass(frozen=True)
class FeedbackEvent:
    """One developer-feedback signal relevant to the memory snapshot.

    ``finding_id`` lets the Phase 11 learning loop join events back to the
    decision trace; it is ``None`` for repo-level feedback without a finding.
    """

    category: str
    outcome: FeedbackOutcome
    age_days: float
    finding_id: str | None = None


def event_positive(event: FeedbackEvent, outcomes: frozenset[FeedbackOutcome]) -> bool:
    return event.outcome in outcomes


def aggregate_category_shares(
    events: Sequence[FeedbackEvent],
    *,
    tau_days: float = 90.0,
    lam: float = 1.0,
    horizon_days: float | None = None,
) -> dict[str, dict[str, float]]:
    """Temporally decayed per-category accept/reject shares.

    Returns ``{"category": {"accepted_share": .., "rejected_share": ..}}`` where
    each share is ``sum(weight * positive) / sum(weight)`` over all events in
    that category (and its parent prefix). Events older than ``horizon_days``
    (default ``3 * tau_days``, ARUM.md §6) are excluded. Returns ``{}`` when
    there is no evidence.
    """
    horizon = 3.0 * tau_days if horizon_days is None else horizon_days
    accepted: defaultdict[str, list[tuple[float, bool]]] = defaultdict(list)
    rejected: defaultdict[str, list[tuple[float, bool]]] = defaultdict(list)
    for event in events:
        if event.age_days < 0.0 or event.age_days >= horizon:
            continue
        keys = [event.category]
        if "/" in event.category:
            keys.append(event.category.rsplit("/", 1)[0])
        for key in keys:
            accepted[key].append(
                (event.age_days, event_positive(event, ACCEPTED_OUTCOMES))
            )
            rejected[key].append(
                (event.age_days, event_positive(event, REJECTED_OUTCOMES))
            )

    categories: dict[str, dict[str, float]] = {}
    for category in sorted(set(accepted) | set(rejected)):
        categories[category] = {
            "accepted_share": _round4(
                weighted_share(accepted[category], tau_days=tau_days, lam=lam)
            ),
            "rejected_share": _round4(
                weighted_share(rejected[category], tau_days=tau_days, lam=lam)
            ),
        }
    return categories


def build_memory_snapshot(
    events: Sequence[FeedbackEvent],
    *,
    tau_days: float = 90.0,
    lam: float = 1.0,
    horizon_days: float | None = None,
) -> dict[str, Any]:
    """Assemble the ``repository_memory.snapshot`` JSON shape.

    ``features.py`` reads ``snapshot["categories"]``; the rest is traceability
    for the memory worker and experiments (EXPERIMENTS.md §5).
    """
    return {
        "categories": aggregate_category_shares(
            events,
            tau_days=tau_days,
            lam=lam,
            horizon_days=horizon_days,
        ),
        "decay_params": {
            "tau_days": tau_days,
            "lambda": lam,
            "horizon_days": 3.0 * tau_days if horizon_days is None else horizon_days,
        },
        "events_count": len(events),
    }


def _round4(value: float) -> float:
    return round(value, 4)
