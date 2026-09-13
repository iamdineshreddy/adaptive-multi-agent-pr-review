"""Finding consolidation + redundancy detection (docs/ARCHITECTURE.md §4.5).

Phase 7. Bare-broker: normalise per-agent output (F) and group duplicates via
embeddings + cosine + proximity (G). The orchestrator persists the resulting
writes through its store before handing the review to the decision layer.
"""

from app.consolidation.consolidate import (
    NormalisationResult,
    consolidate_review,
    normalise_findings,
)
from app.consolidation.datatypes import (
    ConsolidationSummary,
    EmbeddingWrite,
    FindingGroupWrite,
    FindingWrite,
)
from app.consolidation.embeddings import (
    EMBEDDING_DIM,
    EmbeddingsConfigurationError,
    EmbeddingsError,
    EmbeddingsProvider,
    MockEmbeddingsProvider,
    OpenAIEmbeddingsProvider,
    build_embeddings_provider,
)
from app.consolidation.redundancy import apply_groups, finding_id
from app.consolidation.similarity import (
    categories_aligned,
    content_hash,
    cosine_similarity,
    embedding_text,
    line_ranges_close,
    vector_sql_literal,
)

__all__ = [
    "ConsolidationSummary",
    "EMBEDDING_DIM",
    "EmbeddingWrite",
    "EmbeddingsConfigurationError",
    "EmbeddingsError",
    "EmbeddingsProvider",
    "FindingGroupWrite",
    "FindingWrite",
    "MockEmbeddingsProvider",
    "NormalisationResult",
    "OpenAIEmbeddingsProvider",
    "apply_groups",
    "build_embeddings_provider",
    "categories_aligned",
    "consolidate_review",
    "content_hash",
    "cosine_similarity",
    "embedding_text",
    "finding_id",
    "line_ranges_close",
    "normalise_findings",
    "vector_sql_literal",
]
