"""Pydantic models for GitHub webhook payloads.

Only the subset of the GitHub ``pull_request`` event the system consumes is decoded
(see docs/API.md §1). Extra fields are ignored; missing/incorrectly-typed required
fields raise a ValidationError (HTTP 422).
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field


class GitHubUser(BaseModel):
    model_config = ConfigDict(extra="ignore")

    login: str | None = None
    id: int | None = None


class GitHubRef(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ref: str
    sha: str
    label: str | None = None


class GitHubRepository(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    full_name: str
    default_branch: str = "main"
    language: str | None = None
    owner: GitHubUser | None = None
    private: bool | None = None


class GitHubInstallation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int | None = None


class GitHubPullRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    number: int
    title: str
    state: str
    draft: bool | None = None
    user: GitHubUser | None = None
    base: GitHubRef
    head: GitHubRef
    changed_files: int = 0
    additions: int = 0
    deletions: int = 0


class PullRequestWebhookEvent(BaseModel):
    """Validated ``pull_request`` webhook event."""

    model_config = ConfigDict(extra="ignore")

    action: str
    number: int
    pull_request: GitHubPullRequest
    repository: GitHubRepository
    installation: GitHubInstallation | None = None
    sender: GitHubUser | None = None


class WebhookAccepted(BaseModel):
    """202 response body (docs/API.md §1)."""

    review_id: uuid.UUID
    delivery_id: str
    queued: bool = True


class WebhookIgnored(BaseModel):
    """2xx response for validated-but-unsupported events (no review created)."""

    ignored: bool = True
    reason: str


class WebhookError(BaseModel):
    """Error envelope consistent with docs/API.md conventions."""

    detail: ErrorDetail


class ErrorDetail(BaseModel):
    code: str
    message: str
    field_errors: list[FieldError] = Field(default_factory=list)


class FieldError(BaseModel):
    loc: str
    msg: str
