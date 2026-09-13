"""GitHub API response models (FR-1.3)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PullRequestDetail(BaseModel):
    """Normalised PR metadata fetched from the GitHub API."""

    number: int
    title: str
    state: str
    body: str | None = None
    base_ref: str
    head_ref: str
    head_sha: str
    changed_files: int = Field(default=0, ge=0)
    additions: int = Field(default=0, ge=0)
    deletions: int = Field(default=0, ge=0)
    merged: bool | None = None
    draft: bool | None = None
    author_login: str | None = None


class ChangedFile(BaseModel):
    """One changed file from ``GET /pulls/{n}/files`` (patch included)."""

    filename: str
    status: str = "modified"
    additions: int = Field(default=0, ge=0)
    deletions: int = Field(default=0, ge=0)
    changes: int = Field(default=0, ge=0)
    patch: str | None = None
    previous_filename: str | None = None
    raw_url: str | None = None


class CommitInfo(BaseModel):
    """A commit fetched from ``GET /pulls/{n}/commits``."""

    sha: str
    message: str
    author_login: str | None = None
