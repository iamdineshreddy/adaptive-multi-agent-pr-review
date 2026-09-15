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
from app.security.auth import get_principal
from app.security.tokens import TokenPrincipal

REVIEW_ID = uuid.uuid4()
REVIEW_ID_2 = uuid.uuid4()
FINDING_ID = uuid.uuid4()
REPO_ID = uuid.uuid4()
REPO_ID_2 = uuid.uuid4()

_ADMIN = TokenPrincipal(token_hash="test-admin", role="admin", label="test")


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
            "repository_id": str(REPO_ID),
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
    store.repositories[str(REPO_ID)] = {
        "full_name": "acme/app",
        "default_branch": "main",
        "main_language": "Python",
        "is_active": True,
    }
    store.repositories[str(REPO_ID_2)] = {
        "full_name": "acme/other",
        "default_branch": "main",
        "main_language": "Go",
        "is_active": True,
    }
    store.feedback.append(
        {
            "id": str(uuid.uuid4()),
            "finding_id": str(FINDING_ID),
            "review_id": str(REVIEW_ID),
            "repository_id": str(REPO_ID),
            "outcome": "ACCEPTED",
            "source": "API",
            "author_login": "dev1",
            "commit_sha": None,
            "created_at": "2026-01-02T00:00:00Z",
            "category": "security/xss",
        }
    )


@pytest.fixture
def dashboard_client() -> Iterator[tuple[TestClient, MemoryOrchestratorStore]]:
    store = MemoryOrchestratorStore()
    _seed(store)
    app.dependency_overrides[get_dashboard_store] = lambda: store
    app.dependency_overrides[get_principal] = lambda: _ADMIN
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


# --- Phase 13 part 2 ---------------------------------------------------------


def test_list_repositories(dashboard_client) -> None:
    client, _ = dashboard_client
    response = client.get("/api/v1/repositories")
    assert response.status_code == 200
    repos = response.json()
    assert len(repos) == 2
    names = {repo["full_name"] for repo in repos}
    assert names == {"acme/app", "acme/other"}
    assert all("id" in repo for repo in repos)


def test_repository_detail(dashboard_client) -> None:
    client, _ = dashboard_client
    response = client.get(f"/api/v1/repositories/{REPO_ID}")
    assert response.status_code == 200
    body = response.json()
    assert body["full_name"] == "acme/app"
    assert body["is_active"] is True


def test_repository_detail_unknown_is_404(dashboard_client) -> None:
    client, _ = dashboard_client
    response = client.get(f"/api/v1/repositories/{uuid.uuid4()}")
    assert response.status_code == 404


def test_repository_memory_requires_seed(dashboard_client) -> None:
    """Memory for a repo is 404 until the orchestrator records a snapshot."""
    client, store = dashboard_client
    store.memory[str(REPO_ID)] = {
        "snapshot": {"security": {"accepted_share": 0.5}},
        "decay_params": {"tau_days": 90.0},
        "version": 1,
    }
    ok = client.get(f"/api/v1/repositories/{REPO_ID}/memory")
    assert ok.status_code == 200
    assert ok.json()["version"] == 1
    assert ok.json()["snapshot"]["security"]["accepted_share"] == 0.5
    missing = client.get(f"/api/v1/repositories/{uuid.uuid4()}/memory")
    assert missing.status_code == 404


def test_repository_feedback_lists_events(dashboard_client) -> None:
    client, _ = dashboard_client
    response = client.get(f"/api/v1/repositories/{REPO_ID}/feedback")
    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 1
    assert rows[0]["outcome"] == "ACCEPTED"
    assert rows[0]["author_login"] == "dev1"


def test_review_findings_paginated_and_filtered(dashboard_client) -> None:
    client, store = dashboard_client
    submitted = store.findings[0]
    store.findings.append(
        {
            **submitted,
            "id": str(uuid.uuid4()),
            "publication_status": FindingStatus.SUPPRESSED.value,
        }
    )
    all_rows = client.get(f"/api/v1/reviews/{REVIEW_ID}/findings").json()
    assert len(all_rows) == 2
    only_scheduled = client.get(
        f"/api/v1/reviews/{REVIEW_ID}/findings?status=SCHEDULED"
    ).json()
    assert len(only_scheduled) == 1
    only_suppressed = client.get(
        f"/api/v1/reviews/{REVIEW_ID}/findings?status=SUPPRESSED"
    ).json()
    assert len(only_suppressed) == 1
    first_page = client.get(
        f"/api/v1/reviews/{REVIEW_ID}/findings?limit=1&offset=0"
    ).json()
    second_page = client.get(
        f"/api/v1/reviews/{REVIEW_ID}/findings?limit=1&offset=1"
    ).json()
    assert len(first_page) == 1 and len(second_page) == 1


def test_review_iterations_endpoint(dashboard_client) -> None:
    client, _ = dashboard_client
    response = client.get(f"/api/v1/reviews/{REVIEW_ID}/iterations")
    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 1
    assert rows[0]["iteration"] == 1
    assert rows[0]["stale_count"] == 1


def test_metrics_rollup(dashboard_client) -> None:
    client, store = dashboard_client
    store.findings[0]["duplicate_group"] = str(uuid.uuid4())
    store.metrics.append(
        {
            "review_id": str(REVIEW_ID),
            "agent_key": "security",
            "agent_id": "sec-agent",
            "stats": {
                "tokens_in": 100,
                "tokens_out": 50,
                "cost_usd": 0.01,
                "duration_ms": 250,
            },
        }
    )
    response = client.get("/api/v1/metrics")
    assert response.status_code == 200
    body = response.json()
    assert body["findings_total"] == 1
    assert body["findings_by_status"] == {"SCHEDULED": 1}
    assert body["reviews_by_status"] == {"PUBLISHED": 1, "FAILED": 1}
    assert body["feedback_total"] == 1
    assert body["feedback_by_outcome"] == {"ACCEPTED": 1}
    assert body["redundancy_rate"] == 1.0
    assert body["agent_metrics"]["tokens_in"] == 100
    assert body["agent_metrics"]["tokens_out"] == 50
    assert body["agent_metrics"]["latency_max_ms"] == 250
