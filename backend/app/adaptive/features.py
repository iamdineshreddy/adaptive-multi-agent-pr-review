"""ARUM eight-feature extraction (docs/ARUM.md §2).

All features are normalised to ``[0, 1]``. Features that need data the
decision layer does not yet have (RAG relevance, cached memory shares,
feedback history) default to ``0.0`` — they are wired in Phases 10–11 — while
severity/confidence/agreement/redundancy come straight from the consolidated
candidate set.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.consolidation.datatypes import FindingWrite

FEATURE_NAMES = (
    "severity",
    "confidence",
    "agent_agreement",
    "historical_actionability",
    "repository_relevance",
    "context_relevance",
    "redundancy",
    "historical_rejection",
)

SEVERITY_SCALE: Mapping[str, float] = {
    "critical": 1.0,
    "high": 0.8,
    "medium": 0.5,
    "low": 0.2,
    "info": 0.05,
}

AGREEMENT_SINGLE = 0.5  # one agent, evidence-supported candidate (ARUM.md §2)
VARIANCE_THRESHOLD = 0.3


@dataclass(frozen=True)
class ArumFeatures:
    """The eight normalised ``[0, 1]`` features in ``FEATURE_NAMES`` order."""

    severity: float
    confidence: float
    agent_agreement: float
    historical_actionability: float
    repository_relevance: float
    context_relevance: float
    redundancy: float
    historical_rejection: float

    def as_dict(self) -> dict[str, float]:
        return {
            "severity": self.severity,
            "confidence": self.confidence,
            "agent_agreement": self.agent_agreement,
            "historical_actionability": self.historical_actionability,
            "repository_relevance": self.repository_relevance,
            "context_relevance": self.context_relevance,
            "redundancy": self.redundancy,
            "historical_rejection": self.historical_rejection,
        }

    def as_vector(self) -> tuple[float, ...]:
        return (
            self.severity,
            self.confidence,
            self.agent_agreement,
            self.historical_actionability,
            self.repository_relevance,
            self.context_relevance,
            self.redundancy,
            self.historical_rejection,
        )


def severity_score(severity: str) -> float:
    """Ordinal severity map (ARUM.md §2). Unknown labels score 0.0."""
    return SEVERITY_SCALE.get(severity.lower(), 0.0)


def confidence_score(confidence: float) -> float:
    return min(max(float(confidence), 0.0), 1.0)


def redundancy_term(member_count: int) -> float:
    """``1 - 1/group_size`` (ARUM.md §2): 0 for singletons, 0.5 for a pair."""
    if member_count < 2:
        return 0.0
    return 1.0 - 1.0 / float(member_count)


def disagreement_variance(members: Sequence[FindingWrite]) -> float:
    """Normalised severity+confidence spread inside a redundancy group (§8).

    Returns ``0.0`` when the group agrees; approaches 1.0 as members cluster at
    opposite ends of both scales. Used to reduce ``agent_agreement``.
    """
    if len(members) < 2:
        return 0.0
    sevs = [severity_score(m.severity) for m in members]
    confs = [confidence_score(m.confidence) for m in members]
    spread = [max(v) - min(v) for v in (sevs, confs) if len(v) > 1]
    if not spread:
        return 0.0
    return min(sum(spread[:2]) / 2.0, 1.0)


def agent_agreement(
    member_count: int,
    distinct_agents: int,
    *,
    variance: float = 0.0,
    variance_threshold: float = VARIANCE_THRESHOLD,
) -> float:
    """Cross-agent corroboration, reduced by disagreement (§8).

    1.0 when two+ agents produced the same finding; 0.5 for a single-agent
    finding. When severity/confidence disagree beyond ``variance_threshold``
    the value is discounted by up to 50% so one confident agent cannot outrank
    consensus.
    """
    base = 1.0 if member_count >= 2 and distinct_agents >= 2 else AGREEMENT_SINGLE
    if variance <= variance_threshold or variance_threshold <= 0.0:
        return base
    discount = min(variance / variance_threshold - 1.0, 1.0) * 0.5
    return round(max(base * (1.0 - discount), 0.0), 4)


def historical_terms(
    memory: Mapping[str, Any] | None, category: str
) -> tuple[float, float]:
    """(actionability, rejection) from the cached repo-memory snapshot.

    The snapshot (``repository_memory.snapshot``) stores pre-decayed per-category
    ``accepted_share`` / ``rejected_share`` computed by the Phase 11 memory
    worker. Exact category first, then the parent prefix (``security/xss`` falls
    back to ``security``), then ``0.0``.
    """
    shares = historical_shares(memory or {}, category)
    return shares["accepted"], shares["rejected"]


def historical_shares(memory: Mapping[str, Any], category: str) -> dict[str, float]:
    categories = memory.get("categories") or {}
    for key in (category, category.rsplit("/", 1)[0]):
        entry = categories.get(key)
        if entry:
            return {
                "accepted": _clamp01(entry.get("accepted_share")),
                "rejected": _clamp01(entry.get("rejected_share")),
            }
    return {"accepted": 0.0, "rejected": 0.0}


def repository_relevance(memory: Mapping[str, Any] | None) -> float:
    """Semantic fit with repo standards/concerns (pgvector RAG, Phase 10)."""
    return _clamp01((memory or {}).get("repository_relevance"))


def context_relevance(memory: Mapping[str, Any] | None) -> float:
    """Fit with previously resolved findings in the same region (Phase 12)."""
    return _clamp01((memory or {}).get("context_relevance"))


def extract_features(
    finding: FindingWrite,
    *,
    member_count: int,
    distinct_agents: int,
    variance: float = 0.0,
    memory: Mapping[str, Any] | None = None,
) -> ArumFeatures:
    actionability, rejection = historical_terms(memory, finding.category)
    return ArumFeatures(
        severity=severity_score(finding.severity),
        confidence=confidence_score(finding.confidence),
        agent_agreement=agent_agreement(
            member_count, distinct_agents, variance=variance
        ),
        historical_actionability=actionability,
        repository_relevance=repository_relevance(memory),
        context_relevance=context_relevance(memory),
        redundancy=redundancy_term(member_count),
        historical_rejection=rejection,
    )


def _clamp01(value: Any) -> float:
    if value is None:
        return 0.0
    try:
        return min(max(float(value), 0.0), 1.0)
    except (TypeError, ValueError):
        return 0.0
