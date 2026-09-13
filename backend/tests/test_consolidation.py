"""Unit tests for consolidation: normalisation + embed/group end-to-end."""

from __future__ import annotations

import pytest

from app.consolidation.consolidate import consolidate_review, normalise_findings
from app.consolidation.embeddings import (
    EMBEDDING_DIM,
    EmbeddingsError,
    MockEmbeddingsProvider,
)
from app.consolidation.similarity import content_hash, embedding_text

REVIEW_ID = "11111111-1111-4111-8111-111111111111"
REPOSITORY_ID = "22222222-2222-4222-8222-222222222222"


def _valid() -> dict:
    return {
        "file_path": "app/auth.py",
        "category": "security/xss",
        "severity": "HIGH",
        "confidence": 0.9,
        "title": "XSS risk",
        "description": "User input reaches output unescaped.",
        "reason_summary": "Evidence only.",
    }


def test_normalise_keeps_valid_and_drops_invalid() -> None:
    result = normalise_findings(
        REVIEW_ID,
        REPOSITORY_ID,
        [
            ("security", "agent-1", _valid()),
            ("quality", "agent-2", {"file_path": "app/auth.py", "severity": "HIGH"}),
        ],
    )
    assert len(result.findings) == 1
    assert result.dropped == 1
    assert len(result.errors) == 1
    assert "quality: dropped invalid finding" in result.errors[0]
    assert result.total_consumed == 2

    row = result.findings[0]
    assert row.agent_key == "security"
    assert row.agent_id == "agent-1"
    assert row.category == "security/xss"
    assert row.severity == "HIGH"
    assert row.confidence == 0.9


def test_normalise_drops_exact_duplicate_same_agent() -> None:
    result = normalise_findings(
        REVIEW_ID,
        REPOSITORY_ID,
        [
            ("security", "agent-1", _valid()),
            ("security", "agent-1", _valid()),
        ],
    )
    assert len(result.findings) == 1
    assert result.dropped == 1


def test_normalise_keeps_cross_agent_duplicate() -> None:
    result = normalise_findings(
        REVIEW_ID,
        REPOSITORY_ID,
        [
            ("security", "agent-1", _valid()),
            ("quality", "agent-2", _valid()),
        ],
    )
    assert len(result.findings) == 2
    assert result.dropped == 0


def test_normalise_coerces_severity_and_confidence() -> None:
    raw = _valid()
    raw["severity"] = "critical"
    raw["confidence"] = "0.55"
    result = normalise_findings(
        REVIEW_ID, REPOSITORY_ID, [("security", "agent-1", raw)]
    )
    assert result.findings[0].severity == "CRITICAL"
    assert result.findings[0].confidence == 0.55


def test_normalise_ids_are_deterministic() -> None:
    a = normalise_findings(
        REVIEW_ID, REPOSITORY_ID, [("security", "agent-1", _valid())]
    )
    b = normalise_findings(
        REVIEW_ID, REPOSITORY_ID, [("security", "agent-1", _valid())]
    )
    assert a.findings[0].id == b.findings[0].id


async def test_consolidate_review_builds_embeddings_and_hash() -> None:
    normalised = normalise_findings(
        REVIEW_ID, REPOSITORY_ID, [("security", "agent-1", _valid())]
    )
    provider = MockEmbeddingsProvider()
    summary = await consolidate_review(
        REVIEW_ID,
        normalised.findings,
        provider,
        similarity_threshold=0.85,
        max_line_gap=5,
    )

    assert len(summary.findings) == 1
    assert summary.groups == []
    assert len(summary.embeddings) == 1
    embedding = summary.embeddings[0]
    assert embedding.finding_id == summary.findings[0].id
    assert embedding.repository_id == REPOSITORY_ID
    assert embedding.model == provider.model
    assert len(embedding.vector) == EMBEDDING_DIM
    expected_hash = content_hash(embedding_text(summary.findings[0]))
    assert embedding.content_hash == expected_hash


async def test_consolidate_review_empty_findings_short_circuits() -> None:
    summary = await consolidate_review(
        REVIEW_ID,
        [],
        MockEmbeddingsProvider(),
        similarity_threshold=0.85,
        max_line_gap=5,
    )
    assert summary.findings == [] and summary.embeddings == []


async def test_consolidate_review_rejects_wrong_dimension() -> None:
    class WrongDimProvider:
        name = "wrong"
        model = "wrong"

        async def embed_texts(self, texts: list[str]) -> list[tuple[float, ...]]:
            return [(0.5,) for _ in texts]  # 1-dim, not EMBEDDING_DIM

    normalised = normalise_findings(
        REVIEW_ID, REPOSITORY_ID, [("security", "agent-1", _valid())]
    )
    with pytest.raises(EmbeddingsError, match="expected 1536"):
        await consolidate_review(
            REVIEW_ID,
            normalised.findings,
            WrongDimProvider(),
            similarity_threshold=0.85,
            max_line_gap=5,
        )


async def test_consolidate_review_groups_cross_agent_duplicate() -> None:
    normalised = normalise_findings(
        REVIEW_ID,
        REPOSITORY_ID,
        [
            ("security", "agent-1", _valid()),
            ("quality", "agent-2", _valid()),
        ],
    )
    summary = await consolidate_review(
        REVIEW_ID,
        normalised.findings,
        MockEmbeddingsProvider(),
        similarity_threshold=0.85,
        max_line_gap=5,
    )
    assert len(summary.findings) == 2
    group = summary.groups[0]
    assert group.member_count == 2
    assert {f.duplicate_group for f in summary.findings} == {group.id}


async def test_consolidate_review_agent_keys_reported() -> None:
    normalised = normalise_findings(
        REVIEW_ID,
        REPOSITORY_ID,
        [
            ("security", "agent-1", _valid()),
            ("quality", "agent-2", _valid()),
        ],
    )
    summary = await consolidate_review(
        REVIEW_ID,
        normalised.findings,
        MockEmbeddingsProvider(),
        similarity_threshold=0.85,
        max_line_gap=5,
    )
    assert summary.agent_keys == frozenset({"security", "quality"})


async def test_public_api_exports() -> None:
    import app.consolidation as consolidate

    for name in (
        "normalise_findings",
        "consolidate_review",
        "build_embeddings_provider",
        "apply_groups",
        "FindingWrite",
        "EmbeddingsError",
    ):
        assert hasattr(consolidate, name)
