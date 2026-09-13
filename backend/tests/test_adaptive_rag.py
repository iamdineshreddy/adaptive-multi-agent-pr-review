"""RAG retrieval + repository-memory store boundary (docs/ARUM.md §2, FR-5.4).

The ranking core is pure (``rag.top_k_similar``); the Memory store exercises the
same boundary the Sql store implements against PostgreSQL, so the Phase 10
wiring is testable offline with deterministic mock embeddings.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.adaptive.rag import EmbeddingRow, RagHit, max_relevance, top_k_similar
from app.consolidation.embeddings import MockEmbeddingsProvider
from app.consolidation.similarity import cosine_similarity
from app.models.enums import FeedbackOutcome
from app.orchestrator.persistence import MemoryOrchestratorStore

REPO = "repo-1"


def _row(
    text: str,
    *,
    resource_type: str = "finding",
    finding_id: str | None = None,
    file_path: str | None = "app/x.py",
    status: str | None = "RESOLVED",
) -> EmbeddingRow:
    vector = MockEmbeddingsProvider()._vector(text)
    return EmbeddingRow(
        id=uuid.uuid4().hex,
        repository_id=REPO,
        resource_type=resource_type,
        content_hash=str(len(text)),
        vector=vector,
        model="mock/text-embedding",
        finding_id=finding_id,
        file_path=file_path,
        status=status,
        content_text=text,
    )


def test_top_k_ranks_by_cosine_desc_with_tie_break() -> None:
    provider = MockEmbeddingsProvider()
    query = provider._vector("needle text")
    near = _row("needle text")  # cosine 1.0 with itself
    far = _row("something completely unrelated")
    hits = top_k_similar(query, [far, near], k=5)
    assert [h.finding_id for h in hits] == [near.finding_id, far.finding_id]
    assert hits[0].cosine == pytest.approx(1.0)


def test_top_k_respects_threshold_and_limit() -> None:
    provider = MockEmbeddingsProvider()
    query = provider._vector("needle")
    identical = _row("needle")  # cosine 1.0
    near_but_distinct = EmbeddingRow(
        id=uuid.uuid4().hex,
        repository_id=REPO,
        resource_type="finding",
        content_hash="near-hash",
        vector=query,  # same embedding, different content identity
        model="mock/text-embedding",
        finding_id="f-near",
        file_path="app/x.py",
        status="RESOLVED",
    )
    rows = [identical, near_but_distinct]
    few = top_k_similar(query, rows, k=1, similarity_threshold=1.0)
    assert len(few) == 1
    assert few[0].cosine == pytest.approx(1.0)
    assert top_k_similar(query, rows, k=5, similarity_threshold=1.1) == []


def test_top_k_filters_by_resource_type_and_file_path() -> None:
    provider = MockEmbeddingsProvider()
    query = provider._vector("needle")
    standard = _row("needle", resource_type="standard", finding_id=None, file_path=None)
    finding_other_file = _row("needle", file_path="app/other.py", finding_id="f-other")
    finding_same_file = _row("needle", file_path="app/x.py", finding_id="f-same")
    hits = top_k_similar(
        query,
        [standard, finding_other_file, finding_same_file],
        k=5,
        resource_types=("finding",),
        file_path="app/x.py",
    )
    assert [h.finding_id for h in hits] == ["f-same"]


def test_top_k_filters_by_status_for_context() -> None:
    provider = MockEmbeddingsProvider()
    query = provider._vector("needle")
    resolved = _row("needle", finding_id="f-resolved", status="RESOLVED")
    open_finding = _row("needle", finding_id="f-open", status="SCHEDULED")
    hits = top_k_similar(
        query,
        [resolved, open_finding],
        k=5,
        statuses=("RESOLVED",),
    )
    assert [h.finding_id for h in hits] == ["f-resolved"]


def test_max_relevance_empty_and_clamped() -> None:
    assert max_relevance([]) == 0.0
    hit = RagHit(repository_id=REPO, resource_type="standard", cosine=2.5)
    assert max_relevance([hit]) == 1.0
    negative = RagHit(repository_id=REPO, resource_type="finding", cosine=-0.5)
    assert max_relevance([negative]) == 0.0


async def test_memory_store_upsert_and_get_round_trip() -> None:
    store = MemoryOrchestratorStore()
    snapshot = {
        "categories": {"security": {"accepted_share": 0.7, "rejected_share": 0.1}}
    }
    decay = {"tau_days": 90.0, "lambda": 1.0}
    assert await store.get_repository_memory(REPO) is None
    version = await store.upsert_repository_memory(REPO, snapshot, decay)
    assert version == 1
    again = await store.upsert_repository_memory(REPO, snapshot, decay)
    assert again == 2  # monotonic rebuild counter
    assert await store.get_repository_memory(REPO) == snapshot


async def test_memory_store_feedback_events_filtered_by_age() -> None:
    store = MemoryOrchestratorStore()
    now = datetime.now(UTC)
    store.feedback = [
        {
            "repository_id": REPO,
            "category": "security/xss",
            "outcome": FeedbackOutcome.ACCEPTED.value,
            "created_at": now - timedelta(days=5),
        },
        {
            "repository_id": REPO,
            "category": "security",
            "outcome": FeedbackOutcome.REJECTED.value,
            "created_at": now - timedelta(days=300),
        },
    ]
    events = await store.repository_feedback_events(REPO, max_age_days=270, now=now)
    assert [e.category for e in events] == ["security/xss"]
    assert events[0].outcome == FeedbackOutcome.ACCEPTED


async def test_memory_store_retrieves_seeded_rag() -> None:
    provider = MockEmbeddingsProvider()
    store = MemoryOrchestratorStore()
    text = "security/xss | cross-site scripting risk | input unsanitised"
    query_vector = provider._vector(text)

    past_id = "past-finding"
    store.findings.append(
        {
            "id": past_id,
            "review_id": "old-review",
            "repository_id": REPO,
            "file_path": "app/auth.py",
            "publication_status": "RESOLVED",
        }
    )
    store.embeddings.append(
        {
            "id": uuid.uuid4().hex,
            "review_id": "old-review",
            "repository_id": REPO,
            "finding_id": past_id,
            "resource_type": "finding",
            "content_hash": "past-hash",
            "vector": list(query_vector),
            "model": "mock/text-embedding",
            "content_text": text,
        }
    )
    store.embeddings.append(
        {
            "id": uuid.uuid4().hex,
            "review_id": None,
            "repository_id": REPO,
            "finding_id": None,
            "resource_type": "standard",
            "content_hash": "std-hash",
            "vector": list(provider._vector("unrelated standard")),
            "model": "mock/text-embedding",
            "content_text": "unrelated standard",
        }
    )

    context = await store.retrieve_rag(
        REPO,
        query_vector,
        k=5,
        resource_types=("finding",),
        file_path="app/auth.py",
        statuses=("RESOLVED",),
    )
    assert [h.finding_id for h in context] == [past_id]
    assert context[0].cosine == pytest.approx(
        cosine_similarity(query_vector, query_vector)
    )
    repo_hits = await store.retrieve_rag(
        REPO, query_vector, k=5, resource_types=("standard",)
    )
    assert repo_hits  # the unrelated standard still counts (threshold 0.0)


async def test_memory_store_standard_dedupe_and_hashes() -> None:
    store = MemoryOrchestratorStore()
    store.embeddings.append(
        {
            "id": uuid.uuid4().hex,
            "review_id": None,
            "repository_id": REPO,
            "finding_id": None,
            "resource_type": "standard",
            "content_hash": "known-hash",
            "vector": [0.1, 0.2],
            "model": "mock/text-embedding",
            "content_text": None,
        }
    )
    hashes = await store.embedding_hashes(REPO, resource_types=("standard",))
    assert hashes == {"known-hash"}
    assert await store.embedding_hashes(REPO, resource_types=("finding",)) == set()
