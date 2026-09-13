"""Build the agent scope for a scheduled review from DB + GitHub data (FR-1.3).

The ``orchestrator.run_review`` Celery task only carries a ``review_id``; the
worker rebuilds the ``AgentScope`` (PR metadata, changed-file diff slices, active
repo standards) from the persisted review row and the GitHub REST client before
driving the orchestration graph. This path is live-only (PostgreSQL + GitHub);
the broker-free cores it feeds are unit-tested with explicit scopes.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from sqlalchemy import select

from app.agents.contract import AgentScope
from app.config.settings import get_settings
from app.database import SessionFactory
from app.github.client import GitHubClient
from app.github.scope import build_scope_from_github
from app.models import CodingStandard, PullRequest, Repository, Review

logger = structlog.get_logger(__name__)


class ScopeBuildError(RuntimeError):
    """The review row/repository/GitHub data needed to build a scope is missing."""


def _split_full_name(full_name: str) -> tuple[str, str]:
    if "/" not in full_name:
        raise ScopeBuildError(
            f"repository full_name is not 'owner/repo': '{full_name}'"
        )
    owner, name = full_name.split("/", 1)
    if not owner or not name:
        raise ScopeBuildError(
            f"repository full_name is not 'owner/repo': '{full_name}'"
        )
    return owner, name


async def _load_standards(repository_id: uuid.UUID) -> list[str]:
    async with SessionFactory() as session:
        rows = await session.scalars(
            select(CodingStandard.description).where(
                CodingStandard.repository_id == repository_id,
                CodingStandard.is_active.is_(True),
            )
        )
        return list(rows)


async def build_scope_data_for_review(review_id: uuid.UUID) -> dict[str, Any]:
    """Fetch everything needed for one review and return the serialised scope."""
    settings = get_settings()
    async with SessionFactory() as session:
        review = await session.get(Review, review_id)
        if review is None:
            raise ScopeBuildError(f"no review row with id {review_id}")
        pull_request = await session.get(PullRequest, review.pull_request_id)
        if pull_request is None:
            raise ScopeBuildError(f"no pull_request row for review {review_id}")
        repository = await session.get(Repository, review.repository_id)
        if repository is None:
            raise ScopeBuildError(f"no repository row for review {review_id}")
        owner, name = _split_full_name(repository.full_name)
        standards = await _load_standards(review.repository_id)

    client = GitHubClient(
        base_url=settings.github_webhook_url,
        token=settings.github_pat,
    )
    try:
        pr = await client.get_pull_request(owner, name, pull_request.number)
        files = await client.get_pull_request_files(owner, name, pull_request.number)
    finally:
        await client.aclose()

    scope = build_scope_from_github(
        review_id=review_id,
        repository_id=review.repository_id,
        pr=pr,
        files=files,
        repo_standards=standards,
    )
    logger.info(
        "scope_built_for_review",
        review_id=str(review_id),
        pr_number=scope.pr_number,
        changed_files=len(scope.changed_files),
        standards=len(scope.repo_standards),
    )
    return scope.to_dict()


def scope_from_data(scope_data: dict[str, Any]) -> AgentScope:
    """Rebuild an AgentScope from a serialised scope (see ``AgentScope.from_dict``)."""
    return AgentScope.from_dict(scope_data)
