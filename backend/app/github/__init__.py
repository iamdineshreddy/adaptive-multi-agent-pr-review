"""GitHub integration (FR-1.3): REST client, response models, agent-scope builder."""

from __future__ import annotations

from app.github.client import GitHubApiError, GitHubClient
from app.github.schemas import ChangedFile, CommitInfo, PullRequestDetail
from app.github.scope import build_scope_from_github

__all__ = [
    "ChangedFile",
    "CommitInfo",
    "GitHubApiError",
    "GitHubClient",
    "PullRequestDetail",
    "build_scope_from_github",
]
