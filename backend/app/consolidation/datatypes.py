"""Write-level DTOs for the consolidation layer (docs/ARCHITECTURE.md §4.5).

Field names map 1:1 onto the ``findings`` / ``finding_groups`` / ``embeddings``
tables so ``OrchestratorStore`` persistence is mechanical (docs/DATABASE.md
§3.7-3.8, §3.12). All ids are pre-computed deterministically (see
``app.consolidation.redundancy``) so a review can be re-consolidated
reproducibly (docs/EXPERIMENTS.md §5).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class FindingWrite:
    """One candidate finding ready for persistence to ``findings``.

    ``duplicate_group`` is assigned later by the redundancy layer; ``None``
    means the finding was not part of any redundancy group (its evidence is
    still persisted as a candidate).
    """

    id: str
    agent_key: str
    agent_id: str
    review_id: str
    repository_id: str
    file_path: str
    line_start: int | None
    line_end: int | None
    category: str
    severity: str  # Severity.value; coerced during normalisation
    confidence: float
    title: str
    description: str
    evidence: dict[str, Any]
    suggested_fix: str | None
    reason_summary: str
    duplicate_group: str | None = None


@dataclass(frozen=True)
class FindingGroupWrite:
    """A redundancy group (member_count >= 2) persisting to ``finding_groups``."""

    id: str
    representative_finding_id: str
    member_count: int
    redundancy_method: str
    max_pairwise_similarity: float


@dataclass(frozen=True)
class EmbeddingWrite:
    """One embedding row persisting to ``embeddings`` (resource_type 'finding')."""

    finding_id: str
    repository_id: str
    content_hash: str
    vector: tuple[float, ...]
    model: str


@dataclass(frozen=True)
class ConsolidationSummary:
    """Result of consolidating + de-duplicating one review's agent findings."""

    findings: list[FindingWrite] = field(default_factory=list)
    groups: list[FindingGroupWrite] = field(default_factory=list)
    embeddings: list[EmbeddingWrite] = field(default_factory=list)
    agent_keys: frozenset[str] = field(default_factory=frozenset)
