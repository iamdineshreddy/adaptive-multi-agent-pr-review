"""Phase 15 part 2b: admin write actions + audit trail (FR-7.3).

Covers ``POST /api/v1/reviews/{id}/rerun`` and ``PATCH
/api/v1/repositories/{id}/settings`` (operator gates, dispatch, audit rows) and
``GET /api/v1/audit-log`` (admin gate + repo-scope filtering), against the
in-memory store with the dispatcher stubbed. Also unit-tests the per-repo
settings consumption helpers (``_budget_cap`` / ``_repo_float``).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.admin import get_admin_dispatcher, get_admin_store
from app.main import app
from app.models.enums import ReviewStatus
from app.orchestrator.persistence import MemoryOrchestratorStore
from app.orchestrator.service import _budget_cap, _repo_float
from app.queue.contracts import DispatchResult
from app.security.auth import get_principal
from app.security.tokens import TokenPrincipal

REVIEW_ID = uuid.uuid4()
OTHER_REVIEW_ID = uuid.uuid4()
REPO_ID = uuid.uuid4()
OTHER_REPO_ID = uuid.uuid4()

_ADMIN = TokenPrincipal(token_hash="admin-hash", role="admin", label="ci-admin")
_OPERATOR = TokenPrincipal(token_hash="op-hash", role="operator", label="ci-op")
_VIEWER = TokenPrincipal(token_hash="viewer-hash", role="viewer", label="ci-view")
_SCOPED_OP = TokenPrincipal(
    token_hash="scoped-op-hash",
    role="operator",
    label="ci-scoped-op",
    repos=frozenset({str(REPO_ID)}),
)


class StubDispatcher:
    """Records dispatch calls; never touches a broker."""

    def __init__(self) -> None:
        self.calls: list[tuple[object, str]] = []

    async def dispatch(self, review_id: object, delivery_id: str) -> DispatchResult:
        self.calls.append((review_id, delivery_id))
        return DispatchResult(dispatched=True, note=f"stub dispatch of {review_id}")


def _seed(store: MemoryOrchestratorStore) -> None:
    store.reviews[str(REVIEW_ID)] = {
        "status": ReviewStatus.PUBLISHED.value,
        "repository_id": str(REPO_ID),
        "pull_request": {
            "changed_files": 5,
            "additions": 40,
            "deletions": 10,
            "number": 42,
        },
        "supervisor_notes": [],
        "status_history": [],
    }
    store.reviews[str(OTHER_REVIEW_ID)] = {
        "status": ReviewStatus.FAILED.value,
        "failure_reason": "boom",
        "repository_id": str(OTHER_REPO_ID),
        "pull_request": {
            "changed_files": 1,
            "additions": 2,
            "deletions": 0,
            "number": 7,
        },
        "supervisor_notes": [],
        "status_history": [],
    }
    store.repositories[str(REPO_ID)] = {
        "full_name": "acme/app",
        "default_branch": "main",
        "review_settings": {"urgency": 0.5},
    }
    store.repositories[str(OTHER_REPO_ID)] = {
        "full_name": "acme/other",
        "default_branch": "main",
        "review_settings": {},
    }


AdminFixture = tuple[TestClient, MemoryOrchestratorStore, StubDispatcher]


@pytest.fixture
def admin_client() -> Iterator[AdminFixture]:
    """Unscoped admin client + seeded memory store + stub dispatcher."""
    store = MemoryOrchestratorStore()
    _seed(store)
    dispatcher = StubDispatcher()
    app.dependency_overrides[get_admin_store] = lambda: store
    app.dependency_overrides[get_admin_dispatcher] = lambda: dispatcher
    app.dependency_overrides[get_principal] = lambda: _ADMIN
    with TestClient(app) as client:
        yield client, store, dispatcher
    app.dependency_overrides.clear()


def _as(principal: TokenPrincipal) -> None:
    """Point the auth dependency at a different principal for the next request."""
    app.dependency_overrides[get_principal] = lambda: principal


# --- rerun ---------------------------------------------------------------------


def test_rerun_creates_manual_rerun_and_dispatches(
    admin_client: AdminFixture,
) -> None:
    client, store, dispatcher = admin_client
    response = client.post(f"/api/v1/reviews/{REVIEW_ID}/rerun")
    assert response.status_code == 202
    body = response.json()
    assert body["mode"] == "MANUAL_RERUN"
    assert body["status"] == "QUEUED"
    assert body["review_id"] != str(REVIEW_ID)
    assert body["pr_number"] == 42
    assert body["priority_score"] > 0
    assert body["risk_class"] in {"LOW", "MEDIUM", "HIGH"}
    assert body["dispatch_note"].startswith("stub dispatch")

    new_id = uuid.UUID(body["review_id"])
    assert (new_id, str(dispatcher.calls[0][0])) == (new_id, body["review_id"])
    assert dispatcher.calls[0][1].startswith("rerun:")
    assert str(dispatcher.calls[0][0]) in store.reviews
    assert store.reviews[str(new_id)]["status"] == "QUEUED"
    assert store.reviews[str(new_id)]["mode"] == "MANUAL_RERUN"


def test_rerun_records_audit_with_principal_and_before_after(
    admin_client: AdminFixture,
) -> None:
    client, store, _ = admin_client
    response = client.post(f"/api/v1/reviews/{REVIEW_ID}/rerun")
    assert response.status_code == 202
    audit = store.audit
    assert len(audit) == 1
    row = audit[0]
    assert row["action"] == "review.rerun"
    assert row["target_kind"] == "review"
    assert row["target_id"] == str(REVIEW_ID)
    assert row["repository_id"] == str(REPO_ID)
    assert row["principal_token_hash"] == "admin-hash"
    assert row["principal_role"] == "admin"
    assert row["before"] == {"source_review_id": str(REVIEW_ID)}
    assert row["after"]["status"] == "QUEUED"
    assert row["after"]["rerun_review_id"] == response.json()["review_id"]


def test_rerun_requires_operator_role(admin_client: AdminFixture) -> None:
    client, _, _ = admin_client
    _as(_VIEWER)
    assert client.post(f"/api/v1/reviews/{REVIEW_ID}/rerun").status_code == 403
    _as(_ADMIN)


def test_rerun_scoped_operator_isolated(admin_client: AdminFixture) -> None:
    client, _, dispatcher = admin_client
    _as(_SCOPED_OP)
    in_scope = client.post(f"/api/v1/reviews/{REVIEW_ID}/rerun")
    assert in_scope.status_code == 202
    assert len(dispatcher.calls) == 1
    out_of_scope = client.post(f"/api/v1/reviews/{OTHER_REVIEW_ID}/rerun")
    assert out_of_scope.status_code == 403
    assert len(dispatcher.calls) == 1


def test_rerun_unknown_review_is_404(admin_client: AdminFixture) -> None:
    client, _, dispatcher = admin_client
    response = client.post(f"/api/v1/reviews/{uuid.uuid4()}/rerun")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "review_not_found"
    assert dispatcher.calls == []


# --- repository settings -------------------------------------------------------


def test_settings_patch_merges_and_audits(admin_client: AdminFixture) -> None:
    client, store, _ = admin_client
    response = client.patch(
        f"/api/v1/repositories/{REPO_ID}/settings",
        json={
            "urgency": 0.9,
            "review_budget": {"low": 3, "medium": 8, "high": 15},
            "arum_weights": {"w1_severity": 0.5},
            "decay": {"temporal_decay_days": 60, "decay_lambda": 0.8},
        },
    )
    assert response.status_code == 200
    saved = response.json()["review_settings"]
    assert saved["urgency"] == 0.9
    assert saved["review_budget"] == {"low": 3, "medium": 8, "high": 15}
    assert saved["arum_weights"] == {"w1_severity": 0.5}
    assert saved["decay"] == {"temporal_decay_days": 60, "decay_lambda": 0.8}

    assert len(store.audit) == 1
    row = store.audit[0]
    assert row["action"] == "repository.settings.update"
    assert row["target_id"] == str(REPO_ID)
    assert row["before"] == {"urgency": 0.5}
    assert row["after"] == saved
    assert row["principal_role"] == "admin"


def test_settings_patch_merges_scalar_over_existing(admin_client: AdminFixture) -> None:
    client, _store, _ = admin_client
    first = client.patch(
        f"/api/v1/repositories/{REPO_ID}/settings", json={"urgency": 0.9}
    )
    assert first.status_code == 200
    second = client.patch(
        f"/api/v1/repositories/{REPO_ID}/settings",
        json={"arum_weights": {"w2_confidence": 0.4}},
    )
    assert second.status_code == 200
    saved = second.json()["review_settings"]
    assert saved["urgency"] == 0.9
    assert saved["arum_weights"] == {"w2_confidence": 0.4}


def test_settings_patch_requires_a_change(admin_client: AdminFixture) -> None:
    client, _, _ = admin_client
    response = client.patch(f"/api/v1/repositories/{REPO_ID}/settings", json={})
    assert response.status_code == 422


def test_settings_patch_rejects_unknown_arum_key(admin_client: AdminFixture) -> None:
    client, _, _ = admin_client
    response = client.patch(
        f"/api/v1/repositories/{REPO_ID}/settings",
        json={"arum_weights": {"w9_typo": 0.5}},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_settings"


def test_settings_patch_validates_ranges(admin_client: AdminFixture) -> None:
    client, _, _ = admin_client
    assert (
        client.patch(
            f"/api/v1/repositories/{REPO_ID}/settings", json={"urgency": 1.5}
        ).status_code
        == 422
    )
    assert (
        client.patch(
            f"/api/v1/repositories/{REPO_ID}/settings",
            json={"safety_gates": {"high_confidence": 2.0}},
        ).status_code
        == 422
    )


def test_settings_patch_requires_operator_role(admin_client: AdminFixture) -> None:
    client, _, _ = admin_client
    _as(_VIEWER)
    assert (
        client.patch(
            f"/api/v1/repositories/{REPO_ID}/settings", json={"urgency": 0.1}
        ).status_code
        == 403
    )
    _as(_ADMIN)


def test_settings_patch_unknown_repo_is_404(admin_client: AdminFixture) -> None:
    client, _, _ = admin_client
    response = client.patch(
        f"/api/v1/repositories/{uuid.uuid4()}/settings", json={"urgency": 0.5}
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "repository_not_found"


# --- audit log -----------------------------------------------------------------


def test_audit_log_lists_rows_newest_first(admin_client: AdminFixture) -> None:
    client, store, _ = admin_client
    assert client.post(f"/api/v1/reviews/{REVIEW_ID}/rerun").status_code == 202
    assert (
        client.patch(
            f"/api/v1/repositories/{REPO_ID}/settings", json={"urgency": 0.7}
        ).status_code
        == 200
    )
    response = client.get("/api/v1/audit-log")
    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 2
    assert [row["action"] for row in rows] == [
        "repository.settings.update",
        "review.rerun",
    ]
    assert all("created_at" in row and "principal_role" in row for row in rows)


def test_audit_log_filters_by_action(admin_client: AdminFixture) -> None:
    client, _, _ = admin_client
    client.post(f"/api/v1/reviews/{REVIEW_ID}/rerun")
    response = client.get("/api/v1/audit-log", params={"action": "review.rerun"})
    assert len(response.json()) == 1
    other = client.get("/api/v1/audit-log", params={"action": "other.action"})
    assert other.json() == []


def test_audit_log_pagination(admin_client: AdminFixture) -> None:
    client, _, _ = admin_client
    client.post(f"/api/v1/reviews/{REVIEW_ID}/rerun")
    client.post(f"/api/v1/reviews/{OTHER_REVIEW_ID}/rerun")
    page = client.get("/api/v1/audit-log", params={"limit": 1})
    assert len(page.json()) == 1
    second = client.get("/api/v1/audit-log", params={"limit": 1, "offset": 1})
    assert len(second.json()) == 1
    assert page.json()[0]["id"] != second.json()[0]["id"]


def test_audit_log_requires_admin_role(admin_client: AdminFixture) -> None:
    client, _, _ = admin_client
    _as(_OPERATOR)
    assert client.get("/api/v1/audit-log").status_code == 403
    _as(_ADMIN)


def test_audit_log_scoped_admin_sees_only_own_repositories(
    admin_client: AdminFixture,
) -> None:
    client, _, _ = admin_client
    client.post(f"/api/v1/reviews/{REVIEW_ID}/rerun")
    client.post(f"/api/v1/reviews/{OTHER_REVIEW_ID}/rerun")
    _as(
        TokenPrincipal(
            token_hash="scoped-admin",
            role="admin",
            label="scoped",
            repos=frozenset({str(REPO_ID)}),
        )
    )
    rows = client.get("/api/v1/audit-log").json()
    assert len(rows) == 1
    assert rows[0]["repository_id"] == str(REPO_ID)


# --- gate: all admin endpoints sit behind bearer auth --------------------------


def test_admin_endpoints_require_bearer_token(admin_client: AdminFixture) -> None:
    client, _, _ = admin_client
    app.dependency_overrides.pop(get_principal, None)
    assert client.post(f"/api/v1/reviews/{REVIEW_ID}/rerun").status_code == 401
    assert (
        client.patch(
            f"/api/v1/repositories/{REPO_ID}/settings", json={"urgency": 0.1}
        ).status_code
        == 401
    )
    assert client.get("/api/v1/audit-log").status_code == 401
    app.dependency_overrides[get_principal] = lambda: _ADMIN


# --- per-repo settings consumption helpers -------------------------------------


def test_budget_cap_uses_repo_override_per_risk_class() -> None:
    from app.config.settings import get_settings
    from app.models.enums import RiskClass

    cfg = get_settings()
    repo_budget: dict[str, Any] = {"low": 3, "medium": 8, "high": 15}
    assert _budget_cap(RiskClass.HIGH, cfg, repo_budget) == 15
    assert _budget_cap(RiskClass.LOW, cfg, repo_budget) == 3
    assert _budget_cap(RiskClass.MEDIUM, cfg, repo_budget) == 8
    assert _budget_cap(None, cfg, {"low": 3}) == cfg.review_budget_high


def test_budget_cap_falls_back_on_bad_override() -> None:
    from app.config.settings import get_settings
    from app.models.enums import RiskClass

    cfg = get_settings()
    assert _budget_cap(None, cfg, {"high": "not-a-cap"}) == cfg.review_budget_high
    assert _budget_cap(RiskClass.LOW, cfg, {"medium": 8}) == cfg.review_budget_low
    assert _budget_cap(None, cfg, None) == cfg.review_budget_high


def test_repo_float_defensive() -> None:
    assert _repo_float({"high_confidence": "0.9"}, "high_confidence", 0.8) == 0.9
    assert _repo_float({"high_confidence": "garbage"}, "high_confidence", 0.8) == 0.8
    assert _repo_float({}, "high_confidence", 0.8) == 0.8
