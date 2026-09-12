"""GitHub webhook endpoint (docs/API.md §1, docs/ARCHITECTURE.md §4.1).

Request lifecycle: rate limit -> signature verify -> event routing -> validate ->
persist (idempotent) -> return immediately. No AI work runs on this path.
"""

from __future__ import annotations

import json
import uuid
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, ValidationError

from app.config.settings import get_settings
from app.webhooks.dispatch import LoggingDispatcher, ReviewDispatcher
from app.webhooks.ingest import ReviewIngester, SqlReviewIngester
from app.webhooks.ratelimit import MemoryRateLimiter
from app.webhooks.schemas import (
    ErrorDetail,
    FieldError,
    PullRequestWebhookEvent,
    WebhookAccepted,
    WebhookError,
    WebhookIgnored,
)
from app.webhooks.security import SignatureVerifier

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

_SUPPORTED_EVENTS = {"pull_request", "ping"}
_PR_ACTIONS = {"opened", "synchronize", "ready_for_review", "reopened"}


class WebhookPing(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ok: bool
    event: str = "ping"


# --- dependencies -------------------------------------------------------------

_verifier: SignatureVerifier | None = None
_limiter: MemoryRateLimiter | None = None
_ingester: ReviewIngester | None = None
_dispatcher: ReviewDispatcher | None = None


def get_verifier() -> SignatureVerifier:
    global _verifier
    if _verifier is None:
        _verifier = SignatureVerifier(get_settings().github_webhook_secret)
    return _verifier


def get_limiter() -> MemoryRateLimiter:
    global _limiter
    if _limiter is None:
        s = get_settings()
        _limiter = MemoryRateLimiter(
            per_ip_per_minute=s.rate_limit_webhook_per_minute,
            per_repo_burst=s.rate_limit_webhook_repo_burst,
        )
    return _limiter


def get_ingester() -> ReviewIngester:
    global _ingester
    if _ingester is None:
        _ingester = SqlReviewIngester()
    return _ingester


def get_dispatcher() -> ReviewDispatcher:
    global _dispatcher
    if _dispatcher is None:
        _dispatcher = LoggingDispatcher()
    return _dispatcher


# --- helpers -------------------------------------------------------------------


def _error(
    code: str, message: str, field_errors: list[FieldError] | None = None
) -> WebhookError:
    return WebhookError(
        detail=ErrorDetail(code=code, message=message, field_errors=field_errors or [])
    )


def _field_errors(err: ValidationError) -> list[FieldError]:
    return [
        FieldError(loc=".".join(str(p) for p in e["loc"]), msg=e["msg"])
        for e in err.errors()
    ]


def _raise(status_code: int, error: WebhookError) -> HTTPException:
    return HTTPException(status_code=status_code, detail=error.detail.model_dump())


# --- endpoint ------------------------------------------------------------------


@router.post(
    "/github",
    response_model=WebhookAccepted | WebhookIgnored | WebhookPing,
    summary="Receive a GitHub webhook delivery",
)
async def github_webhook(
    request: Request,
    response: Response,
    verifier: Annotated[SignatureVerifier, Depends(get_verifier)],
    limiter: Annotated[MemoryRateLimiter, Depends(get_limiter)],
    ingester: Annotated[
        ReviewIngester,
        Depends(get_ingester),
    ],
    dispatcher: Annotated[
        ReviewDispatcher,
        Depends(get_dispatcher),
    ],
) -> WebhookAccepted | WebhookIgnored | WebhookPing:
    """Rate-limit, verify, then persist the event. Returns before any AI work."""
    body = await request.body()
    event_name = request.headers.get("X-GitHub-Event") or ""
    delivery_id = request.headers.get("X-GitHub-Delivery") or ""
    client_ip = request.client.host if request.client else "test"

    if not limiter.allow([("ip", client_ip)]):
        raise _raise(
            status.HTTP_429_TOO_MANY_REQUESTS,
            _error("rate_limited", "Webhook rate limit exceeded"),
        )

    if not verifier.configured:
        raise _raise(
            status.HTTP_401_UNAUTHORIZED,
            _error("server_misconfigured", "Webhook secret is not configured"),
        )
    if not verifier.verify(
        body,
        request.headers.get("X-Hub-Signature-256"),
        request.headers.get("X-Hub-Signature"),
    ):
        raise _raise(
            status.HTTP_401_UNAUTHORIZED,
            _error("invalid_signature", "Signature verification failed"),
        )

    if event_name == "ping":
        response.status_code = status.HTTP_200_OK
        return WebhookPing(ok=True)

    if event_name not in _SUPPORTED_EVENTS:
        if get_settings().webhook_unknown_event_action == "ignore":
            response.status_code = status.HTTP_200_OK
            return WebhookIgnored(
                ignored=True, reason=f"event '{event_name}' not handled"
            )
        raise _raise(
            status.HTTP_400_BAD_REQUEST,
            _error("unsupported_event", f"Event '{event_name}' is not supported"),
        )

    if not delivery_id:
        raise _raise(
            status.HTTP_400_BAD_REQUEST,
            _error("missing_delivery_id", "X-GitHub-Delivery header is required"),
        )

    outcome_model, http_code = await _handle_pull_request_event(
        body, delivery_id, ingester, dispatcher
    )
    response.status_code = http_code
    return outcome_model


async def _handle_pull_request_event(
    body: bytes,
    delivery_id: str,
    ingester: ReviewIngester,
    dispatcher: ReviewDispatcher,
) -> tuple[WebhookAccepted | WebhookIgnored, int]:
    try:
        raw = json.loads(body)
    except json.JSONDecodeError as exc:
        raise _raise(
            status.HTTP_400_BAD_REQUEST,
            _error("invalid_json", f"Payload is not valid JSON: {exc.msg}"),
        ) from exc

    if not isinstance(raw, dict):
        raise _raise(
            status.HTTP_400_BAD_REQUEST,
            _error("invalid_payload", "Payload must be a JSON object"),
        )

    try:
        event = PullRequestWebhookEvent.model_validate(raw)
    except ValidationError as exc:
        raise _raise(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            _error(
                "schema_rejected",
                "Payload failed schema validation",
                _field_errors(exc),
            ),
        ) from exc

    if event.action not in _PR_ACTIONS:
        logger.info(
            "webhook_ignored_action",
            delivery_id=delivery_id,
            action=event.action,
            repository=event.repository.full_name,
        )
        return (
            WebhookIgnored(
                ignored=True, reason=f"action '{event.action}' not reviewed"
            ),
            status.HTTP_200_OK,
        )

    outcome = await ingester.ingest(event, delivery_id)
    await dispatcher.dispatch(outcome.review_id, delivery_id)
    logger.info(
        "webhook_accepted",
        review_id=str(outcome.review_id),
        delivery_id=delivery_id,
        action=event.action,
        repository=event.repository.full_name,
        created=outcome.created,
        priority_score=outcome.priority.score if outcome.priority else None,
    )
    return (
        WebhookAccepted(
            review_id=uuid.UUID(str(outcome.review_id)), delivery_id=delivery_id
        ),
        status.HTTP_202_ACCEPTED,
    )
