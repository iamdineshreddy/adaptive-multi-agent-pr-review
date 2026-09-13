"""Unit tests for similarity + proximity primitives (no provider, no DB)."""

from __future__ import annotations

from app.consolidation.similarity import (
    categories_aligned,
    content_hash,
    cosine_similarity,
    embedding_text,
    line_ranges_close,
    vector_sql_literal,
)

# --- cosine_similarity -----------------------------------------------------


def test_cosine_identical_vectors_returns_one() -> None:
    v = (1.0, 0.0, 0.0)
    assert cosine_similarity(v, v) == 1.0


def test_cosine_perpendicular_vectors_returns_zero() -> None:
    assert cosine_similarity((1.0, 0.0, 0.0), (0.0, 1.0, 0.0)) == 0.0


def test_cosine_near_parallel_vectors_are_near_one() -> None:
    v1 = (1.0, 0.0)
    v2 = (0.99, 0.14)
    sim = cosine_similarity(v1, v2)
    assert sim > 0.99


def test_cosine_empty_vector_returns_zero() -> None:
    assert cosine_similarity((), ()) == 0.0


def test_cosine_mismatched_lengths_returns_zero() -> None:
    assert cosine_similarity((1.0, 0.0), (1.0, 0.0, 0.0)) == 0.0


def test_cosine_zero_vector_returns_zero() -> None:
    assert cosine_similarity((0.0, 0.0), (1.0, 0.0)) == 0.0


# --- line_ranges_close -----------------------------------------------------


def test_overlapping_ranges_are_close() -> None:
    assert line_ranges_close(1, 5, 4, 8, max_line_gap=0) is True


def test_adjacent_ranges_are_close_with_gap_zero() -> None:
    # [1..5] and [6..10] are adjacent; gap 0 allows adjacency
    assert line_ranges_close(1, 5, 6, 10, max_line_gap=0) is True


def test_gap_exceeds_max_line_gap() -> None:
    # [1..5] and [8..10]: gap = 7-5 = 2
    assert line_ranges_close(1, 5, 8, 10, max_line_gap=1) is False


def test_gap_within_max_line_gap() -> None:
    # [1..5] and [7..10]: gap = 7-5 = 2, max_gap=2 → close
    assert line_ranges_close(1, 5, 7, 10, max_line_gap=2) is True


def test_missing_lines_treated_as_open() -> None:
    # None bounds collapse to the other bound; still close per docs
    assert line_ranges_close(None, None, 1, 10, max_line_gap=0) is True
    assert line_ranges_close(1, 10, None, None, max_line_gap=0) is True


def test_symmetry_of_proximity_check() -> None:
    assert line_ranges_close(1, 5, 8, 10, max_line_gap=3) == line_ranges_close(
        8, 10, 1, 5, max_line_gap=3
    )


# --- categories_aligned ----------------------------------------------------


def test_identical_categories_are_aligned() -> None:
    assert categories_aligned("security/xss", "security/xss") is True


def test_prefix_is_aligned() -> None:
    assert categories_aligned("security", "security/xss") is True
    assert categories_aligned("security/xss", "security") is True


def test_unrelated_categories_are_not_aligned() -> None:
    assert categories_aligned("security/xss", "quality/maintainability") is False


def test_partial_match_not_always_aligned() -> None:
    # "security" and "securityx" must not align
    assert categories_aligned("security", "securityx") is False


# --- embedding_text / content_hash / vector_sql_literal --------------------


def test_embedding_text_is_deterministic() -> None:
    from app.consolidation.datatypes import FindingWrite

    f = FindingWrite(
        id="x",
        agent_key="security",
        agent_id="a1",
        review_id="r1",
        repository_id="repo1",
        file_path="a.py",
        line_start=10,
        line_end=20,
        category="security/xss",
        severity="HIGH",
        confidence=0.8,
        title="XSS",
        description="desc",
        evidence={},
        suggested_fix=None,
        reason_summary="reason",
    )
    assert embedding_text(f) == embedding_text(f)


def test_content_hash_is_stable() -> None:
    h1 = content_hash("hello world")
    h2 = content_hash("hello world")
    assert h1 == h2
    assert h1 != content_hash("hello worlds")


def test_vector_sql_literal_format() -> None:
    assert vector_sql_literal([1.0, 2.0, 3.0]) == "[1, 2, 3]"
    assert vector_sql_literal([0.5]) == "[0.5]"
    assert vector_sql_literal(()) == "[]"


def test_content_hash_is_hex() -> None:
    h = content_hash("test")
    assert len(h) == 64  # SHA-256 hex
    assert all(c in "0123456789abcdef" for c in h)
