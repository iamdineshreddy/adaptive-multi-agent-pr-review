"""Developer feedback endpoint (docs/API.md §4, FR-5.1/FR-5.2).

``POST /api/v1/feedback`` accepts an explicit developer outcome. The repository
is resolved server-side from the finding (never trusted from the body); the row
is written idempotently (deterministic id), then the repository memory snapshot
is rebuilt so the next review's historical features reflect the outcome. A
``commit_sha`` attached to the request is recorded as re-evaluation evidence —
it never changes the outcome the developer supplied (FR-5.2). Learning lives
off the request path (``app/adaptive/learning.py``), as in docs/ARUM.md §4.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from app.adaptive.feedback import (
    FeedbackWrite,
    attach_code_change_evidence,
    code_change_signal,
)
from app.config.settings import get_settings
from app.models.enums import FeedbackOutcome, FeedbackSource
from app.orchestrator.persistence import OrchestratorStore, SqlOrchestratorStore
from app.webhooks.ratelimit import MemoryRateLimiter

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/feedback", tags=["feedback"])


class FeedbackRequest(BaseModel):
    """Explicit developer outcome (docs/API.md §4). ``repository_id`` is
    resolved server-side from the finding — never accepted from the body."""

    review_id: uuid.UUID
    finding_id: uuid.UUID
    outcome: FeedbackOutcome
    author_login: str | None = Field(default=None, max_length=128)
    commit_sha: str | None = Field(default=None, max_length=64)
    details: dict[str, Any] | None = None


class FeedbackAccepted(BaseModel):
    feedback_id: uuid.UUID
    recorded: bool
    memory_version: int | None = None


_store: OrchestratorStore | None = None
_limiter: MemoryRateLimiter | None = None


def get_feedback_store() -> OrchestratorStore:
    global _store
    if _store is None:
        _store = SqlOrchestratorStore()
    return _store


def get_feedback_limiter() -> MemoryRateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = MemoryRateLimiter(
            per_ip_per_minute=get_settings().rate_limit_feedback_per_minute,
            per_repo_burst=2**31 - 1,  # no repo bucket on this path
        )
    return _limiter


def _not_found(code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": code, "message": message},
    )


@router.post(
    "",
    response_model=FeedbackAccepted,
    summary="Record an explicit developer outcome",
    responses={
        404: {"description": "review/finding pair not found or mismatched"},
        422: {"description": "schema rejection"},
        429: {"description": "rate limited"},
    },
)
async def record_feedback(
    request: Request,
    response: Response,
    payload: FeedbackRequest,
    limiter: Annotated[MemoryRateLimiter, Depends(get_feedback_limiter)],
    store: Annotated[OrchestratorStore, Depends(get_feedback_store)],
) -> FeedbackAccepted:
    """Validate, persist idempotently, and refresh the repository memory snapshot."""
    client_ip = request.client.host if request.client else "test"
    if not limiter.allow([("ip", client_ip)]):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "rate_limited", "message": "Feedback rate limit exceeded"},
        )

    target = await store.resolve_feedback_finding(payload.review_id, payload.finding_id)
    if target is None:
        raise _not_found(
            "finding_not_found",
            "finding does not belong to the given review",
        )
    repository_id, category = target

    write = FeedbackWrite(
        review_id=str(payload.review_id),
        finding_id=str(payload.finding_id),
        repository_id=repository_id,
        category=category,
        outcome=payload.outcome,
        source=FeedbackSource.API,
        author_login=payload.author_login,
        commit_sha=payload.commit_sha,
        details=payload.details,
    )
    if payload.commit_sha:
        write = attach_code_change_evidence(
            write,
            code_change_signal(
                finding_id=write.finding_id, commit_sha=payload.commit_sha
            ),
        )

    feedback_id = await store.create_feedback(write)
    memory_version = await _refresh_repository_memory(store, repository_id)
    logger.info(
        "feedback_recorded",
        feedback_id=feedback_id,
        repository_id=repository_id,
        outcome=payload.outcome.value,
        commit_sha=payload.commit_sha,
        memory_version=memory_version,
    )
    response.status_code = status.HTTP_201_CREATED
    return FeedbackAccepted(
        feedback_id=uuid.UUID(feedback_id),
        recorded=True,
        memory_version=memory_version,
    )


async def _refresh_repository_memory(
    store: OrchestratorStore, repository_id: str
) -> int | None:
    """Rebuild the decayed snapshot from feedback (docs/ARUM.md §6, FR-5.3)."""
    from app.adaptive.memory import build_memory_snapshot

    settings = get_settings()
    tau = settings.arum_temporal_decay_days
    events = await store.repository_feedback_events(
        repository_id, max_age_days=3.0 * tau
    )
    if not events:
        return None
    built = build_memory_snapshot(events, tau_days=tau, lam=settings.arum_decay_lambda)
    return await store.upsert_repository_memory(
        repository_id, built, decay_params=built["decay_params"]
    )
