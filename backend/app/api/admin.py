"""Admin write API (Phase 15 part 2b, FR-7.3 — docs/API.md §2-3).

HTTP surface for the two admin write actions plus the audit trail, gated by
bearer-token authn and the operator/admin role gates (docs/SECURITY.md §4,
§10-12):

- ``POST /api/v1/reviews/{review_id}/rerun`` — re-enqueue a review: a fresh
  ``Review`` (``mode=MANUAL_RERUN``) is created for the same pull request with
  the priority recomputed from stored PR state, then handed to the queue
  dispatcher. Returns ``202`` + the new run's tracking payload.
- ``PATCH /api/v1/repositories/{repository_id}/settings`` — merge validated
  review settings (urgency, budget caps, safety gates, ARUM weight overrides,
  temporal decay) into ``Repository.review_settings``. Weights are validated
  through the real ARUM override path (unknown/non-finite keys are rejected).
- ``GET /api/v1/audit-log`` — the append-only admin audit trail (admin role;
  scoped tokens see only their repositories).

Every write appends an :class:`AdminAuditWrite` carrying the acting principal
digest + before/after state, so admin behaviour is traceable and the audit
records never outlive the endpoint surface they were made for
(docs/ROADMAP.md Phase 15 split note).
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, model_validator

from app.adaptive.weights import ArumConfigurationError, ArumWeights
from app.config.settings import get_settings
from app.models.enums import ReviewMode, ReviewStatus
from app.orchestrator.persistence import (
    AdminAuditWrite,
    OrchestratorStore,
    SqlOrchestratorStore,
)
from app.queue.contracts import ReviewDispatcher
from app.queue.dispatch import CeleryDispatcher
from app.security.auth import get_principal, require_repo_access, require_role
from app.security.tokens import TokenPrincipal
from app.webhooks.dispatch import LoggingDispatcher

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["admin"], dependencies=[Depends(get_principal)])

_store: OrchestratorStore | None = None
_dispatcher: ReviewDispatcher | None = None


def get_admin_store() -> OrchestratorStore:
    global _store
    if _store is None:
        _store = SqlOrchestratorStore()
    return _store


def get_admin_dispatcher() -> ReviewDispatcher:
    global _dispatcher
    if _dispatcher is None:
        _dispatcher = (
            CeleryDispatcher()
            if get_settings().queue_dispatch_provider == "celery"
            else LoggingDispatcher()
        )
    return _dispatcher


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code, detail={"code": code, "message": message}
    )


# --- request / response models --------------------------------------------------


class RerunAccepted(BaseModel):
    """Tracking payload for a manually re-enqueued review run (FR-7.3)."""

    review_id: uuid.UUID
    mode: str = ReviewMode.MANUAL_RERUN.value
    status: str = ReviewStatus.QUEUED.value
    priority_score: float
    risk_class: str | None
    pr_number: int
    dispatch_note: str


class ReviewBudgetPatch(BaseModel):
    low: int = Field(ge=1)
    medium: int = Field(ge=1)
    high: int = Field(ge=1)


class SafetyGatesPatch(BaseModel):
    high_confidence: float = Field(ge=0.0, le=1.0)
    low_confidence: float = Field(ge=0.0, le=1.0)
    high_redundancy: float = Field(ge=0.0, le=1.0)


class DecayPatch(BaseModel):
    temporal_decay_days: int = Field(ge=1)
    decay_lambda: float = Field(gt=0.0)


class RepositorySettingsPatch(BaseModel):
    """Partial settings update; at least one field must be present.

    ``arum_weights`` keys are validated against the real ARUM override grammar
    (``ArumWeights.with_overrides``) so a typo cannot silently change policy.
    """

    urgency: float | None = Field(default=None, ge=0.0, le=1.0)
    review_budget: ReviewBudgetPatch | None = None
    safety_gates: SafetyGatesPatch | None = None
    arum_weights: dict[str, float] | None = None
    decay: DecayPatch | None = None

    @model_validator(mode="after")
    def _requires_a_change(self) -> RepositorySettingsPatch:
        if all(
            value is None
            for value in (
                self.urgency,
                self.review_budget,
                self.safety_gates,
                self.arum_weights,
                self.decay,
            )
        ):
            raise ValueError("at least one settings field is required")
        return self


# --- helpers --------------------------------------------------------------------


def _merge_settings(current: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Merge a validated partial settings patch into the current JSON dict.

    Nested maps (budget / gates / decay / weights) merge key-wise so a partial
    update never clobbers sibling values the caller did not touch; scalar fields
    replace outright.
    """
    merged = dict(current)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = {**merged[key], **value}
        else:
            merged[key] = value
    return merged


# --- endpoints ---------------------------------------------------------------


@router.post(
    "/reviews/{review_id}/rerun",
    response_model=RerunAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Re-enqueue a review run (re-runs the state machine)",
    responses={
        202: {"description": "fresh MANUAL_RERUN run accepted onto the queue"},
        403: {"description": "insufficient role or repository out of scope"},
        404: {"description": "review not found"},
    },
)
async def rerun_review(
    review_id: uuid.UUID,
    store: Annotated[OrchestratorStore, Depends(get_admin_store)],
    principal: Annotated[TokenPrincipal, Depends(require_role("operator"))],
    dispatcher: Annotated[ReviewDispatcher, Depends(get_admin_dispatcher)],
) -> RerunAccepted:
    """Create a fresh ``MANUAL_RERUN`` run for the review's PR and enqueue it."""
    repository_id = await store.review_repository(review_id)
    if repository_id is None:
        raise _error(
            status.HTTP_404_NOT_FOUND,
            "review_not_found",
            f"review '{review_id}' not found",
        )
    require_repo_access(repository_id, principal)

    delivery_id = f"rerun:{uuid.uuid4().hex}"
    outcome = await store.create_rerun_review(review_id, delivery_id=delivery_id)
    if outcome is None:
        raise _error(
            status.HTTP_404_NOT_FOUND,
            "review_not_found",
            f"review '{review_id}' not found",
        )
    result = await dispatcher.dispatch(
        uuid.UUID(str(outcome["review_id"])), delivery_id
    )

    await store.record_audit(
        AdminAuditWrite(
            action="review.rerun",
            target_kind="review",
            target_id=str(review_id),
            repository_id=outcome["repository_id"],
            principal_token_hash=principal.token_hash,
            principal_role=principal.role,
            principal_label=principal.label,
            before={"source_review_id": str(review_id)},
            after={
                "rerun_review_id": outcome["review_id"],
                "status": ReviewStatus.QUEUED.value,
                "delivery_id": delivery_id,
            },
        )
    )
    logger.info(
        "admin_review_rerun",
        source_review_id=str(review_id),
        rerun_review_id=outcome["review_id"],
        repository_id=outcome["repository_id"],
        priority_score=outcome["priority_score"],
        dispatched=result.dispatched,
    )
    return RerunAccepted(
        review_id=uuid.UUID(str(outcome["review_id"])),
        mode=outcome["mode"],
        priority_score=float(outcome["priority_score"]),
        risk_class=outcome["risk_class"],
        pr_number=int(outcome["pr_number"]),
        dispatch_note=result.note,
    )


@router.patch(
    "/repositories/{repository_id}/settings",
    response_model=dict[str, Any],
    summary="Update review settings for a repository (audited)",
    responses={
        403: {"description": "insufficient role or repository out of scope"},
        404: {"description": "repository not found"},
        422: {"description": "invalid settings"},
    },
)
async def update_repository_settings(
    repository_id: uuid.UUID,
    payload: RepositorySettingsPatch,
    store: Annotated[OrchestratorStore, Depends(get_admin_store)],
    principal: Annotated[TokenPrincipal, Depends(require_role("operator"))],
) -> dict[str, Any]:
    """Merge validated review settings and record the before/after audit row."""
    require_repo_access(repository_id, principal)
    detail = await store.repository_detail(repository_id)
    if detail is None:
        raise _error(
            status.HTTP_404_NOT_FOUND,
            "repository_not_found",
            f"repository '{repository_id}' not found",
        )

    if payload.arum_weights:
        try:
            ArumWeights().with_overrides(payload.arum_weights)
        except ArumConfigurationError as exc:
            raise _error(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "invalid_settings",
                str(exc),
            ) from exc

    current = dict(detail.get("review_settings") or {})
    merged = _merge_settings(current, payload.model_dump(exclude_unset=True))
    saved = await store.patch_repository_settings(repository_id, merged)
    if saved is None:
        raise _error(
            status.HTTP_404_NOT_FOUND,
            "repository_not_found",
            f"repository '{repository_id}' not found",
        )

    await store.record_audit(
        AdminAuditWrite(
            action="repository.settings.update",
            target_kind="repository",
            target_id=str(repository_id),
            repository_id=str(repository_id),
            principal_token_hash=principal.token_hash,
            principal_role=principal.role,
            principal_label=principal.label,
            before=current,
            after=dict(saved),
        )
    )
    logger.info(
        "admin_repository_settings",
        repository_id=str(repository_id),
        changed=list(payload.model_dump(exclude_unset=True).keys()),
    )
    return {"repository_id": str(repository_id), "review_settings": dict(saved)}


@router.get(
    "/audit-log",
    response_model=list[dict[str, Any]],
    summary="Admin audit trail (append-only)",
    responses={403: {"description": "requires admin role"}},
)
async def audit_log(
    store: Annotated[OrchestratorStore, Depends(get_admin_store)],
    principal: Annotated[TokenPrincipal, Depends(require_role("admin"))],
    action: Annotated[str | None, Query(description="Filter by exact action")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[dict[str, Any]]:
    """Newest-first audit rows; scoped admin tokens see only their repositories."""
    return await store.audit_log(
        limit=limit,
        offset=offset,
        action=action,
        repository_ids=(list(principal.repos) if principal.repos is not None else None),
    )
