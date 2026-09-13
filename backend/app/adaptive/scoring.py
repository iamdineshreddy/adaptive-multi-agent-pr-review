"""ARUM scoring: weighted utility + deterministic ranking.

A candidate finding's review utility is the linear combination defined in
docs/ARUM.md §1 (redundancy and historical rejection subtract). The function is
pure and deterministic — same features + same weights produce the same score —
which is what the reproducibility contract (§10) requires.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.adaptive.features import ArumFeatures
from app.adaptive.weights import ArumWeights

TRACE_VERSION = "1"


def utility(features: ArumFeatures, weights: ArumWeights) -> float:
    """ARUM utility in ``[-0.5, 1]``-ish range, rounded to 6 decimals."""
    score = (
        weights.w1_severity * features.severity
        + weights.w2_confidence * features.confidence
        + weights.w3_agent_agreement * features.agent_agreement
        + weights.w4_historical_actionability * features.historical_actionability
        + weights.w5_repository_relevance * features.repository_relevance
        + weights.w6_context_relevance * features.context_relevance
        - weights.w7_redundancy * features.redundancy
        - weights.w8_historical_rejection * features.historical_rejection
    )
    return round(score, 6)


@dataclass(frozen=True)
class Decision:
    """One scored candidate — the reproducibility contract (ARUM.md §10).

    ``budget_cap`` and ``safety_gate`` are populated by the Phase 9 budget
    controller; until then they stay ``None`` and every candidate remains in
    ``CANDIDATE`` publication status (selection is Phase 9).
    """

    review_id: str
    finding_id: str
    group_id: str | None
    arum_version: str
    features: ArumFeatures
    utility: float
    weights: ArumWeights
    inputs_hash: str
    budget_cap: int | None = None
    safety_gate: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "trace_version": TRACE_VERSION,
            "review_id": self.review_id,
            "finding_id": self.finding_id,
            "group_id": self.group_id,
            "arum_version": self.arum_version,
            "features": self.features.as_dict(),
            "utility": self.utility,
            "weights": self.weights.as_dict(),
            "inputs_hash": self.inputs_hash,
            "budget_cap": self.budget_cap,
            "safety_gate": self.safety_gate,
        }


def rank_decisions(decisions: Sequence[Decision]) -> list[Decision]:
    """Deterministic rank by descending utility (finding id breaks ties)."""
    return sorted(decisions, key=lambda d: (-d.utility, d.finding_id))
