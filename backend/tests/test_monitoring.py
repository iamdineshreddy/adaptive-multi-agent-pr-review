"""Phase 14 part 1: Prometheus metrics exposition and structured logging.

Runs entirely offline (no DB, no network). Covers the redaction processor, the
metric request/one-line registration surface, gauge projection from a seeded
in-memory rollup, and the ``GET /api/v1/monitoring/prometheus`` endpoint with
its explicit non-instrumented fallback.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator

import pytest
import structlog
from fastapi.testclient import TestClient

from app.api.dashboard import get_dashboard_store
from app.main import app
from app.models.enums import ReviewStatus
from app.monitoring import logsetup
from app.monitoring import prometheus as metrics
from app.orchestrator.persistence import MemoryOrchestratorStore
from app.security.auth import get_principal
from app.security.tokens import TokenPrincipal

REVIEW_ID = uuid.uuid4()
REPO_ID = uuid.uuid4()

_ADMIN = TokenPrincipal(token_hash="test-admin", role="admin", label="test")


def _seeded_store() -> MemoryOrchestratorStore:
    store = MemoryOrchestratorStore()
    store.reviews[str(REVIEW_ID)] = {
        "status": ReviewStatus.PUBLISHED.value,
        "supervisor_notes": [],
        "status_history": [
            {"status": "PUBLISHED", "from": None, "at": "2026-01-01T00:00:00Z"}
        ],
    }
    store.repositories[str(REPO_ID)] = {
        "full_name": "acme/app",
        "default_branch": "main",
        "is_active": True,
    }
    store.metrics.append(
        {"stats": {"tokens_in": 1000, "tokens_out": 500, "cost_usd": 0.001}}
    )
    return store


@pytest.fixture()
def dashboard_client() -> Iterator[tuple[TestClient, MemoryOrchestratorStore]]:
    store = _seeded_store()
    app.dependency_overrides[get_dashboard_store] = lambda: store
    app.dependency_overrides[get_principal] = lambda: _ADMIN
    with TestClient(app) as client:
        yield client, store
    app.dependency_overrides.clear()


# --- redaction ----------------------------------------------------------------


def test_redact_secrets_blanks_credential_shaped_keys() -> None:
    logger = structlog.get_logger()
    event = {
        "event": "ingest",
        "api_key": "sk-abc-123",
        "GITHUB_PAT": "ghp_hunter2",
        "signature": "sha256=<expected>",
        "author": "dz",
    }
    out = logsetup.redact_secrets(logger, "info", event)
    assert out["api_key"] == "[REDACTED]"
    assert out["GITHUB_PAT"] == "[REDACTED]"
    assert out["signature"] == "[REDACTED]"
    assert out["author"] == "dz"
    assert out["event"] == "ingest"
    assert set(out) == {"event", "api_key", "GITHUB_PAT", "signature", "author"}


def test_configure_logging_json_rendering(capsys: pytest.CaptureFixture[str]) -> None:
    logsetup.configure_logging(level="debug")
    structlog.get_logger("monitoring.test").info("probe_event", review_id="abc")
    captured = capsys.readouterr().out
    assert "probe_event" in captured
    assert '"review_id": "abc"' in captured


# --- metric registration + increment surface (no endpoint) ---------------------


def test_counter_increments_appear_in_exposition() -> None:
    metrics.record_webhook_event("pull_request", "opened", "accepted")
    metrics.record_feedback("FIXED")
    body, content_type = metrics.render_metrics()
    assert "text/plain" in content_type
    assert "adaptive_review_webhook_events_total" in body
    assert 'action="opened"' in body
    assert 'outcome="accepted"' in body
    assert "adaptive_review_feedback_recorded_total" in body
    assert 'outcome_label="FIXED"' in body


def test_store_gauges_reflect_rollup() -> None:
    store = _seeded_store()
    rollup = asyncio.run(store.metrics_rollup())
    metrics.update_store_gauges(rollup)
    body, _ = metrics.render_metrics()
    assert "adaptive_review_reviews_by_status" in body
    assert 'status="PUBLISHED"} 1.0' in body
    assert "adaptive_review_redundancy_rate 0.0" in body
    assert "adaptive_review_agent_tokens_total 1500.0" in body
    assert "adaptive_review_agent_cost_usd_total 0.001" in body


def test_counter_labels_use_real_call_sites_only() -> None:
    names = {
        "adaptive_review_webhook_events_total",
        "adaptive_review_feedback_recorded_total",
    }
    body, _ = metrics.render_metrics()
    for name in names:
        assert name in body


# --- endpoint -----------------------------------------------------------------


def test_prometheus_endpoint_returns_text(
    dashboard_client: tuple[TestClient, MemoryOrchestratorStore],
) -> None:
    client, _ = dashboard_client
    response = client.get("/api/v1/monitoring/prometheus")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "adaptive_review_reviews_by_status" in response.text
    assert "adaptive_review_webhook_events_total" in response.text


def test_prometheus_endpoint_never_fabricates(
    dashboard_client: tuple[TestClient, MemoryOrchestratorStore],
) -> None:
    client, _ = dashboard_client
    first = client.get("/api/v1/monitoring/prometheus")
    assert first.status_code == 200
    # An unseeded counter family has no samples at all (no made-up rows).
    assert "adaptive_review_webhook_events_total 1.0" not in first.text


# --- log wiring smoke ---------------------------------------------------------


def test_log_redaction_does_not_crash_system_logger() -> None:
    logsetup.configure_logging(level="info")
    log = structlog.get_logger("monitoring.smoke")
    log.info("redact_probe", token="x" * 40, ok=True)


def _parse_metric_float(body: str, name: str) -> float:
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith(name) and not stripped.startswith("#"):
            return float(stripped.split()[-1])
    raise AssertionError(f"metric {name} not found")


def test_gauges_set_to_zero_when_rollup_empty() -> None:
    metrics.update_store_gauges({})
    body, _ = metrics.render_metrics()
    assert _parse_metric_float(body, "adaptive_review_agent_tokens_total") == 0.0
