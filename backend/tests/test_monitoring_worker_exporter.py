"""Phase 18: worker-process metrics exporter tests.

Covers the separate ``worker:8001`` scrape endpoint (docs/OBSERVABILITY.md
§Prometheus): bearer-token gating, health probe, and the rollup -> gauge -> text
pipeline shared with the API's monitoring endpoint. Runs entirely offline (no
DB, no network).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.api.dashboard import get_dashboard_store
from app.monitoring.worker_exporter import worker_app
from app.orchestrator.persistence import MemoryOrchestratorStore
from app.security.auth import get_principal
from app.security.tokens import TokenPrincipal

_ADMIN = TokenPrincipal(token_hash="test-admin", role="admin", label="test")


@pytest.fixture()
def exporter_client() -> Iterator[TestClient]:
    app_store = MemoryOrchestratorStore()
    worker_app.dependency_overrides[get_dashboard_store] = lambda: app_store
    worker_app.dependency_overrides[get_principal] = lambda: _ADMIN
    with TestClient(worker_app) as client:
        yield client
    worker_app.dependency_overrides.clear()


def test_health_endpoint() -> None:
    with TestClient(worker_app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["service"] == "adaptive-review-worker"


def test_metrics_requires_bearer_token() -> None:
    """The real gate runs when nothing is overridden; unknown -> 401."""
    with TestClient(worker_app) as client:
        response = client.get("/api/v1/monitoring/prometheus")
    assert response.status_code == 401


def test_metrics_returns_prometheus_text(exporter_client: TestClient) -> None:
    response = exporter_client.get("/api/v1/monitoring/prometheus")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "adaptive_review_webhook_events_total" in response.text
    assert "adaptive_review_reviews_by_status" in response.text


def test_metrics_never_fabricates_counters(exporter_client: TestClient) -> None:
    response = exporter_client.get("/api/v1/monitoring/prometheus")
    assert response.status_code == 200
    assert "adaptive_review_webhook_events_total 1.0" not in response.text
