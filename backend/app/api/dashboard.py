"""Dashboard read API (Phase 13).

Read-only endpoints that power the frontend dashboard: review queue/status
summary, review listings with pull-request context, and detailed review views
(findings + iteration history). No writes, no fabricated data — each endpoint
reads through :class:`OrchestratorStore` (SQL in production, Memory in tests).
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.models.enums import ReviewStatus
from app.orchestrator.persistence import (
    OrchestratorStore,
    SqlOrchestratorStore,
)

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["dashboard"])

_store: OrchestratorStore | None = None


def get_dashboard_store() -> OrchestratorStore:
    global _store
    if _store is None:
        _store = SqlOrchestratorStore()
    return _store


def _not_found(message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "not_found", "message": message},
    )


@router.get(
    "/reviews",
    response_model=list[dict[str, Any]],
    summary="List reviews with pull-request context",
)
async def list_reviews(
    store: Annotated[OrchestratorStore, Depends(get_dashboard_store)],
    status_filter: Annotated[
        ReviewStatus | None,
        Query(alias="status", description="Filter by exact review status"),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[dict[str, Any]]:
    """Newest-first review summaries, optionally filtered by status."""
    return await store.review_summaries(
        limit=limit, offset=offset, status=status_filter
    )


@router.get(
    "/reviews/{review_id}",
    response_model=dict[str, Any],
    summary="Review detail with findings and iteration history",
)
async def get_review(
    review_id: uuid.UUID,
    store: Annotated[OrchestratorStore, Depends(get_dashboard_store)],
) -> dict[str, Any]:
    detail = await store.review_detail(review_id)
    if detail is None:
        raise _not_found(f"review '{review_id}' not found")
    return detail


@router.get(
    "/dashboard/summary",
    response_model=dict[str, Any],
    summary="Review queue/status summary for the dashboard",
)
async def dashboard_summary(
    store: Annotated[OrchestratorStore, Depends(get_dashboard_store)],
) -> dict[str, Any]:
    statuses = await store.status_summary()
    return {
        "total": sum(statuses.values()),
        "by_status": statuses,
    }
