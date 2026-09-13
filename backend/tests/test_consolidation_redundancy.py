"""Unit tests for cross-agent redundancy grouping (docs/ARCHITECTURE.md §4.5).

Grouping is a pure function of candidate findings + their embeddings; vectors
are scripted here so cosine distances are exact and the hybrid rule
(similarity + category + file/line proximity) is exercised decisively.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.consolidation.datatypes import FindingWrite
from app.consolidation.embeddings import EMBEDDING_DIM
from app.consolidation.redundancy import REDUNDANCY_METHOD_HYBRID, apply_groups
from app.consolidation.similarity import embedding_text

REVIEW_ID = "11111111-1111-4111-8111-111111111111"
REPOSITORY_ID = "22222222-2222-4222-8222-222222222222"


class ScriptedEmbeddingsProvider:
    name = "scripted"
    model = "scripted"

    def __init__(self, mapping: dict[str, tuple[float, ...]]) -> None:
        self._mapping = mapping

    async def embed_texts(self, texts: list[str]) -> list[tuple[float, ...]]:
        return [self._mapping[text] for text in texts]


def _finding(
    agent_key: str,
    *,
    category: str = "security/xss",
    file_path: str = "app/auth.py",
    line_start: int | None = None,
    line_end: int | None = None,
    confidence: float = 0.9,
    title: str = "XSS risk",
    description: str = "User input reaches output unescaped.",
    severity: str = "HIGH",
) -> FindingWrite:
    return FindingWrite(
        id=str(uuid.uuid4()),
        agent_key=agent_key,
        agent_id=f"agent-{agent_key}",
        review_id=REVIEW_ID,
        repository_id=REPOSITORY_ID,
        file_path=file_path,
        line_start=line_start,
        line_end=line_end,
        category=category,
        severity=severity,
        confidence=confidence,
        title=title,
        description=description,
        evidence={},
        suggested_fix=None,
        reason_summary="Evidence only.",
    )


def _vector(entries: list[tuple[int, float]]) -> tuple[float, ...]:
    """L2-normalised 1536-dim vector from (index, weight) pairs."""
    values = [0.0] * EMBEDDING_DIM
    for index, weight in entries:
        values[index] = weight
    norm = sum(v * v for v in values) ** 0.5
    return tuple(v / norm for v in values)


def _script(
    findings: list[FindingWrite], vectors: list[tuple[float, ...]]
) -> ScriptedEmbeddingsProvider:
    return ScriptedEmbeddingsProvider(
        {embedding_text(f): v for f, v in zip(findings, vectors, strict=True)}
    )


async def _group(
    findings: list[FindingWrite],
    vectors: list[tuple[float, ...]],
    *,
    threshold: float = 0.85,
    max_line_gap: int = 5,
) -> tuple[list[FindingWrite], list[Any]]:
    return apply_groups(
        findings,
        vectors,
        similarity_threshold=threshold,
        max_line_gap=max_line_gap,
    )


async def test_cross_agent_duplicate_is_grouped() -> None:
    security = _finding("security", confidence=0.9)
    quality = _finding(
        "quality",
        confidence=0.6,
        title="Parameterised output is missing",
        description="Same defect from the quality angle.",
    )
    # quality's distinct title/description change the text, so script both:
    v_security = _vector([(0, 0.99), (1, 0.14)])
    v_quality = _vector([(0, 0.98), (1, 0.19)])
    provider = _script([security, quality], [v_security, v_quality])
    vectors = await provider.embed_texts(
        [embedding_text(f) for f in [security, quality]]
    )
    updated, groups = await _group([security, quality], vectors)

    assert len(groups) == 1
    group = groups[0]
    assert group.member_count == 2
    assert group.redundancy_method == REDUNDANCY_METHOD_HYBRID
    assert group.max_pairwise_similarity > 0.85
    assert group.representative_finding_id == security.id  # highest confidence
    assert updated[0].duplicate_group == group.id
    assert updated[1].duplicate_group == group.id


async def test_grouping_is_deterministic() -> None:
    a = _finding("security", confidence=0.9)
    b = _finding("quality", confidence=0.7)
    vectors = [_vector([(0, 1.0)]), _vector([(0, 1.0)])]
    first, g1 = await _group([a, b], vectors)
    second, g2 = await _group([a, b], vectors)
    assert [g.id for g in g1] == [g.id for g in g2]
    assert [f.duplicate_group for f in first] == [f.duplicate_group for f in second]


async def test_different_categories_not_grouped() -> None:
    a = _finding("security", category="security/xss")
    b = _finding("quality", category="quality/maintainability")
    vectors = [_vector([(0, 1.0)]), _vector([(0, 0.99), (1, 0.14)])]
    updated, groups = await _group([a, b], vectors)
    assert groups == []
    assert all(f.duplicate_group is None for f in updated)


async def test_prefix_category_alignment_keeps_group() -> None:
    a = _finding("security", category="security/xss")
    b = _finding("security", category="security")
    vectors = [_vector([(0, 1.0)]), _vector([(0, 0.95)])]
    _, groups = await _group([a, b], vectors)
    assert len(groups) == 1


async def test_far_apart_lines_not_grouped() -> None:
    a = _finding("security", line_start=10, line_end=15)
    b = _finding("quality", line_start=400, line_end=410)
    vectors = [_vector([(0, 1.0)]), _vector([(0, 0.99), (1, 0.14)])]
    updated, groups = await _group([a, b], vectors, max_line_gap=5)
    assert groups == []
    assert updated[0].duplicate_group is None
    assert updated[1].duplicate_group is None
    assert all(f.duplicate_group is None for f in updated)


async def test_nearby_lines_grouped_within_gap() -> None:
    a = _finding("security", line_start=10, line_end=15)
    b = _finding("quality", line_start=17, line_end=20)
    vectors = [_vector([(0, 1.0)]), _vector([(0, 0.99), (1, 0.14)])]
    _, groups = await _group([a, b], vectors, max_line_gap=5)
    assert len(groups) == 1


async def test_different_files_not_grouped() -> None:
    a = _finding("security", file_path="app/auth.py")
    b = _finding("quality", file_path="app/other.py")
    vectors = [_vector([(0, 1.0)]), _vector([(0, 0.99), (1, 0.14)])]
    updated, groups = await _group([a, b], vectors)
    assert groups == []
    assert all(f.duplicate_group is None for f in updated)


async def test_two_duplicates_plus_unique_make_one_group() -> None:
    a = _finding("security", confidence=0.9)
    b = _finding("quality", confidence=0.7)
    c = _finding("performance", category="performance/loop", title="Tight loop")
    vectors = [
        _vector([(0, 1.0)]),
        _vector([(0, 0.98)]),
        _vector([(10, 1.0)]),  # unrelated direction
    ]
    updated, groups = await _group([a, b, c], vectors)
    assert len(groups) == 1
    assert groups[0].member_count == 2
    grouped_ids = {f.id for f in updated if f.duplicate_group is not None}
    assert grouped_ids == {a.id, b.id}
    assert updated[2].duplicate_group is None


async def test_below_threshold_not_grouped() -> None:
    a = _finding("security")
    b = _finding("quality", title="Different enough")
    vectors = [_vector([(0, 1.0)]), _vector([(0, 0.7), (1, 0.71)])]  # cosine ~0.70
    updated, groups = await _group([a, b], vectors, threshold=0.85)
    assert groups == []
    assert all(f.duplicate_group is None for f in updated)


async def test_empty_findings_yield_no_groups() -> None:
    updated, groups = await _group([], [])
    assert updated == [] and groups == []


async def test_mismatched_lengths_raise() -> None:
    a = _finding("security")
    try:
        await _group([a], [])
    except ValueError as exc:
        assert "same length" in str(exc)
    else:
        raise AssertionError("expected ValueError")
