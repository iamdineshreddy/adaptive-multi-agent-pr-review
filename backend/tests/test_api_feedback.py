"""Phase 11: ``POST /api/v1/feedback`` endpoint (docs/API.md §4, FR-5.1/FR-5.2).

Runs against the in-memory store (no database), with a fresh
``MemoryRateLimiter`` injected per test so rate-limit states are isolated.
Covers the happy path (201), idempotent replay, 404, 422, 429, and the
code-change-evidence attachment.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.api.feedback import get_feedback_limiter, get_feedback_store
from app.main import app
from app.orchestrator.persistence import MemoryOrchestratorStore
from app.security.auth import get_principal
from app.security.tokens import TokenPrincipal

REPO_ID = uuid.uuid4()
REVIEW_ID = uuid.uuid4()
FINDING_ID = uuid.uuid4()
FEEDBACK_URL = "/api/v1/feedback"

_ADMIN = TokenPrincipal(token_hash="test-admin", role="admin", label="test")


def _seed_finding(store: MemoryOrchestratorStore) -> None:
    """Persist one resolved finding so ``resolve_feedback_finding`` can find it."""
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
            "suggested_fix": None,
            "reason_summary": "...",
            "duplicate_group": None,
            "publication_status": "SCHEDULED",
        }
    )


def _feedback_body(
    *,
    finding_id: uuid.UUID | None = FINDING_ID,
    outcome: str = "ACCEPTED",
    commit_sha: str | None = None,
) -> dict:
    body: dict = {
        "review_id": str(REVIEW_ID),
        "finding_id": str(finding_id) if finding_id else None,
        "outcome": outcome,
        "author_login": "dev1",
    }
    if commit_sha:
        body["commit_sha"] = commit_sha
    return body


@pytest.fixture
def feedback_client() -> Iterator[tuple[TestClient, MemoryOrchestratorStore]]:
    store = MemoryOrchestratorStore()
    _seed_finding(store)

    app.dependency_overrides[get_feedback_store] = lambda: store
    app.dependency_overrides[get_feedback_limiter] = lambda: MemoryRateLimiter(
        per_ip_per_minute=100, per_repo_burst=2**31 - 1
    )
    app.dependency_overrides[get_principal] = lambda: _ADMIN
    with TestClient(app) as client:
        yield client, store
    app.dependency_overrides.clear()


# We need MemoryRateLimiter here so we can create rate-limited overrides.
from app.webhooks.ratelimit import MemoryRateLimiter  # noqa: E402

# --- happy path --------------------------------------------------------------


def test_accepts_valid_outcome_returns_201(
    feedback_client: tuple[TestClient, MemoryOrchestratorStore],
):
    client, store = feedback_client
    response = client.post(FEEDBACK_URL, json=_feedback_body())
    assert response.status_code == 201
    body = response.json()
    assert body["recorded"] is True
    uuid.UUID(body["feedback_id"])
    assert body["memory_version"] is not None
    # Feedback row persisted.
    assert len(store.feedback) == 1
    # Snapshot computed.
    assert store.memory[str(REPO_ID)]["version"] == body["memory_version"]


# --- idempotency -------------------------------------------------------------


def test_replay_same_body_returns_same_feedback_id(
    feedback_client: tuple[TestClient, MemoryOrchestratorStore],
):
    client, store = feedback_client
    first = client.post(FEEDBACK_URL, json=_feedback_body())
    second = client.post(FEEDBACK_URL, json=_feedback_body())
    assert first.json()["feedback_id"] == second.json()["feedback_id"]
    assert len(store.feedback) == 1  # no duplicate row


# --- code change evidence ----------------------------------------------------


def test_commit_sha_attaches_code_change_signal_and_preserves_outcome(
    feedback_client: tuple[TestClient, MemoryOrchestratorStore],
):
    client, store = feedback_client
    body = _feedback_body(outcome="IGNORED", commit_sha="abc123")
    response = client.post(FEEDBACK_URL, json=body)
    assert response.status_code == 201
    row = store.feedback[0]
    assert row["outcome"] == "IGNORED"  # not overridden
    assert len(row["details"]["code_change_signals"]) == 1
    assert row["details"]["code_change_signals"][0]["commit_sha"] == "abc123"


# --- 404 ---------------------------------------------------------------------


def test_unknown_finding_returns_404():
    store = MemoryOrchestratorStore()
    app.dependency_overrides[get_feedback_store] = lambda: store
    app.dependency_overrides[get_feedback_limiter] = lambda: MemoryRateLimiter(
        per_ip_per_minute=100, per_repo_burst=2**31 - 1
    )
    app.dependency_overrides[get_principal] = lambda: _ADMIN
    with TestClient(app) as client:
        body = _feedback_body(finding_id=uuid.uuid4())
        response = client.post(FEEDBACK_URL, json=body)
    app.dependency_overrides.clear()
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "finding_not_found"


# --- 422 ---------------------------------------------------------------------


def test_invalid_outcome_rejected_422():
    store = MemoryOrchestratorStore()
    app.dependency_overrides[get_feedback_store] = lambda: store
    app.dependency_overrides[get_feedback_limiter] = lambda: MemoryRateLimiter(
        per_ip_per_minute=100, per_repo_burst=2**31 - 1
    )
    app.dependency_overrides[get_principal] = lambda: _ADMIN
    with TestClient(app) as client:
        body = _feedback_body(outcome="NOT_A_REAL_OUTCOME")
        response = client.post(FEEDBACK_URL, json=body)
    app.dependency_overrides.clear()
    assert response.status_code == 422


# --- rate limiting (429) -----------------------------------------------------


def test_rate_limit_returns_429():
    store = MemoryOrchestratorStore()
    _seed_finding(store)
    limiter = MemoryRateLimiter(per_ip_per_minute=1, per_repo_burst=2**31 - 1)
    app.dependency_overrides[get_feedback_store] = lambda: store
    app.dependency_overrides[get_feedback_limiter] = lambda: limiter
    app.dependency_overrides[get_principal] = lambda: _ADMIN
    with TestClient(app) as client:
        first = client.post(FEEDBACK_URL, json=_feedback_body())
        assert first.status_code == 201
        second = client.post(FEEDBACK_URL, json=_feedback_body(outcome="REJECTED"))
        # outcome changed so identity differs, but ip rate limit blocks it
        assert second.status_code == 429
        assert second.json()["detail"]["code"] == "rate_limited"
    app.dependency_overrides.clear()


# --- missing finding_id validated by pydantic before hitting store -----------


def test_null_finding_id_rejected_422():
    store = MemoryOrchestratorStore()
    app.dependency_overrides[get_feedback_store] = lambda: store
    app.dependency_overrides[get_feedback_limiter] = lambda: MemoryRateLimiter(
        per_ip_per_minute=100, per_repo_burst=2**31 - 1
    )
    app.dependency_overrides[get_principal] = lambda: _ADMIN
    with TestClient(app) as client:
        body = _feedback_body(finding_id=None)
        response = client.post(FEEDBACK_URL, json=body)
    app.dependency_overrides.clear()
    assert response.status_code == 422


# --- Phase 15 part 2: bearer-token gate + repo scope --------------------------


def test_feedback_requires_bearer_token_directly(
    feedback_client: tuple[TestClient, MemoryOrchestratorStore],
):
    """Without the fixture's principal override, the router-level gate 401s."""
    client, _ = feedback_client
    app.dependency_overrides.pop(get_principal, None)
    response = client.post(FEEDBACK_URL, json=_feedback_body())
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "missing_bearer_token"


def test_feedback_scoped_token_rejected_outside_scope(
    feedback_client: tuple[TestClient, MemoryOrchestratorStore],
):
    """A token scoped to another repository cannot post feedback for REPO_ID."""
    client, _ = feedback_client
    scoped = TokenPrincipal(
        token_hash="scoped", role="operator", repos={str(uuid.uuid4())}
    )
    app.dependency_overrides[get_principal] = lambda: scoped
    response = client.post(FEEDBACK_URL, json=_feedback_body())
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "repository_out_of_scope"


def test_feedback_scoped_token_allowed_inside_scope(
    feedback_client: tuple[TestClient, MemoryOrchestratorStore],
):
    client, _ = feedback_client
    scoped = TokenPrincipal(token_hash="scoped", role="operator", repos={str(REPO_ID)})
    app.dependency_overrides[get_principal] = lambda: scoped
    response = client.post(FEEDBACK_URL, json=_feedback_body())
    assert response.status_code == 201
