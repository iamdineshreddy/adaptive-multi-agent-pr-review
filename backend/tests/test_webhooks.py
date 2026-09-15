"""Unit tests for the webhook service (docs/ROADMAP.md Phase 3).

All tests are database-free: persistence/dispatch dependencies are replaced with
double objects, so the HTTP, signature, validation and priority behaviour are what
is exercised here.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from collections.abc import Iterator, Mapping

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import app.webhooks.router as router_module
from app.config.settings import Settings, get_settings
from app.main import app
from app.queue.dispatch import CeleryDispatcher
from app.webhooks.dispatch import DispatchResult, LoggingDispatcher
from app.webhooks.ingest import IngestOutcome
from app.webhooks.priority import (
    RiskClass,
    change_impact,
    compute_priority,
    risk_class_for,
)
from app.webhooks.ratelimit import MemoryRateLimiter
from app.webhooks.router import (
    get_dispatcher,
    get_ingester,
    get_limiter,
    get_verifier,
)
from app.webhooks.schemas import PullRequestWebhookEvent
from app.webhooks.security import SignatureVerifier

SECRET = "test-webhook-secret"
WEBHOOK_URL = "/api/v1/webhooks/github"
_DELIVERY: str = "11111111-2222-3333-4444-555555555555"


def sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def valid_payload(action: str = "opened") -> dict:
    return {
        "action": action,
        "number": 7,
        "pull_request": {
            "id": 111,
            "number": 7,
            "title": "Add feature",
            "state": "open",
            "draft": False,
            "user": {"login": "dev1", "id": 1},
            "base": {"ref": "main", "sha": "abc", "label": "org:main"},
            "head": {"ref": "feat", "sha": "def", "label": "org:feat"},
            "changed_files": 3,
            "additions": 40,
            "deletions": 10,
        },
        "repository": {
            "id": 42,
            "full_name": "acme/widgets",
            "default_branch": "main",
            "language": "python",
            "private": True,
        },
        "installation": {"id": 9},
        "sender": {"login": "dev1", "id": 1},
    }


class RecordingIngester:
    def __init__(self) -> None:
        self.calls: list[tuple[PullRequestWebhookEvent, str]] = []
        self._reviews: dict[str, uuid.UUID] = {}

    async def ingest(
        self, event: PullRequestWebhookEvent, delivery_id: str
    ) -> IngestOutcome:
        self.calls.append((event, delivery_id))
        existing = self._reviews.get(delivery_id)
        if existing is not None:
            return IngestOutcome(review_id=existing, created=False, priority=None)
        created = uuid.uuid4()
        self._reviews[delivery_id] = created
        return IngestOutcome(review_id=created, created=True, priority=None)


class RecordingDispatcher:
    def __init__(self) -> None:
        self.calls: list[tuple[uuid.UUID, str]] = []

    async def dispatch(self, review_id: object, delivery_id: str) -> DispatchResult:
        self.calls.append((uuid.UUID(str(review_id)), delivery_id))
        return DispatchResult(dispatched=True, note="recorded")


@pytest.fixture
def webhook_client() -> Iterator[
    tuple[TestClient, RecordingIngester, RecordingDispatcher]
]:
    ingester = RecordingIngester()
    dispatcher = RecordingDispatcher()

    def fake_verifier() -> SignatureVerifier:
        return SignatureVerifier(SECRET)

    app.dependency_overrides[get_verifier] = fake_verifier
    app.dependency_overrides[get_ingester] = lambda: ingester
    app.dependency_overrides[get_dispatcher] = lambda: dispatcher
    app.dependency_overrides[get_limiter] = lambda: MemoryRateLimiter(
        per_ip_per_minute=1000, per_repo_burst=100
    )
    with TestClient(app) as client:
        yield client, ingester, dispatcher
    app.dependency_overrides.clear()


def _headers(
    delivery: str = _DELIVERY, sig: str | None = None, event: str = "pull_request"
) -> dict:
    default_signature = sign(json.dumps(valid_payload()).encode())
    return {
        "X-GitHub-Event": event,
        "X-GitHub-Delivery": delivery,
        "X-Hub-Signature-256": sig or default_signature,
    }


# --- security --------------------------------------------------------------------


class TestSignatureVerifier:
    def test_accepts_valid_sha256(self) -> None:
        verifier = SignatureVerifier(SECRET)
        assert verifier.verify(b"payload", sign(b"payload"))

    def test_rejects_tampered_body(self) -> None:
        verifier = SignatureVerifier(SECRET)
        assert not verifier.verify(b"tampered", sign(b"payload"))

    def test_rejects_wrong_secret(self) -> None:
        verifier = SignatureVerifier("other-secret")
        assert not verifier.verify(b"payload", sign(b"payload"))

    def test_rejects_malformed_header(self) -> None:
        verifier = SignatureVerifier(SECRET)
        assert not verifier.verify(b"payload", "not-a-signature")

    def test_legacy_sha1_supported(self) -> None:
        verifier = SignatureVerifier(SECRET)
        expected = (
            "sha1=" + hmac.new(SECRET.encode(), b"payload", hashlib.sha1).hexdigest()
        )
        assert verifier.verify(b"payload", None, expected)

    def test_unconfigured_reports_false(self) -> None:
        assert not SignatureVerifier("").configured
        assert SignatureVerifier(SECRET).configured


# --- schemas ---------------------------------------------------------------------


class TestWebhookSchemas:
    def test_valid_payload_parses(self) -> None:
        event = PullRequestWebhookEvent.model_validate(valid_payload())
        assert event.pull_request.number == 7
        assert event.repository.full_name == "acme/widgets"

    def test_extra_fields_ignored(self) -> None:
        payload = valid_payload()
        payload["extra_top_level"] = {"anything": 1}
        payload["pull_request"]["labels"] = ["x"]
        event = PullRequestWebhookEvent.model_validate(payload)
        assert event.action == "opened"

    def test_missing_required_field_rejected(self) -> None:
        payload = valid_payload()
        del payload["repository"]
        with pytest.raises(ValidationError):
            PullRequestWebhookEvent.model_validate(payload)

    def test_wrong_type_rejected(self) -> None:
        payload = valid_payload()
        payload["pull_request"]["changed_files"] = "three"
        with pytest.raises(ValidationError):
            PullRequestWebhookEvent.model_validate(payload)


# --- priority scoring --------------------------------------------------------------


class TestPriority:
    def test_change_impact_increases_with_size(self) -> None:
        small = change_impact(1, 10, 0)
        large = change_impact(120, 4000, 500)
        assert large > small
        assert 0.0 <= large <= 1.0

    def test_supported_factors_only(self) -> None:
        settings = Settings()
        result = compute_priority(
            PullRequestWebhookEvent.model_validate(valid_payload()).pull_request,
            settings,
        )
        assert result.factors.security_risk == 0.0
        assert result.factors.historical_risk == 0.0
        assert result.factors.component_criticality == 0.0
        assert result.factors.dependency_risk == 0.0
        assert result.factors.change_impact > 0.0

    def test_repo_urgency_raises_score(self) -> None:
        settings = Settings()
        pr = PullRequestWebhookEvent.model_validate(valid_payload()).pull_request
        neutral = compute_priority(pr, settings)
        urgent = compute_priority(pr, settings, repo_urgency=1.0)
        assert urgent.score > neutral.score

    def test_risk_class_thresholds(self) -> None:
        settings = Settings()
        assert risk_class_for(0.5, settings) == RiskClass.LOW
        assert risk_class_for(3.0, settings) == RiskClass.MEDIUM
        assert risk_class_for(6.0, settings) == RiskClass.MEDIUM
        assert risk_class_for(6.1, settings) == RiskClass.HIGH

    def test_score_uses_documented_scale(self) -> None:
        settings = Settings()
        assert abs(settings.priority_scale - 10.0) < 1e-6
        assert (
            abs(
                sum(
                    (
                        settings.priority_w_security,
                        settings.priority_w_impact,
                        settings.priority_w_history,
                        settings.priority_w_component,
                        settings.priority_w_dependency,
                        settings.priority_w_urgency,
                    )
                )
                - 1.0
            )
            < 1e-6
        )


# --- rate limiter -----------------------------------------------------------------


class TestRateLimiter:
    def test_ip_bucket_enforces_per_minute_cap(self) -> None:
        limiter = MemoryRateLimiter(per_ip_per_minute=3, per_repo_burst=10)
        assert limiter.allow([("ip", "1.2.3.4")])
        assert limiter.allow([("ip", "1.2.3.4")])
        assert limiter.allow([("ip", "1.2.3.4")])
        assert not limiter.allow([("ip", "1.2.3.4")])

    def test_repo_bucket_independent_of_ip(self) -> None:
        limiter = MemoryRateLimiter(per_ip_per_minute=1, per_repo_burst=1)
        assert limiter.allow([("ip", "a"), ("repo", 42)])
        assert not limiter.allow([("ip", "a"), ("repo", 42)])
        # fresh repo key -> ip cap is the only constraint; a new IP passes
        assert limiter.allow([("ip", "b"), ("repo", 43)])


# --- router -----------------------------------------------------------------------


def _post(
    client: TestClient,
    payload: Mapping | bytes,
    delivery: str = _DELIVERY,
    event: str = "pull_request",
    secret: str = SECRET,
):
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    headers = _headers(delivery=delivery, sig=sign(body, secret), event=event)
    return client.post(WEBHOOK_URL, content=body, headers=headers)


class TestWebhookRouter:
    def test_opened_event_accepted_202(
        self, webhook_client: tuple[TestClient, RecordingIngester, RecordingDispatcher]
    ) -> None:
        client, ingester, dispatcher = webhook_client
        response = _post(client, valid_payload())
        assert response.status_code == 202
        body = response.json()
        assert body["queued"] is True
        assert body["delivery_id"] == _DELIVERY
        uuid.UUID(body["review_id"])
        assert len(ingester.calls) == 1
        assert len(dispatcher.calls) == 1
        assert dispatcher.calls[0][1] == _DELIVERY

    def test_bad_signature_rejected_401(
        self, webhook_client: tuple[TestClient, RecordingIngester, RecordingDispatcher]
    ) -> None:
        client, _, _ = webhook_client
        response = _post(client, valid_payload(), secret="wrong-secret")
        assert response.status_code == 401
        assert response.json()["detail"]["code"] == "invalid_signature"

    def test_missing_signature_rejected_401(
        self, webhook_client: tuple[TestClient, RecordingIngester, RecordingDispatcher]
    ) -> None:
        client, _, _ = webhook_client
        headers = _headers(sig=None)
        headers.pop("X-Hub-Signature-256")
        response = client.post(
            WEBHOOK_URL, content=json.dumps(valid_payload()).encode(), headers=headers
        )
        assert response.status_code == 401

    def test_ping_returns_200(
        self, webhook_client: tuple[TestClient, RecordingIngester, RecordingDispatcher]
    ) -> None:
        client, ingester, _ = webhook_client
        response = _post(client, {"zen": "hello", "hook_id": 1}, event="ping")
        assert response.status_code == 200
        assert response.json() == {"ok": True, "event": "ping"}
        assert ingester.calls == []

    def test_invalid_json_rejected_400(
        self, webhook_client: tuple[TestClient, RecordingIngester, RecordingDispatcher]
    ) -> None:
        client, _, _ = webhook_client
        response = _post(client, b"{not valid json")
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "invalid_json"

    def test_schema_rejection_422(
        self, webhook_client: tuple[TestClient, RecordingIngester, RecordingDispatcher]
    ) -> None:
        client, _, _ = webhook_client
        payload = valid_payload()
        del payload["repository"]
        response = _post(client, payload)
        assert response.status_code == 422
        detail = response.json()["detail"]
        assert detail["code"] == "schema_rejected"
        assert any(fe["loc"] == "repository" for fe in detail["field_errors"])

    def test_unsupported_action_ignored_200(
        self, webhook_client: tuple[TestClient, RecordingIngester, RecordingDispatcher]
    ) -> None:
        client, ingester, _ = webhook_client
        response = _post(client, valid_payload(action="closed"))
        assert response.status_code == 200
        assert response.json()["ignored"] is True
        assert ingester.calls == []

    def test_unknown_event_rejected_400(
        self, webhook_client: tuple[TestClient, RecordingIngester, RecordingDispatcher]
    ) -> None:
        client, ingester, _ = webhook_client
        response = _post(client, {"something": 1}, event="issues")
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "unsupported_event"
        assert ingester.calls == []

    def test_duplicate_delivery_is_idempotent(
        self, webhook_client: tuple[TestClient, RecordingIngester, RecordingDispatcher]
    ) -> None:
        client, _, dispatcher = webhook_client
        first = _post(client, valid_payload())
        second = _post(client, valid_payload())
        assert first.status_code == 202
        assert second.status_code == 202
        assert first.json()["review_id"] == second.json()["review_id"]
        # the duplicate is not re-created but the same review is re-staged for the queue
        assert len(dispatcher.calls) == 2
        assert dispatcher.calls[0] == dispatcher.calls[1]

    def test_repo_burst_rate_limit_429(self) -> None:
        ingester = RecordingIngester()
        dispatcher = RecordingDispatcher()
        limiter = MemoryRateLimiter(per_ip_per_minute=2, per_repo_burst=2)
        app.dependency_overrides.clear()
        app.dependency_overrides[get_verifier] = lambda: SignatureVerifier(SECRET)
        app.dependency_overrides[get_ingester] = lambda: ingester
        app.dependency_overrides[get_dispatcher] = lambda: dispatcher
        app.dependency_overrides[get_limiter] = lambda: limiter
        with TestClient(app) as client:
            assert _post(client, valid_payload()).status_code == 202
            assert _post(client, valid_payload()).status_code == 202
            third = _post(client, valid_payload())
            assert third.status_code == 429


# --- Phase 16: real LoggingDispatcher provider (broker-free fallback) ---------


class TestLoggingDispatcher:
    async def test_records_dispatch_intent(self) -> None:
        """The Phase 3 fallback never pretends to enqueue."""
        result = await LoggingDispatcher().dispatch(uuid.uuid4(), _DELIVERY)
        assert isinstance(result, DispatchResult)
        assert result.dispatched is False
        assert "pr_ingestion_queue" in result.note


class TestDispatcherSelection:
    """``get_dispatcher`` must honour ``queue_dispatch_provider`` settings."""

    def test_logging_provider_selected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(router_module, "_dispatcher", None)
        monkeypatch.setattr(get_settings(), "queue_dispatch_provider", "logging")
        assert isinstance(get_dispatcher(), LoggingDispatcher)

    def test_celery_provider_selected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(router_module, "_dispatcher", None)
        monkeypatch.setattr(get_settings(), "queue_dispatch_provider", "celery")
        assert isinstance(get_dispatcher(), CeleryDispatcher)


class TestWebhookRealDispatcher:
    def test_real_logging_dispatcher_wired_through_router(self) -> None:
        """POST reaches the production fallback and returns 202 accepted."""
        ingester = RecordingIngester()
        app.dependency_overrides.clear()
        app.dependency_overrides[get_verifier] = lambda: SignatureVerifier(SECRET)
        app.dependency_overrides[get_ingester] = lambda: ingester
        app.dependency_overrides[get_dispatcher] = lambda: LoggingDispatcher()
        app.dependency_overrides[get_limiter] = lambda: MemoryRateLimiter(
            per_ip_per_minute=100, per_repo_burst=100
        )
        with TestClient(app) as client:
            response = _post(client, valid_payload())
        app.dependency_overrides.clear()
        assert response.status_code == 202
        assert len(ingester.calls) == 1
