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


class ReviewCommentWrite(BaseModel):
    """One inline comment to attach to a created pull-request review.

    ``line`` is the line number in the *new* (right) side of the diff; GitHub
    rejects comments whose position does not fall inside the diff, so the
    publisher degrades those to the review body instead of losing the finding.
    """

    path: str
    line: int | None = None
    side: str = "RIGHT"
    body: str


class PullRequestReviewWrite(BaseModel):
    """Payload for ``POST /repos/{o}/{r}/pulls/{n}/reviews``."""

    commit_id: str
    event: str = "COMMENT"
    body: str | None = None
    comments: list[ReviewCommentWrite] = []


class ReviewCommentResult(BaseModel):
    """One created review comment as echoed by the GitHub API."""

    id: int
    path: str
    line: int | None = None
    body: str = ""


class PullRequestReviewResult(BaseModel):
    """The created review incl. its id and the posting order of comments.

    The API echoes inline comments in request order; the publisher maps them
    back onto findings by the order it posted them (idempotency keys).
    """

    id: int
    url: str | None = None
    state: str = ""
    comments: list[ReviewCommentResult] = []
