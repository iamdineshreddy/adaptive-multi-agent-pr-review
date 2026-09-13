"""Shared pytest fixtures."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.agents.contract import AgentScope, ChangeKind, FileSlice
from app.main import app


@pytest.fixture
def client() -> TestClient:
    """Test client against the app shell (no external services started)."""
    return TestClient(app)


@pytest.fixture
def finding_data() -> dict[str, Any]:
    """A valid AgentFinding-shaped dict (copy + override keys per test)."""
    return {
        "file_path": "app/auth.py",
        "line_start": 12,
        "line_end": None,
        "category": "security/authorization",
        "severity": "HIGH",
        "confidence": 0.9,
        "title": "Missing authorization check",
        "description": "The route handler performs no authorization check.",
        "evidence": {"rule": "authz", "snippet": "def handler(): ..."},
        "suggested_fix": "Add an authorization guard.",
        "reason_summary": "Route lacks an authorization decorator.",
        "related_categories": [],
    }


@pytest.fixture
def sample_scope() -> AgentScope:
    """A valid review scope with one changed file."""
    return AgentScope(
        review_id=uuid.uuid4(),
        repository_id=uuid.uuid4(),
        pr_number=42,
        pr_title="Add authentication",
        pr_description="Implements login flow.",
        base_ref="main",
        head_ref="feat/auth",
        language="python",
        changed_files=(
            FileSlice(
                file_path="app/auth.py",
                patch=(
                    "@@ -1,5 +1,7 @@\n"
                    " def login():\n"
                    "     user = fetch_user(request)\n"
                    "+    require_active(user)\n"
                    "+    audit_login(user)\n"
                    "     return token(user)"
                ),
                new_start=1,
                new_end=7,
                change_kind=ChangeKind.MODIFIED,
            ),
        ),
    )
