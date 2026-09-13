"""Finding consolidation + redundancy end-to-end (docs/ARCHITECTURE.md §4.5).

Component F (Finding Consolidation) normalises per-agent output into the
``findings`` model, dropping and counting anything that fails the strict
Pydantic contract. Component G (Redundancy Detection) then embeds and groups
the candidates by semantic similarity + category + file/line proximity. The
result is a serialisable :class:`ConsolidationSummary` of writes the
orchestrator persists through its store — no broker, no database, fully unit
testable.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from app.agents.contract import AgentFinding
from app.consolidation.datatypes import (
    ConsolidationSummary,
    EmbeddingWrite,
    FindingWrite,
)
from app.consolidation.embeddings import (
    EMBEDDING_DIM,
    EmbeddingsError,
    EmbeddingsProvider,
)
from app.consolidation.redundancy import apply_groups, finding_id
from app.consolidation.similarity import content_hash, embedding_text


@dataclass(frozen=True)
class NormalisationResult:
    """Outcome of consolidating raw agent output into candidate findings."""

    findings: list[FindingWrite] = field(default_factory=list)
    dropped: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def total_consumed(self) -> int:
        return len(self.findings) + self.dropped


def normalise_findings(
    review_id: str,
    repository_id: str,
    agent_inputs: Sequence[tuple[str, str, Mapping[str, object]]],
) -> NormalisationResult:
    """Normalise per-agent finding dicts into :class:`FindingWrite` rows.

    ``agent_inputs`` items are ``(agent_key, agent_id, finding_dict)``. Items
    that fail the strict contract (docs/AGENTS.md §4) are dropped and counted;
    exact duplicates from the *same* agent and location are dropped once. Valid
    cross-agent duplicates intentionally survive so redundancy can group them.
    """
    result: list[FindingWrite] = []
    dropped = 0
    errors: list[str] = []
    seen: set[tuple[str, str, int | None, int | None, str, str]] = set()

    for agent_key, agent_id, raw in agent_inputs:
        try:
            finding = AgentFinding.model_validate(dict(raw))
        except Exception as exc:  # noqa: BLE001 - per-item validation failure
            dropped += 1
            errors.append(f"{agent_key}: dropped invalid finding: {_short(str(exc))}")
            continue

        text = _plain_text(finding)
        hash_key = content_hash(text)
        dedupe_key = (
            agent_key,
            finding.file_path,
            finding.line_start,
            finding.line_end,
            finding.category,
            hash_key,
        )
        if dedupe_key in seen:
            dropped += 1
            errors.append(
                f"{agent_key}: dropped exact duplicate of "
                f"{finding.category}@{finding.file_path}:{finding.line_start}"
            )
            continue
        seen.add(dedupe_key)

        result.append(
            FindingWrite(
                id=finding_id(
                    review_id=review_id,
                    agent_key=agent_key,
                    content_hash=hash_key,
                    category=finding.category,
                    file_path=finding.file_path,
                    line_start=finding.line_start,
                    line_end=finding.line_end,
                    severity=finding.severity.value,
                ),
                agent_key=agent_key,
                agent_id=agent_id,
                review_id=review_id,
                repository_id=repository_id,
                file_path=finding.file_path,
                line_start=finding.line_start,
                line_end=finding.line_end,
                category=finding.category,
                severity=finding.severity.value,
                confidence=finding.confidence,
                title=finding.title,
                description=finding.description,
                evidence=dict(finding.evidence),
                suggested_fix=finding.suggested_fix,
                reason_summary=finding.reason_summary,
            )
        )
    return NormalisationResult(findings=result, dropped=dropped, errors=errors)


async def consolidate_review(
    review_id: str,
    findings: Sequence[FindingWrite],
    provider: EmbeddingsProvider,
    *,
    similarity_threshold: float,
    max_line_gap: int,
) -> ConsolidationSummary:
    """Embed + group already-normalised candidates and build persistable writes.

    Raises :class:`EmbeddingsError` for provider failures and for vectors whose
    dimension does not match the ``embeddings.vector vector(EMBEDDING_DIM)``
    column — the caller surfaces that as a diagnosed FAILED review, never a
    silently-completed one.
    """
    if not findings:
        return ConsolidationSummary(
            findings=[],
            agent_keys=frozenset(),
        )

    texts = [embedding_text(f) for f in findings]
    vectors = await provider.embed_texts(texts)
    for index, vector in enumerate(vectors):
        if len(vector) != EMBEDDING_DIM:
            raise EmbeddingsError(
                f"embeddings provider returned a {len(vector)}-dimensional vector "
                f"for finding {index}; expected {EMBEDDING_DIM} "
                "(must match embeddings.vector)"
            )

    grouped, groups = apply_groups(
        findings,
        vectors,
        similarity_threshold=similarity_threshold,
        max_line_gap=max_line_gap,
    )
    embeddings = [
        EmbeddingWrite(
            finding_id=f.id,
            repository_id=f.repository_id,
            content_hash=content_hash(texts[i]),
            vector=vectors[i],
            model=provider.model,
        )
        for i, f in enumerate(grouped)
    ]
    return ConsolidationSummary(
        findings=grouped,
        groups=groups,
        embeddings=embeddings,
        agent_keys=frozenset(f.agent_key for f in grouped),
    )


def _plain_text(finding: AgentFinding) -> str:
    """Mirror of ``embedding_text`` for an AgentFinding (pre-FindingWrite).

    The normalisation step computes the embedding hash before the write row
    exists; keeping the shape identical guarantees the stored
    ``embeddings.content_hash`` matches what the redundancy layer re-derives.
    """
    return " | ".join(
        (
            finding.category,
            finding.title,
            finding.description,
            finding.file_path,
            "" if finding.line_start is None else str(finding.line_start),
            "" if finding.line_end is None else str(finding.line_end),
        )
    )


def _short(message: str, limit: int = 200) -> str:
    compact = " ".join(message.split())
    if len(compact) <= limit:
        return compact
    return compact[:limit] + "…"
