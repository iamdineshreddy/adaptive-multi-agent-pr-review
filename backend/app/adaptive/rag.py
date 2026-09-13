"""RAG retrieval for ARUM relevance features (docs/ARUM.md §2, FR-5.4).

``repository_relevance`` is the semantic fit between a candidate and the repo's
coded standards / accepted concerns; ``context_relevance`` is the same fit
against previously resolved findings in the *same file path* — re-raising an
already-closed issue is penalised unless new evidence appears. Both are computed
as the best cosine similarity over the top-``k`` pgvector hits, so they stay in
``[0, 1]`` for the normalised feature vector (ARUM.md §2).

The ranking core is pure: stores hand it embedding rows and get ordered,
thresholded hits back, keeping the ranking deterministic and database-free for
tests (docs/EXPERIMENTS.md §5).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.consolidation.similarity import cosine_similarity

RAG_RESOURCE_KINDS = frozenset({"finding", "standard", "decision", "comment"})


@dataclass(frozen=True)
class EmbeddingRow:
    """A candidate embedding for retrieval (mirrors the ``embeddings`` table)."""

    id: str
    repository_id: str
    resource_type: str
    content_hash: str
    vector: tuple[float, ...]
    model: str
    finding_id: str | None = None
    file_path: str | None = None
    status: str | None = None
    content_text: str | None = None


@dataclass(frozen=True)
class RagHit:
    """One retrieved embedding with its similarity to the query."""

    repository_id: str
    resource_type: str
    cosine: float
    finding_id: str | None = None
    content_text: str | None = None


def top_k_similar(
    query_vector: Sequence[float],
    rows: Sequence[EmbeddingRow],
    *,
    k: int,
    similarity_threshold: float = 0.0,
    resource_types: Sequence[str] | None = None,
    file_path: str | None = None,
    statuses: Sequence[str] | None = None,
) -> list[RagHit]:
    """Rank ``rows`` by cosine distance from ``query_vector`` (ARUM.md §2).

    Filters by ``resource_types``, an optional ``file_path`` and an optional set
    of finding statuses (context relevance only matches *resolved* findings in
    the same region), drops hits below ``similarity_threshold``, and returns the
    top ``k`` in deterministic order (cosine desc, then ``(finding_id, content_text)``).
    """
    kinds = (
        set(resource_types)
        if resource_types is not None
        else set(RAG_RESOURCE_KINDS)
    )
    wanted_statuses = set(statuses) if statuses is not None else None
    scored: list[RagHit] = []
    for row in rows:
        if row.resource_type not in kinds:
            continue
        if file_path is not None and (row.file_path or "") != file_path:
            continue
        if wanted_statuses is not None and row.status not in wanted_statuses:
            continue
        similarity = cosine_similarity(query_vector, row.vector)
        if similarity < similarity_threshold:
            continue
        scored.append(
            RagHit(
                repository_id=row.repository_id,
                resource_type=row.resource_type,
                cosine=similarity,
                finding_id=row.finding_id,
                content_text=row.content_text,
            )
        )
    scored.sort(
        key=lambda hit: (hit.cosine, hit.finding_id or "", hit.content_text or ""),
        reverse=True,
    )
    return scored[:k]


def max_relevance(hits: Sequence[RagHit]) -> float:
    """Best cosine similarity clamped to ``[0.0, 1.0]`` — the ARUM relevance feature.

    Returns ``0.0`` when there is no retrieved evidence, so a cold-start
    repository does not degrade better candidates, and negative cosine never
    leaks into a relevance feature.
    """
    if not hits:
        return 0.0
    return min(max(max(hit.cosine for hit in hits), 0.0), 1.0)
