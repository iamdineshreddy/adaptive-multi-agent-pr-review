"""Cross-agent redundancy detection (docs/ARCHITECTURE.md component G).

Findings from parallel agents are grouped by the documented hybrid rule —
semantic similarity (embedding cosine) + category alignment + file/line
proximity. Grouping is deterministic: the representative is the highest-
confidence member (tie-broken by file path / line), member ids and group ids
are UUIDv5 over stable inputs, so re-running the same review reproduces the
same groups (docs/EXPERIMENTS.md §5).

Only multi-member groups create ``finding_groups`` rows; singletons keep
``duplicate_group = NULL`` (docs/DATABASE.md §3.7-3.8).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import replace

from app.consolidation.datatypes import FindingGroupWrite, FindingWrite
from app.consolidation.similarity import (
    categories_aligned,
    cosine_similarity,
    line_ranges_close,
)

REDUNDANCY_METHOD_HYBRID = "hybrid: embedding cosine + category + proximity"
_ID_NAMESPACE = uuid.NAMESPACE_OID


def finding_id(
    *,
    review_id: str,
    agent_key: str,
    content_hash: str,
    category: str,
    file_path: str,
    line_start: int | None,
    line_end: int | None,
    severity: str,
) -> str:
    """Deterministic per-finding id derived from stable review inputs."""
    raw = (
        f"{review_id}|{agent_key}|{content_hash}|{category}|{file_path}|"
        f"{line_start}|{line_end}|{severity}"
    )
    return str(uuid.uuid5(_ID_NAMESPACE, raw))


def apply_groups(
    findings: Sequence[FindingWrite],
    vectors: Sequence[Sequence[float]],
    *,
    similarity_threshold: float,
    max_line_gap: int,
) -> tuple[list[FindingWrite], list[FindingGroupWrite]]:
    """Greedy, deterministic hybrid grouping of ``findings``.

    Returns the findings with ``duplicate_group`` filled in (only for members
    of multi-member groups) plus the persisted group rows.
    """
    if len(findings) != len(vectors):
        raise ValueError("findings and vectors must be the same length")
    n = len(findings)
    updated = list(findings)
    if n == 0:
        return updated, []

    order = sorted(
        range(n),
        key=lambda i: (
            -findings[i].confidence,
            findings[i].file_path,
            findings[i].line_start if findings[i].line_start is not None else -1,
            findings[i].id,
        ),
    )
    assigned = [False] * n
    groups: list[FindingGroupWrite] = []

    for i in order:
        if assigned[i]:
            continue
        members = [i]
        for j in order:
            if i == j or assigned[j]:
                continue
            if not _groupable(
                findings, vectors, i, j, similarity_threshold, max_line_gap
            ):
                continue
            members.append(j)

        if len(members) < 2:
            continue

        for member in members:
            assigned[member] = True
        rep = findings[i]
        group_id = _group_id(rep)
        max_similarity = max(
            cosine_similarity(vectors[a], vectors[b]) for a in members for b in members
        )
        groups.append(
            FindingGroupWrite(
                id=group_id,
                representative_finding_id=rep.id,
                member_count=len(members),
                redundancy_method=REDUNDANCY_METHOD_HYBRID,
                max_pairwise_similarity=max_similarity,
            )
        )
        for member in members:
            updated[member] = replace(updated[member], duplicate_group=group_id)

    return updated, groups


def _groupable(
    findings: Sequence[FindingWrite],
    vectors: Sequence[Sequence[float]],
    i: int,
    j: int,
    similarity_threshold: float,
    max_line_gap: int,
) -> bool:
    a = findings[i]
    b = findings[j]
    if cosine_similarity(vectors[i], vectors[j]) < similarity_threshold:
        return False
    if not categories_aligned(a.category, b.category):
        return False
    if a.file_path != b.file_path:
        return False
    return line_ranges_close(
        a.line_start,
        a.line_end,
        b.line_start,
        b.line_end,
        max_line_gap=max_line_gap,
    )


def _group_id(representative: FindingWrite) -> str:
    raw = f"{representative.review_id}|dup|{representative.id}"
    return str(uuid.uuid5(_ID_NAMESPACE, raw))
