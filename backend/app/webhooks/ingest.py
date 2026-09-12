"""Webhook ingestion: persist repository / PR and create the review run.

Architecture §4.1 steps 4-7. Pure persistence and state bookkeeping -- no AI work,
no queueing (dispatch is handled by ``ReviewDispatcher``).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config.settings import get_settings
from app.database import SessionFactory
from app.models import (
    PRState,
    PullRequest,
    Repository,
    Review,
    ReviewMode,
    ReviewStatus,
)
from app.webhooks.priority import PriorityResult, compute_priority
from app.webhooks.schemas import PullRequestWebhookEvent

_ACTION_TO_MODE = {
    "opened": ReviewMode.OPENED,
    "synchronize": ReviewMode.SYNCHRONIZE,
    "ready_for_review": ReviewMode.READY_FOR_REVIEW,
    "reopened": ReviewMode.REOPENED,
}


@dataclass(frozen=True)
class IngestOutcome:
    review_id: uuid.UUID
    created: bool
    priority: PriorityResult | None


class ReviewIngester(Protocol):
    """Boundary between the HTTP layer and persistence (replaced in tests)."""

    async def ingest(
        self,
        event: PullRequestWebhookEvent,
        delivery_id: str,
    ) -> IngestOutcome: ...


class SqlReviewIngester:
    """PostgreSQL-backed ingestion with delivery-id idempotency."""

    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession] | None = None
    ) -> None:
        self._session_factory = session_factory or SessionFactory

    async def ingest(
        self,
        event: PullRequestWebhookEvent,
        delivery_id: str,
    ) -> IngestOutcome:
        pr = event.pull_request
        settings = get_settings()

        async with self._session_factory() as session, session.begin():
            repo = await self._upsert_repository(session, event)
            pr_row = await self._upsert_pull_request(session, repo, event)

            existing = await session.scalar(
                select(Review).where(Review.github_delivery_id == delivery_id)
            )
            if existing is not None:
                return IngestOutcome(
                    review_id=existing.id,
                    created=False,
                    priority=None,
                )

            urgency = repo.review_settings.get("urgency")
            priority = compute_priority(pr, settings, urgency)
            review = Review(
                pull_request_id=pr_row.id,
                repository_id=repo.id,
                status=ReviewStatus.QUEUED,
                mode=_ACTION_TO_MODE[event.action],
                priority_score=priority.score,
                risk_class=priority.risk_class,
                github_delivery_id=delivery_id,
            )
            session.add(review)
            await session.flush()
            return IngestOutcome(
                review_id=review.id,
                created=True,
                priority=priority,
            )

    @staticmethod
    async def _upsert_repository(
        session: AsyncSession,
        event: PullRequestWebhookEvent,
    ) -> Repository:
        repository = event.repository
        repo = await session.scalar(
            select(Repository).where(Repository.github_id == repository.id)
        )
        if repo is None:
            repo = Repository(
                github_id=repository.id,
                full_name=repository.full_name,
                default_branch=repository.default_branch,
                main_language=repository.language,
                github_installation_id=(
                    event.installation.id if event.installation else None
                ),
                priority_overrides={},
                review_settings={},
            )
            session.add(repo)
        else:
            repo.full_name = repository.full_name
            repo.default_branch = repository.default_branch
            if repository.language is not None:
                repo.main_language = repository.language
            if event.installation and event.installation.id is not None:
                repo.github_installation_id = event.installation.id
        await session.flush()
        return repo

    @staticmethod
    async def _upsert_pull_request(
        session: AsyncSession,
        repo: Repository,
        event: PullRequestWebhookEvent,
    ) -> PullRequest:
        pr = event.pull_request
        pr_row = await session.scalar(
            select(PullRequest).where(
                PullRequest.repository_id == repo.id,
                PullRequest.number == pr.number,
            )
        )
        if pr_row is None:
            pr_row = PullRequest(
                repository_id=repo.id,
                github_pr_id=pr.id,
                number=pr.number,
                title=pr.title,
                author_login=pr.user.login if pr.user else "unknown",
                base_ref=pr.base.ref,
                head_ref=pr.head.ref,
                head_sha=pr.head.sha,
                state=PRState.OPEN,
                changed_files=pr.changed_files,
                additions=pr.additions,
                deletions=pr.deletions,
            )
            session.add(pr_row)
        else:
            pr_row.title = pr.title
            pr_row.head_ref = pr.head.ref
            pr_row.head_sha = pr.head.sha
            pr_row.changed_files = pr.changed_files
            pr_row.additions = pr.additions
            pr_row.deletions = pr.deletions
            if pr.user is not None and pr.user.login:
                pr_row.author_login = pr.user.login
        await session.flush()
        return pr_row
