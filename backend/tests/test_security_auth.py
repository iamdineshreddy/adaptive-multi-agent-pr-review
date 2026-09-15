"""Phase 15 part 1: bearer-token authn/authz (docs/SECURITY.md §4).

Covers the token primitives (digest-only storage, constant-time compare,
issuance), the settings-backed provider, the FastAPI 401/403 gates, and the
per-repository isolation enforced on repository-scoped endpoints.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api.dashboard import get_dashboard_store
from app.config.settings import get_settings
from app.main import app
from app.models.enums import ReviewStatus
from app.orchestrator.persistence import MemoryOrchestratorStore
from app.security import auth as auth_mod
from app.security.tokens import (
    TokenPrincipal,
    constant_time_equals,
    hash_token,
    issue_token,
    role_rank,
)

REPO_ID = uuid.uuid4()
OTHER_REPO_ID = uuid.uuid4()


def _seeded_store() -> MemoryOrchestratorStore:
    store = MemoryOrchestratorStore()
    store.reviews[str(uuid.uuid4())] = {
        "status": ReviewStatus.PUBLISHED.value,
        "supervisor_notes": [],
        "status_history": [],
    }
    store.repositories[str(REPO_ID)] = {
        "full_name": "acme/app",
        "default_branch": "main",
        "is_active": True,
    }
    store.repositories[str(OTHER_REPO_ID)] = {
        "full_name": "acme/other",
        "default_branch": "main",
        "is_active": True,
    }
    return store


@pytest.fixture
def auth_client() -> Iterator[tuple[TestClient, str, MemoryOrchestratorStore]]:
    """A dashboard client authenticated with an admin token."""
    store = _seeded_store()
    settings = get_settings()
    original = (
        list(settings.api_token_hashes),
        dict(settings.api_token_roles),
        dict(settings.api_token_scopes),
    )
    principal, raw = issue_token(label="test-admin", role="admin")
    settings.api_token_hashes = [principal.token_hash]
    settings.api_token_roles = {principal.token_hash: principal.role}
    settings.api_token_scopes = {principal.token_hash: []}
    provider = auth_mod.SettingsTokenProvider()

    app.dependency_overrides[get_dashboard_store] = lambda: store
    app.dependency_overrides[auth_mod.get_token_provider] = lambda: provider
    with TestClient(app) as client:
        yield client, f"Bearer {raw}", store
    app.dependency_overrides.clear()
    settings.api_token_hashes, settings.api_token_roles, settings.api_token_scopes = (
        original
    )


def _install_scoped_token(role: str, repos: list[str]) -> str:
    """Provision an additional token and return its Bearer header value."""
    settings = get_settings()
    principal, raw = issue_token(label="scoped", role=role, repos=repos)
    settings.api_token_hashes = [*settings.api_token_hashes, principal.token_hash]
    settings.api_token_roles = {**settings.api_token_roles, principal.token_hash: role}
    settings.api_token_scopes = {
        **settings.api_token_scopes,
        principal.token_hash: repos,
    }
    return f"Bearer {raw}"


# --- primitives ----------------------------------------------------------------


def test_hash_token_is_digest_only() -> None:
    raw = "sk-test-secret"
    digest = hash_token(raw)
    assert len(digest) == 64
    assert raw not in digest
    assert hash_token(raw) == digest
    assert hash_token(raw) != hash_token("sk-other")


def test_constant_time_compare() -> None:
    assert constant_time_equals("abc", "abc") is True
    assert constant_time_equals("abc", "abd") is False


def test_issue_token_produces_prefixed_random_and_matching_hash() -> None:
    principal, raw = issue_token(label="ci", role="operator")
    assert raw.startswith("sk-")
    assert len(raw) > len("sk-")
    assert hash_token(raw) == principal.token_hash
    _, raw2 = issue_token(label="ci", role="operator")
    assert raw != raw2


def test_issue_token_rejects_unknown_role() -> None:
    with pytest.raises(ValueError):
        issue_token(label="x", role="superuser")


def test_role_rank_ordering() -> None:
    assert role_rank("viewer") < role_rank("operator") < role_rank("admin")
    assert role_rank("nonsense") < role_rank("viewer")
    assert role_rank(None) < role_rank("viewer")


def test_settings_provider_lookup_by_digest() -> None:
    principal, raw = issue_token(label="ci", role="viewer", repos=None)
    settings = get_settings()
    original = (
        list(settings.api_token_hashes),
        dict(settings.api_token_roles),
        dict(settings.api_token_scopes),
    )
    try:
        settings.api_token_hashes = [principal.token_hash]
        provider = auth_mod.SettingsTokenProvider()
        resolved = asyncio.run(provider.lookup(raw))
        assert resolved is not None
        assert resolved.role == "viewer"
        assert resolved.token_hash == principal.token_hash
        assert asyncio.run(provider.lookup("sk-unknown")) is None
    finally:
        (
            settings.api_token_hashes,
            settings.api_token_roles,
            settings.api_token_scopes,
        ) = original


# --- role gates ----------------------------------------------------------------


def test_require_role_gate_ranks() -> None:
    gate_admin = auth_mod.require_role("admin")
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(gate_admin(principal=TokenPrincipal(token_hash="h", role="viewer")))
    assert exc_info.value.status_code == 403
    result = asyncio.run(
        gate_admin(principal=TokenPrincipal(token_hash="h", role="admin"))
    )
    assert result.role == "admin"


# --- endpoint authn ------------------------------------------------------------


def test_dashboard_requires_bearer_token(
    auth_client: tuple[TestClient, str, MemoryOrchestratorStore],
) -> None:
    client, _, _ = auth_client
    assert client.get("/api/v1/dashboard/summary").status_code == 401
    assert (
        client.get(
            "/api/v1/dashboard/summary",
            headers={"Authorization": "Bearer wrong"},
        ).status_code
        == 401
    )


def test_valid_token_enters_dashboard(
    auth_client: tuple[TestClient, str, MemoryOrchestratorStore],
) -> None:
    client, header, _ = auth_client
    response = client.get(
        "/api/v1/dashboard/summary", headers={"Authorization": header}
    )
    assert response.status_code == 200


# --- repo isolation ------------------------------------------------------------


def test_repo_scoped_token_is_isolated(
    auth_client: tuple[TestClient, str, MemoryOrchestratorStore],
) -> None:
    client, _, _ = auth_client
    scoped_header = _install_scoped_token("viewer", [str(REPO_ID)])
    in_scope = client.get(
        f"/api/v1/repositories/{REPO_ID}", headers={"Authorization": scoped_header}
    )
    assert in_scope.status_code == 200
    assert in_scope.json()["full_name"] == "acme/app"
    out_of_scope = client.get(
        f"/api/v1/repositories/{OTHER_REPO_ID}",
        headers={"Authorization": scoped_header},
    )
    assert out_of_scope.status_code == 403


def test_unscoped_token_reads_all_repositories(
    auth_client: tuple[TestClient, str, MemoryOrchestratorStore],
) -> None:
    client, header, _ = auth_client
    for repo_id in (REPO_ID, OTHER_REPO_ID):
        response = client.get(
            f"/api/v1/repositories/{repo_id}", headers={"Authorization": header}
        )
        assert response.status_code == 200
