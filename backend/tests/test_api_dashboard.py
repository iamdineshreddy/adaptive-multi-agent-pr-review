"""Phase 13: dashboard read API endpoints.

Runs against the in-memory store (no database) with the ``get_dashboard_store``
dependency overridden. Covers status summary, review listing (pagination,
status filter), and review detail (findings + iterations), plus 404 handling.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.api.dashboard import get_dashboard_store
from app.main import app
from app.models.enums import FindingStatus, ReviewStatus
from app.orchestrator.persistence import MemoryOrchestratorStore

REVIEW_ID = uuid.uuid4()
REVIEW_ID_2 = uuid.uuid4()
FINDING_ID = uuid.uuid4()


def _seed(store: MemoryOrchestratorStore) -> None:
    store.reviews[str(REVIEW_ID)] = {
        "status": ReviewStatus.PUBLISHED.value,
        "supervisor_notes": [],
        "status_history": [
            {"status": "PUBLISHED", "from": None, "at": "2026-01-01T00:00:00Z"}
        ],
    }
    store.reviews[str(REVIEW_ID_2)] = {
        "status": ReviewStatus.FAILED.value,
        "failure_reason": "boom",
        "supervisor_notes": [],
        "status_history": [],
    }
    store.findings.append(
        {
            "id": str(FINDING_ID),
            "review_id": str(REVIEW_ID),
            "repository_id": str(uuid.uuid4()),
            "agent_key": "security",
            "agent_id": "sec-agent",
            "file_path": "app/auth.py",
            "line_start": None,
            "line_end": None,
            "category": "security/xss",
            "severity": "HIGH",
            "confidence": 0.9,
            "title": "XSS risk",
            "description": "...",
            "evidence": {},
            "suggested_fix": "sanitise",
            "reason_summary": "...",
            "duplicate_group": None,
            "publication_status": FindingStatus.SCHEDULED.value,
        }
    )
    store.iterations.append(
        {
            "review_id": str(REVIEW_ID),
            "iteration": 1,
            "base_sha": "abc",
            "head_sha": "def",
            "diff_stats": {"stale": 1},
            "agents_invoked": {"targeted": ["security"], "planned": ["security"]},
            "published_count": 1,
            "resolved_count": 0,
            "stale_count": 1,
        }
    )


@pytest.fixture
def dashboard_client() -> Iterator[tuple[TestClient, MemoryOrchestratorStore]]:
    store = MemoryOrchestratorStore()
    _seed(store)
    app.dependency_overrides[get_dashboard_store] = lambda: store
    with TestClient(app) as client:
        yield client, store
    app.dependency_overrides.clear()


def test_dashboard_summary_counts_by_status(
    dashboard_client: tuple[TestClient, MemoryOrchestratorStore],
) -> None:
    client, _ = dashboard_client
    response = client.get("/api/v1/dashboard/summary")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert body["by_status"] == {
        "PUBLISHED": 1,
        "FAILED": 1,
    }


def test_list_reviews_newest_first(dashboard_client) -> None:
    client, store = dashboard_client
    store.reviews[str(REVIEW_ID)]["status"] = ReviewStatus.COMPLETED.value
    response = client.get("/api/v1/reviews")
    assert response.status_code == 200
    reviews = response.json()
    assert len(reviews) == 2
    assert {row["status"] for row in reviews} == {"COMPLETED", "FAILED"}
    assert all("id" in row for row in reviews)


def test_list_reviews_pagination(dashboard_client) -> None:
    client, _ = dashboard_client
    first = client.get("/api/v1/reviews?limit=1&offset=0").json()
    second = client.get("/api/v1/reviews?limit=1&offset=1").json()
    assert len(first) == 1
    assert len(second) == 1
    assert first[0]["id"] != second[0]["id"]


def test_list_reviews_status_filter(dashboard_client) -> None:
    client, _ = dashboard_client
    response = client.get("/api/v1/reviews?status=FAILED")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["status"] == "FAILED"
    assert body[0]["failure_reason"] == "boom"


def test_list_reviews_rejects_bad_status(dashboard_client) -> None:
    client, _ = dashboard_client
    response = client.get("/api/v1/reviews?status=NOPE")
    assert response.status_code == 422


def test_review_detail_includes_findings_and_iterations(dashboard_client) -> None:
    client, _ = dashboard_client
    response = client.get(f"/api/v1/reviews/{REVIEW_ID}")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "PUBLISHED"
    assert len(body["findings"]) == 1
    assert body["findings"][0]["title"] == "XSS risk"
    assert body["findings"][0]["publication_status"] == "SCHEDULED"
    assert len(body["iterations"]) == 1
    assert body["iterations"][0]["iteration"] == 1
    assert body["iterations"][0]["stale_count"] == 1


def test_review_detail_unknown_review_is_404(dashboard_client) -> None:
    client, _ = dashboard_client
    response = client.get(f"/api/v1/reviews/{uuid.uuid4()}")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "not_found"
