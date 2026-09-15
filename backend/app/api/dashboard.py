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

from app.models.enums import FindingStatus, ReviewStatus
from app.orchestrator.persistence import (
    OrchestratorStore,
    SqlOrchestratorStore,
)
from app.security.auth import get_principal, require_repo_access
from app.security.tokens import TokenPrincipal

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["dashboard"], dependencies=[Depends(get_principal)])

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


# --- Phase 13 part 2: repositories / memory / feedback / metrics / findings --


@router.get(
    "/repositories",
    response_model=list[dict[str, Any]],
    summary="List repositories",
)
async def list_repositories(
    store: Annotated[OrchestratorStore, Depends(get_dashboard_store)],
    principal: Annotated[TokenPrincipal, Depends(get_principal)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[dict[str, Any]]:
    summaries = await store.repository_summaries(limit=limit + offset, offset=0)
    if principal.repos is not None:
        summaries = [s for s in summaries if s["id"] in principal.repos]
    return summaries[offset : offset + limit]


@router.get(
    "/repositories/{repository_id}",
    response_model=dict[str, Any],
    summary="Repository detail with settings",
)
async def get_repository(
    repository_id: uuid.UUID,
    store: Annotated[OrchestratorStore, Depends(get_dashboard_store)],
    principal: Annotated[TokenPrincipal, Depends(get_principal)],
) -> dict[str, Any]:
    require_repo_access(repository_id, principal)
    detail = await store.repository_detail(repository_id)
    if detail is None:
        raise _not_found(f"repository '{repository_id}' not found")
    return detail


@router.get(
    "/repositories/{repository_id}/memory",
    response_model=dict[str, Any],
    summary="Repository memory snapshot + learned weights",
)
async def get_repository_memory(
    repository_id: uuid.UUID,
    store: Annotated[OrchestratorStore, Depends(get_dashboard_store)],
    principal: Annotated[TokenPrincipal, Depends(get_principal)],
) -> dict[str, Any]:
    require_repo_access(repository_id, principal)
    detail = await store.repository_memory(repository_id)
    if detail is None:
        raise _not_found(f"memory for repository '{repository_id}' not found")
    return detail


@router.get(
    "/repositories/{repository_id}/feedback",
    response_model=list[dict[str, Any]],
    summary="Feedback events for a repository",
)
async def get_repository_feedback(
    repository_id: uuid.UUID,
    store: Annotated[OrchestratorStore, Depends(get_dashboard_store)],
    principal: Annotated[TokenPrincipal, Depends(get_principal)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[dict[str, Any]]:
    require_repo_access(repository_id, principal)
    return await store.repository_feedback(repository_id, limit=limit, offset=offset)


@router.get(
    "/reviews/{review_id}/findings",
    response_model=list[dict[str, Any]],
    summary="Paginated findings for a review",
)
async def list_review_findings(
    review_id: uuid.UUID,
    store: Annotated[OrchestratorStore, Depends(get_dashboard_store)],
    status_filter: Annotated[
        FindingStatus | None,
        Query(alias="status", description="Filter by publication_status"),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[dict[str, Any]]:
    return await store.findings_for_review(
        review_id, status=status_filter, limit=limit, offset=offset
    )


@router.get(
    "/reviews/{review_id}/iterations",
    response_model=list[dict[str, Any]],
    summary="Iteration summary for a review",
)
async def list_review_iterations(
    review_id: uuid.UUID,
    store: Annotated[OrchestratorStore, Depends(get_dashboard_store)],
) -> list[dict[str, Any]]:
    return await store.iterations_for_review(review_id)


@router.get(
    "/metrics",
    response_model=dict[str, Any],
    summary="Dashboard metrics rollup",
)
async def metrics(
    store: Annotated[OrchestratorStore, Depends(get_dashboard_store)],
) -> dict[str, Any]:
    return await store.metrics_rollup()
