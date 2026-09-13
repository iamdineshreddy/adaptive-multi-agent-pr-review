"""Build an ``AgentScope`` from fetched GitHub data (FR-1.3 -> docs/AGENTS.md §3)."""

from __future__ import annotations

import uuid

from app.agents.contract import AgentScope, FileSlice
from app.agents.diff_utils import _DEFAULT_MAX_SLICE_CHARS, changed_file_slices
from app.github.schemas import ChangedFile, PullRequestDetail


def build_scope_from_github(
    *,
    review_id: uuid.UUID | str,
    repository_id: uuid.UUID | str,
    pr: PullRequestDetail,
    files: list[ChangedFile],
    repo_standards: list[str] | tuple[str, ...] = (),
    rag_context: list[str] | tuple[str, ...] = (),
    max_slice_chars: int = _DEFAULT_MAX_SLICE_CHARS,
) -> AgentScope:
    """Assemble the frozen agent input envelope from PR metadata + changed files."""
    slices: list[FileSlice] = []
    for changed_file in files:
        slices.extend(
            changed_file_slices(
                changed_file.filename,
                changed_file.patch,
                changed_file.status,
                max_slice_chars=max_slice_chars,
            )
        )
    return AgentScope(
        review_id=review_id,
        repository_id=repository_id,
        pr_number=pr.number,
        pr_title=pr.title,
        pr_description=pr.body or "",
        base_ref=pr.base_ref,
        head_ref=pr.head_ref,
        language=None,
        changed_files=tuple(slices),
        repo_standards=tuple(repo_standards),
        rag_context=tuple(rag_context),
    )
