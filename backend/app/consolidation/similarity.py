"""Similarity + proximity primitives for redundancy detection.

These are pure, broker/DB-free maths so the grouping rules
(docs/ARCHITECTURE.md §4.5 step 2: semantic similarity + category +
file/line proximity) are unit-testable without any provider or database.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence

from app.consolidation.datatypes import FindingWrite


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity in ``[0, 1]`` (embeddings are non-negative).

    Handles mismatched-length, empty, and zero vectors by returning ``0.0`` so
    a hostile provider cannot poison the grouping with NaN.
    """
    if len(a) != len(b) or not a:
        return 0.0
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b, strict=True):
        dot += x * y
        norm_a += x * x
        norm_b += y * y
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))


def line_ranges_close(
    a_line_start: int | None,
    a_line_end: int | None,
    b_line_start: int | None,
    b_line_end: int | None,
    *,
    max_line_gap: int,
) -> bool:
    """True when two line ranges are within ``max_line_gap`` of each other.

    ``max_line_gap`` counts the lines strictly between the two ranges: touching
    or overlapping ranges are always close. A missing bound is treated as open
    toward the start/end of the file, so a finding without explicit line info
    satisfies the proximity constraint when it is in the same file (the
    demanding distance check cannot apply).
    """
    a_low, a_high = _bounded(a_line_start, a_line_end)
    b_low, b_high = _bounded(b_line_start, b_line_end)
    if a_low is None or a_high is None or b_low is None or b_high is None:
        return True
    distance = min(abs(b_low - a_high), abs(a_low - b_high))
    return distance <= max_line_gap + 1


def categories_aligned(a: str, b: str) -> bool:
    """True when two categories are equal or one descends from the other.

    Cross-agent findings about the same defect may arrive under sibling
    categories (``security/xss`` from the security agent, ``security`` from a
    broader sweep); aligning on the prefix keeps those in one group.
    """
    if a == b:
        return True
    return a.startswith(b + "/") or b.startswith(a + "/")


def embedding_text(f: FindingWrite) -> str:
    """The deterministic string fed to the embedding provider.

    Category, title, description, and location are the semantic signal; the
    delimiter-separated flat form keeps mock embeddings reproducible.
    """
    return " | ".join(
        (
            f.category,
            f.title,
            f.description,
            f.file_path,
            "" if f.line_start is None else str(f.line_start),
            "" if f.line_end is None else str(f.line_end),
        )
    )


def content_hash(text: str) -> str:
    """SHA-256 hex digest used as the ``embeddings.content_hash`` dedupe key."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def vector_sql_literal(vector: Sequence[float]) -> str:
    """pgvector text literal (``[1.0, 2.0]``) for ``CAST(... AS vector)``.

    The values are floats produced by the embeddings provider, so interpolating
    them into server-side SQL is safe and deliberately avoids the pgvector
    asyncpg codec (registered in Phase 14, see ``app.database``).
    """
    body = ", ".join(f"{float(v):.6g}" for v in vector)
    return f"[{body}]"


def _bounded(start: int | None, end: int | None) -> tuple[int | None, int | None]:
    if start is None and end is None:
        return None, None
    if start is None:
        return end, end
    if end is None:
        return start, start
    return start, end
